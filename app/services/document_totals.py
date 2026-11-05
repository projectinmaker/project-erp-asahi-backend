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


def _distribute_dp_pelunasan(obj, strict: bool, dp_amt):
    """=== Update #042 — mode DP/Pelunasan sales_invoice (dp_pelunasan > 0) ===

    Baris sub_total = porsi dp_pelunasan proporsional thd gross baris
    (harga × qty); Σ baris sub_total = dp_pelunasan persis (selisih rounding
    dikoreksi di baris TERAKHIR). Diskon baris & diskon global diabaikan
    (total_diskon = 0); obj.sub_total = dp; PPN/PPh23 dihitung atas base =
    dp_pelunasan; grand_total = dp + PPN + biaya − PPh23.
    """
    rows = list(obj.details)
    gross_list = []
    total_gross = ZERO
    for row in rows:
        if strict and row.qty <= 0:
            raise ValueError('Kuantitas harus lebih dari nol')
        gross = money(amount(row.harga) * row.qty)
        gross_list.append(gross)
        total_gross += gross
    allocated = ZERO
    last = len(rows) - 1
    for idx, row in enumerate(rows):
        if idx == last:
            # Koreksi selisih rounding ditumpuk ke baris terakhir supaya
            # Σ baris sub_total = dp_pelunasan persis.
            share = dp_amt - allocated
        elif total_gross > 0:
            share = money(dp_amt * gross_list[idx] / total_gross)
        else:
            share = ZERO
        row.sub_total = share
        allocated += share
    obj.sub_total = dp_amt
    if hasattr(obj, 'total_diskon'):
        obj.total_diskon = ZERO
    fees = sum((money(amount(r.jumlah)) for r in getattr(obj, 'biaya_tambahan', []) or []), ZERO)
    if hasattr(obj, 'total_biaya_tambahan'):
        obj.total_biaya_tambahan = fees
    # Pajak atas base = dp_pelunasan (PPN/PPh23 tetap milik invoice).
    tax_pct = amount(obj.ppn) if hasattr(obj, 'ppn') else ZERO
    pph23_pct = amount(getattr(obj, 'pph23', 0))
    if tax_pct > 100 or pph23_pct > 100:
        raise ValueError('PPN dan PPh23 harus antara 0 dan 100 persen')
    ppn_applied = tax_pct if getattr(obj, 'ppn_applicable', True) else ZERO
    pph23_applied = pph23_pct if getattr(obj, 'pph23_applicable', False) else ZERO
    ppn_val = ZERO
    if hasattr(obj, 'total_ppn'):
        obj.total_ppn = money(dp_amt * ppn_applied / 100)
        ppn_val = obj.total_ppn
    if hasattr(obj, 'total_pph23'):
        obj.total_pph23 = money(dp_amt * pph23_applied / 100)
        obj.grand_total = dp_amt + ppn_val + fees - obj.total_pph23
    else:
        obj.grand_total = dp_amt + ppn_val + fees


