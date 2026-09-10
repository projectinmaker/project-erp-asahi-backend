"""AR/AP aging by party identity and invoice balance on a business date."""
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from app.models import SalesInvoice, PurchaseInvoice, PenerimaanKas, PembayaranKas, PurchaseRetur
from app.services import settlement_service as settlement
from app.services.organization_service import apply_scope
from app.models.transaksi.jurnal import JurnalUmum

def scoped_active(db, model, as_of):
    return apply_scope(db, settlement.active_documents(db, model, as_of), JurnalUmum)

BUCKETS = ('belum_jatuh_tempo', 'umur_1_30', 'umur_31_60', 'umur_61_90', 'umur_91_plus')


def aging(db, jenis, as_of):
    model = settlement.invoice_model(jenis)
    party = model.pelanggan if jenis == 'piutang' else model.supplier
    invoices = db.query(model).options(joinedload(party), joinedload(model.syarat_bayar)).filter(
        model.id.in_(scoped_active(db, model, as_of).scalar_subquery())
    ).order_by(model.tanggal, model.id).all()
    balances = settlement.balances(db, invoices, as_of)
    by_party = {}
    for obj in invoices:
        row = balances[obj.id]
        if row['sisa_tagihan'] <= 0:
            continue
        person = obj.pelanggan if jenis == 'piutang' else obj.supplier
        if person.id not in by_party:
            by_party[person.id] = {'pihak_id': person.id, 'nama': person.nama, 'total': Decimal('0'),
                                   **{name: Decimal('0') for name in BUCKETS}, 'rincian': []}
        entry = by_party[person.id]
        age = (settlement.local_day(as_of) - row['jatuh_tempo']).days
        bucket = BUCKETS[0 if age <= 0 else 1 if age <= 30 else 2 if age <= 60 else 3 if age <= 90 else 4]
        entry[bucket] += row['sisa_tagihan']
        entry['total'] += row['sisa_tagihan']
        entry['rincian'].append({'invoice_id': obj.id, 'no_dokumen': row['no_dokumen'],
            'tanggal': row['tanggal'].isoformat(), 'jatuh_tempo': row['jatuh_tempo'].isoformat(),
            'nilai': row['sisa_tagihan'], 'umur_hari': age, 'nilai_tagihan': row['nilai_tagihan'],
            'total_bayar': row['total_bayar'], 'total_retur': row['total_retur'], 'status_pembayaran': row['status_pembayaran']})
    items = sorted(by_party.values(), key=lambda r: (r['nama'], str(r['pihak_id'])))
    totals = {key: sum((r[key] for r in items), Decimal('0')) for key in ('total',) + BUCKETS}
    # Explicitly expose sources that cannot be assigned to an invoice automatically.
    payment = PenerimaanKas if jenis == 'piutang' else PembayaranKas
    unallocated_count, unallocated_total = db.query(func.count(payment.id), func.coalesce(func.sum(payment.total_nilai), 0)).filter(payment.id.in_(scoped_active(db, payment, as_of).scalar_subquery()), ~payment.alokasi.any()).one()
    unlinked_count, unlinked_total = (0, Decimal('0')) if jenis == 'piutang' else db.query(func.count(PurchaseRetur.id), func.coalesce(func.sum(PurchaseRetur.grand_total), 0)).filter(
        PurchaseRetur.id.in_(scoped_active(db, PurchaseRetur, as_of).scalar_subquery()),
        PurchaseRetur.purchase_invoice_id.is_(None)).one()
    return {'as_of_date': settlement.local_day(as_of).isoformat(), 'items': items, **totals,
            'dokumen_kas_tanpa_alokasi': unallocated_count,
            'nilai_kas_tanpa_alokasi': unallocated_total,
            'retur_tanpa_invoice': unlinked_count,
            'nilai_retur_tanpa_invoice': unlinked_total,
            'kelebihan_pelunasan': sum((r['kelebihan'] for r in balances.values()), Decimal('0'))}
