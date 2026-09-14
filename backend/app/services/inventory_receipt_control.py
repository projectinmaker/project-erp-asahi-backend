"""Checks for linked receipts and exact GRNI clearing; never rewrite posted history."""
from collections import defaultdict
from decimal import Decimal

from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun, SaldoNormal
from app.models.master.setting_akun import SettingAkun
from app.models.master.supplier import Supplier
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal
from app.services import organization_service
from app.services.reporting_ledger import local_datetime

KEY = 'PENERIMAAN_DALAM_PROSES'
CENT = Decimal('0.01')


def validate_grni_account(db, account_id):
    account = db.get(AkunPerkiraan, account_id)
    if (not account or account.header != HeaderCOA.KEWAJIBAN
            or account.tingkat != TingkatAkun.DETAIL or account.status != 'AKTIF'
            or account.saldo_normal != SaldoNormal.KREDIT
            or getattr(account, 'is_subledger', False)
            or db.query(SettingAkun).filter(SettingAkun.key == 'HUTANG_USAHA',
                                            SettingAkun.akun_perkiraan_id == account_id).first()
            or db.query(Supplier).filter(Supplier.akun_hutang_id == account_id).first()):
        raise ValueError('GRNI harus akun kewajiban DETAIL AKTIF, saldo normal KREDIT, terpisah dari akun utang supplier')
    return account_id


def configured_grni(db):
    # Read current database settings, not process-local caches from another session.
    setting = db.query(SettingAkun).filter_by(key=KEY).first()
    return validate_grni_account(db, setting.akun_perkiraan_id) if setting else None


def validate_link(db, invoice_id, supplier_id, tanggal):
    if invoice_id is None:
        return
    inv = db.get(PurchaseInvoice, invoice_id)
    if not inv:
        raise ValueError('Purchase invoice terkait tidak ditemukan')
    if inv.jurnal_umum_id or str(getattr(inv.status, 'value', inv.status)) in ('SELESAI', 'DIBATALKAN'):
        raise ValueError('Hubungkan penerimaan ke invoice yang belum diposting atau dibatalkan')
    if inv.supplier_id != supplier_id:
        raise ValueError('Supplier penerimaan harus sama dengan invoice')
    if local_datetime(tanggal).date() > local_datetime(inv.tanggal).date():
        raise ValueError('Tanggal penerimaan tidak boleh setelah tanggal invoice terkait')


def validate_execution(db, receipt, grni):
    validate_link(db, receipt.purchase_invoice_id, receipt.supplier_id, receipt.tanggal)
    if bool(grni) != bool(receipt.purchase_invoice_id):
        raise ValueError('Penerimaan GRNI memerlukan purchaseInvoiceId dan setting PENERIMAAN_DALAM_PROSES; lengkapi keduanya')
    if not grni:
        return
    if not receipt.purchase_order or receipt.purchase_order.supplier_id != receipt.supplier_id:
        raise ValueError('Supplier purchase order harus sama dengan penerimaan')
    if any(line.satuan_id != line.barang.satuan_id for line in receipt.details):
        raise ValueError('Satuan penerimaan GRNI harus sama dengan satuan master barang; konversi satuan belum didukung')
    inv = db.get(PurchaseInvoice, receipt.purchase_invoice_id)
    if inv.supplier.akun_hutang_id == grni:
        raise ValueError('Akun GRNI harus terpisah dari akun utang supplier')
    if organization_service.for_source(db, receipt.id) != organization_service.for_source(db, inv.id):
        raise ValueError('Dimensi organisasi penerimaan dan invoice harus sama')
    expected = defaultdict(int)
    received = defaultdict(int)
    for line in inv.details:
        expected[line.barang_id] += line.qty
    others = db.query(PenerimaanBarang).filter_by(purchase_invoice_id=inv.id).all()
    for row in others:
        if row.id == receipt.id or getattr(row.status, 'value', row.status) == 'SELESAI':
            for line in row.details:
                received[line.barang_id] += line.qty
    if any(qty <= 0 or qty > expected[item] for item, qty in received.items()):
        raise ValueError('Barang/kuantitas penerimaan melebihi atau tidak sesuai invoice terkait')


def clearing_entries(db, inv, basis):
    """Return original GRNI account totals; reject partial or differently priced invoices."""
    rows = [r for r in db.query(PenerimaanBarang).filter_by(purchase_invoice_id=inv.id).all()
            if getattr(r.status, 'value', r.status) != 'DIBATALKAN']
    if not rows:
        if configured_grni(db):
            raise ValueError('GRNI aktif: hubungkan dan selesaikan penerimaan sebelum posting invoice')
        return None
    if inv.total_diskon:
        raise ValueError('Clearing GRNI belum mendukung diskon global; gunakan harga penerimaan dan diskon per baris yang sesuai')
    expected_qty, actual_qty = defaultdict(int), defaultdict(int)
    expected_value, actual_value, accounts = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    for line in inv.details:
        expected_qty[line.barang_id] += line.qty
        expected_value[line.barang_id] += Decimal(line.sub_total)
    for row in rows:
        if row.supplier_id != inv.supplier_id or local_datetime(row.tanggal).date() > local_datetime(inv.tanggal).date():
            raise ValueError('Supplier/tanggal penerimaan tidak sesuai invoice')
        journal = db.get(JurnalUmum, row.jurnal_umum_id) if row.jurnal_umum_id else None
        if (getattr(row.status, 'value', row.status) != 'SELESAI' or not journal
                or journal.status != StatusJurnal.POSTED or journal.tipe_transaksi != 'PENERIMAAN_GRNI'
                or journal.ref_id != row.id
                or db.query(JurnalUmum).filter_by(reversal_of_id=journal.id).first()):
            raise ValueError('Semua penerimaan terkait harus selesai dan mempunyai jurnal GRNI POSTED yang belum direversal')
        scope = organization_service.for_source(db, inv.id)
        if any(getattr(journal, key) != value for key, value in scope.items()):
            raise ValueError('Dimensi organisasi jurnal penerimaan dan invoice harus sama')
        value = Decimal(0)
        for line in row.details:
            actual_qty[line.barang_id] += line.qty
            amount = (Decimal(line.harga_perolehan or 0) * line.qty).quantize(CENT)
            actual_value[line.barang_id] += amount
            value += amount
        credits = sum((line.kredit for line in journal.details), Decimal(0))
        if credits != value:
            raise ValueError('Nilai jurnal penerimaan tidak cocok dengan detail penerimaan')
        for line in journal.details:
            if line.kredit:
                if line.akun_perkiraan_id == inv.supplier.akun_hutang_id:
                    raise ValueError('Akun GRNI tidak boleh sama dengan utang supplier')
                accounts[line.akun_perkiraan_id] += line.kredit
    if (dict(expected_qty) != dict(actual_qty)
            or {k: v.quantize(CENT) for k, v in expected_value.items()} != dict(actual_value)
            or sum(accounts.values(), Decimal(0)) != basis):
        raise ValueError('Barang, kuantitas, dan nilai invoice harus sama dengan total penerimaan GRNI; selisih harga/penerimaan parsial belum didukung')
    return accounts


def require_no_completed_receipts(db, invoice):
    if any(getattr(r.status, 'value', r.status) == 'SELESAI' for r in
           db.query(PenerimaanBarang).filter_by(purchase_invoice_id=invoice.id).all()):
        raise ValueError('Invoice memiliki penerimaan selesai; gunakan alur retur, bukan pembatalan invoice')