def refresh_totals(obj):
    name = obj.__tablename__
    # Dokumen posting (strict: qty & detail wajib) + dokumen order
    # (toleran: draft order lama boleh punya qty 0; tanpa detail → skip recompute).
    strict_docs = ('sales_invoice', 'purchase_invoice', 'sales_retur', 'purchase_retur')
    order_docs = ('sales_order', 'purchase_order', 'penawaran')
    if name in strict_docs or name in order_docs:
        strict = name in strict_docs
        if not obj.details:
            if strict:
                raise ValueError('Minimal satu detail transaksi diperlukan')
            return  # Order tanpa detail: tidak ada total yang bisa direcompute
        # === Update #042 — mode DP/Pelunasan sales_invoice: nilai yang
        # ditagih = dp_pelunasan (header), didistribusikan proporsional ke
        # baris (cabang terpisah; dokumen lain tidak tersentuh). ===
        dp_header = getattr(obj, 'dp_pelunasan', None) if name == 'sales_invoice' else None
        if dp_header is not None and amount(dp_header) > 0:
            _distribute_dp_pelunasan(obj, strict, money(amount(dp_header)))
            return
        subtotal = ZERO
        discount = ZERO
        for row in obj.details:
            if strict and row.qty <= 0:
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
        # === Update #042 — PPN kondisional: sales_order & penawaran tidak
        # punya kolom ppn lagi (PPN hanya di invoice; purchase_order/invoice/
        # retur tetap). total_ppn hanya di-set bila kolomnya ada. ===
        tax_pct = amount(obj.ppn) if hasattr(obj, 'ppn') else ZERO
        # === Update ASAHI — PPh23/PPN opsional (PO & Sales Invoice) ===
        # Flag dipilih saat input: boleh keduanya, salah satu, atau tidak
        # sama sekali. PPN off → total_ppn dipaksa 0; PPh23 on → total_pph23
        # dihitung dan memotong grand_total.
        pph23_pct = amount(getattr(obj, 'pph23', 0))
        if global_pct > 100 or tax_pct > 100 or pph23_pct > 100:
            raise ValueError('Diskon global, PPN, dan PPh23 harus antara 0 dan 100 persen')
        ppn_applied = tax_pct if getattr(obj, 'ppn_applicable', True) else ZERO
        # AUD-12 (update #05): retur TIDAK menghitung ulang total_pph23 — nilai
        # diderivasi & di-cap dari invoice sumber (settlement_service.apply_retur_pph23);
        # refresh hanya memakai nilai tersimpan agar grand tetap NET konsisten.
        is_retur = name in ('sales_retur', 'purchase_retur')
        pph23_applied = ZERO if is_retur else (pph23_pct if getattr(obj, 'pph23_applicable', False) else ZERO)
        # Diskon global diterapkan setelah diskon baris (sama seperti invoice):
        # base = (gross - diskon baris) * (1 - diskon global %)
        discount += money((subtotal - discount) * global_pct / 100)
        base = subtotal - discount
        fees = sum((money(amount(r.jumlah)) for r in getattr(obj, 'biaya_tambahan', []) or []), ZERO)
        obj.sub_total = subtotal
        if hasattr(obj, 'total_diskon'):
            obj.total_diskon = discount
        if hasattr(obj, 'total_biaya_tambahan'):
            obj.total_biaya_tambahan = fees
        # === Update #042 — total_ppn hanya di-set bila dokumen punya kolom
        # (SO/penawaran tidak lagi ber-PPN; purchase/retur/invoice tetap). ===
        ppn_val = ZERO
        if hasattr(obj, 'total_ppn'):
            obj.total_ppn = money(base * ppn_applied / 100)
            ppn_val = obj.total_ppn
        if hasattr(obj, 'total_pph23'):
            # PPh23 dipotong dari grand_total (potongan pajak atas dokumen).
            if not is_retur:
                obj.total_pph23 = money(base * pph23_applied / 100)
            # Retur: total_pph23 tetap nilai tersimpan (cap AUD-12) — cukup
            # pastikan tidak negatif; grand dihitung ulang NET di bawah.
            if obj.total_pph23 < 0:
                obj.total_pph23 = ZERO
            obj.grand_total = base + ppn_val + fees - obj.total_pph23
        else:
            # === Update #042 — grand_total memakai komponen yang ada saja
            # (SO/penawaran: subtotal − diskon + biaya, TANPA PPN). ===
            obj.grand_total = base + ppn_val + fees
    elif name in ('pembayaran_kas', 'penerimaan_kas'):
        if not obj.rincian or any(amount(r.nilai) <= 0 for r in obj.rincian):
            raise ValueError('Rincian kas/bank wajib diisi dengan nilai positif')
        total = sum((money(amount(r.nilai)) for r in obj.rincian), ZERO)
        # Update cetak "Bayar Pemasok" — diskon pelunasan per faktur
        # mengurangi kas keluar (rincian tetap Dr hutang penuh).
        # Update #01 — PPh 23 pelunasan per faktur juga memotong kas
        # (pembayaran: Σ rincian − Σ diskon − Σ pph23; penerimaan:
        # Σ rincian − Σ pph23 — rincian tetap Dr/Kr akun kontrol penuh;
        # selisih diposting ke akun potongan hutang / PPh 23).
        total -= sum((money(amount(getattr(a, 'diskon', 0))) for a in (getattr(obj, 'alokasi', None) or [])), ZERO)
        total -= sum((money(amount(getattr(a, 'pph23', 0))) for a in (getattr(obj, 'alokasi', None) or [])), ZERO)
        obj.total_nilai = total
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
        if hasattr(obj, field) and getattr(obj, field) is not None:
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
