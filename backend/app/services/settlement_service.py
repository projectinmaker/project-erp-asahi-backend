"""Invoice settlements use posted cash documents; allocations never create a second journal."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from app.models import SalesInvoice, PurchaseInvoice, SalesRetur, PurchaseRetur, PenerimaanKas, PembayaranKas, Pelanggan, Supplier, JurnalUmum
from app.models.transaksi.payment_allocation import PaymentAllocation
from app.models.detail.jurnal_detail import JurnalDetail
from app.services.accounting_control import atomic_accounting_write, require_unposted

ZERO = Decimal('0.00')
JAKARTA = timezone(timedelta(hours=7))


def local_day(value):
    if isinstance(value, datetime):
        return (value.astimezone(JAKARTA) if value.tzinfo else value).date()
    return value


def today():
    return datetime.now(JAKARTA).date()


def cutoff(as_of):
    return datetime.combine(local_day(as_of), time.max, tzinfo=JAKARTA)


def invoice_model(jenis):
    if jenis not in ('piutang', 'hutang'):
        raise ValueError('Jenis harus piutang atau hutang')
    return SalesInvoice if jenis == 'piutang' else PurchaseInvoice


def active_documents(db, model, as_of=None):
    """Ledger effective dates, including reversals; historical status changes alone are not accounting events."""
    reversal = aliased(JurnalUmum)
    reversed_query = select(reversal.id).where(reversal.reversal_of_id == JurnalUmum.id, reversal.status == 'POSTED')
    query = db.query(model.id).join(JurnalUmum, model.jurnal_umum_id == JurnalUmum.id).filter(JurnalUmum.status == 'POSTED')
    if as_of is not None:
        end = cutoff(as_of)
        query = query.filter(model.tanggal <= end, JurnalUmum.tanggal <= end)
        reversed_query = reversed_query.where(reversal.tanggal <= end)
    return query.filter(~reversed_query.exists())


def due_date(obj):
    if obj.tanggal_jatuh_tempo:
        return obj.tanggal_jatuh_tempo
    term = getattr(obj, 'syarat_bayar', None)
    return local_day(obj.tanggal) + timedelta(days=max(0, term.hari or 0) if term else 0)


def balances(db, invoices, as_of=None):
    if not invoices:
        return {}
    model = type(invoices[0])
    ar = model is SalesInvoice
    ids = [obj.id for obj in invoices]
    payment_model = PenerimaanKas if ar else PembayaranKas
    return_model = SalesRetur if ar else PurchaseRetur
    invoice_fk = PaymentAllocation.sales_invoice_id if ar else PaymentAllocation.purchase_invoice_id
    payment_fk = PaymentAllocation.penerimaan_id if ar else PaymentAllocation.pembayaran_id
    return_fk = return_model.sales_invoice_id if ar else return_model.purchase_invoice_id
    paid = dict(db.query(invoice_fk, func.sum(PaymentAllocation.nilai)).filter(
        invoice_fk.in_(ids), payment_fk.in_(active_documents(db, payment_model, as_of).scalar_subquery())
    ).group_by(invoice_fk).all())
    credit = dict(db.query(return_fk, func.sum(return_model.grand_total)).filter(
        return_fk.in_(ids), return_model.id.in_(active_documents(db, return_model, as_of).scalar_subquery())
    ).group_by(return_fk).all())
    active = {row[0] for row in active_documents(db, model, as_of).filter(model.id.in_(ids)).all()}
    result = {}
    for obj in invoices:
        bayar, retur = Decimal(paid.get(obj.id, 0)), Decimal(credit.get(obj.id, 0))
        saldo = obj.grand_total - bayar - retur
        is_active = obj.id in active
        if not is_active:
            state = 'DIBATALKAN' if getattr(obj.status, 'value', obj.status) in ('BATAL', 'DIBATALKAN') or obj.jurnal_umum_id else 'BELUM_DIPOSTING'
            if as_of is not None and local_day(obj.tanggal) > local_day(as_of):
                state = 'BELUM_EFEKTIF'
        elif saldo < 0:
            state = 'LEBIH_BAYAR'
        elif saldo == 0:
            state = 'LUNAS'
        elif bayar + retur > 0:
            state = 'PARSIAL'
        else:
            state = 'BELUM_DIBAYAR'
        result[obj.id] = {'invoice_id': obj.id, 'no_dokumen': obj.no_invoice if ar else obj.no_form,
            'pihak_id': obj.pelanggan_id if ar else obj.supplier_id,
            'tanggal': local_day(obj.tanggal), 'jatuh_tempo': due_date(obj),
            'nilai_tagihan': obj.grand_total, 'total_bayar': bayar, 'total_retur': retur,
            'sisa_tagihan': max(ZERO, saldo) if is_active else ZERO,
            'kelebihan': max(ZERO, -saldo) if is_active else ZERO,
            'status_pembayaran': state, 'aktif': is_active}
    return result


def control_account(db, invoice):
    """Prefer the account frozen at invoice posting; never silently follow a changed master COA."""
    if invoice.akun_kontrol_id:
        return invoice.akun_kontrol_id
    # Legacy invoices: only infer when the original journal has one unambiguous control line.
    ar = isinstance(invoice, SalesInvoice)
    value = JurnalDetail.debit if ar else JurnalDetail.kredit
    opposite = JurnalDetail.kredit if ar else JurnalDetail.debit
    rows = db.query(JurnalDetail.akun_perkiraan_id).filter(
        JurnalDetail.jurnal_umum_id == invoice.jurnal_umum_id,
        value == invoice.grand_total, opposite == 0).distinct().all()
    if len(rows) != 1:
        raise ValueError('Akun piutang/hutang invoice lama tidak dapat ditentukan; rekonsiliasi jurnal sumber diperlukan')
    return rows[0][0]


def positive_money(value):
    value = Decimal(str(value))
    if not value.is_finite() or value <= 0 or value != value.quantize(Decimal('.01')) or value >= Decimal('10000000000000000'):
        raise ValueError('Nilai alokasi harus positif, maksimal 2 desimal dan dalam batas nominal')
    return value


def prepare(db, jenis, pihak_id, tanggal, allocation_data):
    model = invoice_model(jenis)
    party = db.get(Pelanggan if jenis == 'piutang' else Supplier, pihak_id)
    if party is None or party.status != 'AKTIF':
        raise ValueError('Pelanggan/supplier tidak tersedia atau tidak aktif')
    if not allocation_data or len(allocation_data) > 100:
        raise ValueError('Isi 1 sampai 100 alokasi invoice')
    ids = [r['invoice_id'] for r in allocation_data]
    if len(ids) != len(set(ids)):
        raise ValueError('Invoice tidak boleh berulang dalam satu pembayaran')
    invoices = db.query(model).filter(model.id.in_(ids)).order_by(model.id).populate_existing().with_for_update().all()
    by_id = {obj.id: obj for obj in invoices}
    if len(invoices) != len(ids):
        raise ValueError('Invoice alokasi tidak ditemukan')
    current = balances(db, invoices)
    grouped = defaultdict(lambda: ZERO)
    rows = []
    for data in allocation_data:
        obj = by_id[data['invoice_id']]
        party_id = obj.pelanggan_id if jenis == 'piutang' else obj.supplier_id
        if party_id != pihak_id:
            raise ValueError('Semua invoice harus milik pelanggan/supplier yang dipilih')
        if not current[obj.id]['aktif']:
            raise ValueError('Alokasi hanya boleh untuk invoice POSTED yang belum dibatalkan')
        if getattr(obj, 'mata_uang', 'IDR') != 'IDR':
            raise ValueError('Alokasi saat ini hanya mendukung invoice IDR')
        if local_day(tanggal) < local_day(obj.tanggal):
            raise ValueError('Tanggal pembayaran tidak boleh sebelum tanggal invoice')
        nilai = positive_money(data['nilai'])
        if nilai > current[obj.id]['sisa_tagihan']:
            raise ValueError(f'Alokasi melebihi sisa tagihan {current[obj.id]["no_dokumen"]}')
        account_id = control_account(db, obj)
        grouped[account_id] += nilai
        rows.append({'invoice_id': obj.id, 'nilai': nilai, 'akun_perkiraan_id': account_id})
    return rows, [{'akun_perkiraan_id': account, 'nilai': amount} for account, amount in grouped.items()]


def attach(payment, jenis, rows):
    for row in rows:
        attrs = {'sales_invoice_id' if jenis == 'piutang' else 'purchase_invoice_id': row['invoice_id'],
                 'nilai': row['nilai'], 'akun_perkiraan_id': row['akun_perkiraan_id']}
        payment.alokasi.append(PaymentAllocation(**attrs))


@atomic_accounting_write
def create_settlement(db, jenis, pihak_id, tanggal, kas_bank_id, no_nukti, allocation_data, created_by, catatan=None):
    from app.services import kas_bank_service as cash
    rows, rincian = prepare(db, jenis, pihak_id, tanggal, allocation_data)
    create = cash.create_penerimaan if jenis == 'piutang' else cash.create_pembayaran
    payment = create(db, no_nukti, tanggal, kas_bank_id, rincian, catatan=catatan,
                     auto_post_jurnal=False, created_by=created_by)
    setattr(payment, 'pelanggan_id' if jenis == 'piutang' else 'supplier_id', pihak_id)
    attach(payment, jenis, rows)
    return payment


@atomic_accounting_write
def update_allocations(db, db_obj, jenis, pihak_id, allocation_data):
    require_unposted(db_obj)
    if type(db_obj) is not (PenerimaanKas if jenis == 'piutang' else PembayaranKas):
        raise ValueError('Jenis pembayaran tidak sesuai')
    rows, rincian = prepare(db, jenis, pihak_id, db_obj.tanggal, allocation_data)
    from app.models.detail.penerimaan_rincian import PenerimaanRincian
    from app.models.detail.pembayaran_rincian import PembayaranRincian
    detail_model = PenerimaanRincian if jenis == 'piutang' else PembayaranRincian
    db_obj.alokasi.clear()
    db_obj.rincian.clear()
    db.flush()  # Release old unique pairs before replacing allocations.
    attach(db_obj, jenis, rows)
    for data in rincian:
        db_obj.rincian.append(detail_model(**data))
    setattr(db_obj, 'pelanggan_id' if jenis == 'piutang' else 'supplier_id', pihak_id)
    db_obj.total_nilai = sum((r['nilai'] for r in rows), ZERO)
    return db_obj


def validate_payment(db, payment):
    if not payment.alokasi:
        return  # Legacy/general cash documents remain supported, without invoice settlement.
    jenis = 'piutang' if isinstance(payment, PenerimaanKas) else 'hutang'
    party = payment.pelanggan_id if jenis == 'piutang' else payment.supplier_id
    rows, expected = prepare(db, jenis, party, payment.tanggal,
                             [{'invoice_id': r.invoice_id, 'nilai': r.nilai} for r in payment.alokasi])
    actual = defaultdict(lambda: ZERO)
    for row in payment.rincian:
        actual[row.akun_perkiraan_id] += row.nilai
    if dict(actual) != {r['akun_perkiraan_id']: r['nilai'] for r in expected} or sum(actual.values(), ZERO) != payment.total_nilai:
        raise ValueError('Rincian kas/bank tidak sesuai alokasi invoice')
    original = {r.invoice_id: r.akun_perkiraan_id for r in payment.alokasi}
    if any(original[r['invoice_id']] != r['akun_perkiraan_id'] for r in rows):
        raise ValueError('Akun alokasi tidak sesuai jurnal invoice')


def validate_return(db, obj):
    model = SalesInvoice if isinstance(obj, SalesRetur) else PurchaseInvoice
    invoice_id = getattr(obj, 'sales_invoice_id', None) if model is SalesInvoice else obj.purchase_invoice_id
    if not invoice_id:
        return  # Historical unlinked purchase returns are disclosed separately in reports.
    invoice = db.get(model, invoice_id)
    if not invoice:
        raise ValueError('Invoice sumber retur tidak ditemukan')
    field = 'pelanggan_id' if model is SalesInvoice else 'supplier_id'
    if getattr(invoice, field) != getattr(obj, field):
        raise ValueError('Pelanggan/supplier retur berbeda dengan invoice')
    balance = balances(db, [invoice])[invoice.id]
    if not balance['aktif'] or obj.grand_total > balance['sisa_tagihan']:
        raise ValueError('Retur melebihi sisa tagihan atau invoice tidak aktif; proses refund diperlukan untuk tagihan yang telah lunas')
    if local_day(obj.tanggal) < local_day(invoice.tanggal):
        raise ValueError('Tanggal retur tidak boleh sebelum invoice')


def require_no_settlements(db, invoice):
    state = balances(db, [invoice])[invoice.id]
    if state['total_bayar'] > 0 or state['total_retur'] > 0:
        raise ValueError('Invoice masih memiliki pembayaran/retur aktif. Batalkan sumber pelunasannya lebih dahulu')


def payment_summary(payment):
    return {'id': payment.id, 'jenis': 'piutang' if isinstance(payment, PenerimaanKas) else 'hutang',
        'no_bukti': payment.no_bukti, 'tanggal': payment.tanggal, 'total_nilai': payment.total_nilai,
        'status': getattr(payment.status, 'value', payment.status), 'jurnal_umum_id': payment.jurnal_umum_id,
        'pihak_id': payment.pelanggan_id if isinstance(payment, PenerimaanKas) else payment.supplier_id,
        'alokasi': [{'id': r.id, 'invoice_id': r.invoice_id, 'nilai': r.nilai,
                     'akun_perkiraan_id': r.akun_perkiraan_id} for r in payment.alokasi]}
