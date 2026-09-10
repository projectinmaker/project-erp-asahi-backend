"""Recalculate draft amounts and validate references before financial posting."""
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import object_session

ZERO = Decimal('0')


def amount(value):
    result = Decimal(str(value or 0))
    if not result.is_finite() or result < 0:
        raise ValueError('Nilai transaksi harus angka valid dan tidak negatif')
    return result


def money(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def refresh_totals(obj):
    name = obj.__tablename__
    if name in ('sales_invoice', 'purchase_invoice', 'sales_retur', 'purchase_retur'):
        if not obj.details:
            raise ValueError('Minimal satu detail transaksi diperlukan')
        subtotal = ZERO
        discount = ZERO
        for row in obj.details:
            if row.qty <= 0:
                raise ValueError('Kuantitas harus lebih dari nol')
            gross = money(amount(row.harga) * row.qty)
            pct = amount(getattr(row, 'diskon', 0))
            if pct > 100:
                raise ValueError('Diskon harus antara 0 dan 100 persen')
            cut = money(gross * pct / 100)
            row.sub_total = gross - cut
            subtotal += gross
            discount += cut
        global_pct = amount(getattr(obj, 'diskon_global', 0))
        tax_pct = amount(obj.ppn)
        if global_pct > 100 or tax_pct > 100:
            raise ValueError('Diskon global dan PPN harus antara 0 dan 100 persen')
        discount += money((subtotal - discount) * global_pct / 100)
        base = subtotal - discount
        fees = sum((money(amount(r.jumlah)) for r in getattr(obj, 'biaya_tambahan', [])), ZERO)
        obj.sub_total = subtotal
        if hasattr(obj, 'total_diskon'):
            obj.total_diskon = discount
        if hasattr(obj, 'total_biaya_tambahan'):
            obj.total_biaya_tambahan = fees
        obj.total_ppn = money(base * tax_pct / 100)
        obj.grand_total = base + obj.total_ppn + fees
    elif name in ('pembayaran_kas', 'penerimaan_kas'):
        if not obj.rincian or any(amount(r.nilai) <= 0 for r in obj.rincian):
            raise ValueError('Rincian kas/bank wajib diisi dengan nilai positif')
        obj.total_nilai = sum((money(amount(r.nilai)) for r in obj.rincian), ZERO)
    elif name == 'transfer_bank':
        if obj.dari_kas_bank_id == obj.ke_kas_bank_id:
            raise ValueError('Kas/Bank asal dan tujuan tidak boleh sama')
        obj.nilai_transfer = money(amount(obj.nilai_transfer))
        obj.biaya_transfer = money(amount(obj.biaya_transfer))
        if obj.nilai_transfer <= 0:
            raise ValueError('Nilai transfer harus positif')


def validate_postable(db, obj):
    from app.models import Pelanggan, Supplier, KasBankAkun, SalesOrder, SalesInvoice, PurchaseOrder, JurnalUmum
    for field, model in (('pelanggan_id', Pelanggan), ('supplier_id', Supplier),
                         ('kas_bank_id', KasBankAkun), ('dari_kas_bank_id', KasBankAkun), ('ke_kas_bank_id', KasBankAkun)):
        if hasattr(obj, field):
            party = db.get(model, getattr(obj, field))
            if not party or party.status != 'AKTIF':
                raise ValueError(f'Master {field} tidak tersedia atau tidak aktif')
    if getattr(obj, 'grand_total', getattr(obj, 'total_nilai', getattr(obj, 'nilai_transfer', 0))) <= 0:
        raise ValueError('Total transaksi harus positif untuk posting')
    for field, model, party in (('sales_order_id', SalesOrder, 'pelanggan_id'),
                                ('sales_invoice_id', SalesInvoice, 'pelanggan_id'),
                                ('purchase_order_id', PurchaseOrder, 'supplier_id')):
        ref_id = getattr(obj, field, None)
        if not ref_id:
            continue
        source = db.get(model, ref_id)
        if not source or getattr(source.status, 'value', source.status) in ('BATAL', 'DIBATALKAN'):
            raise ValueError('Dokumen sumber tidak tersedia atau sudah dibatalkan')
        if getattr(source, party) != getattr(obj, party):
            raise ValueError('Pelanggan/supplier tidak sama dengan dokumen sumber')
        if model in (SalesOrder, PurchaseOrder) and source.jurnal_umum_id:
            if not db.query(JurnalUmum.id).filter_by(reversal_of_id=source.jurnal_umum_id).first():
                raise ValueError('Jurnal order lama harus direkonsiliasi sebelum posting')
        if model == SalesInvoice and not source.jurnal_umum_id:
            raise ValueError('Invoice sumber retur belum diposting')
