"""Restore goods and COGS from the actual shipment, never today's master price."""
from datetime import date, datetime
from decimal import Decimal
from app.models import PengirimanBarang, SalesRetur, StokMutasi
from app.models.transaksi.jurnal import RefModule
from app.services.stok_service import update_stok_barang
from app.services.warehouse_service import money, method
from app.services.settlement_service import local_day
from app.services.posting_service import JurnalEntryItem, auto_posting_jurnal


def execute_sales_return(db, obj):
    source = db.get(PengirimanBarang, obj.pengiriman_id) if obj.pengiriman_id else None
    if not source or getattr(source.status, 'value', source.status) != 'SELESAI' or source.pelanggan_id != obj.pelanggan_id:
        raise ValueError('Pilih pengirimanId yang selesai dan milik pelanggan yang sama untuk mengembalikan stok/HPP')
    if obj.sales_invoice.sales_order_id and obj.sales_invoice.sales_order_id != source.sales_order_id:
        raise ValueError('Pengiriman berbeda dengan order invoice retur')
    if local_day(obj.tanggal) < local_day(source.tanggal):
        raise ValueError('Retur mendahului pengiriman')
    earlier = db.query(SalesRetur).filter_by(pengiriman_id=source.id, status='SELESAI').all()
    grouped = {}
    for detail in obj.details:
        grouped[detail.barang_id] = grouped.get(detail.barang_id, 0) + detail.qty
    entries = []
    for item_id, qty in grouped.items():
        moves = db.query(StokMutasi).filter_by(ref_id=source.id, barang_id=item_id).order_by(StokMutasi.created_at, StokMutasi.id).all()
        if not moves or any(not m.inventory_account_id or not m.expense_account_id for m in moves):
            raise ValueError('Pengiriman lama belum memiliki snapshot HPP; perlu rekonsiliasi sebelum retur stok')
        previous = sum(d.qty for r in earlier for d in r.details if d.barang_id == item_id)
        original_qty = sum(m.qty for m in moves)
        if qty <= 0 or previous + qty > original_qty:
            raise ValueError('Jumlah retur melebihi barang yang dikirim dan belum diretur')
        accounts = {(m.inventory_account_id, m.expense_account_id) for m in moves}
        if len(accounts) != 1:
            raise ValueError('Snapshot akun HPP pengiriman ambigu')
        inventory, expense = next(iter(accounts))
        item = moves[0].barang
        parts, skip, remaining = [], previous, qty
        for movement in moves:
            for part in movement.cost_parts or []:
                if skip >= part['qty']:
                    skip -= part['qty']
                    continue
                take = min(part['qty']-skip, remaining)
                skip = 0
                if take:
                    parts.append({'qty': take, 'harga': part['harga'],
                        'expiry': date.fromisoformat(part['expiry']) if part.get('expiry') else None,
                        'tanggal': datetime.fromisoformat(part['tanggal']) if part.get('tanggal') else None})
                    remaining -= take
                if not remaining:
                    break
            if not remaining:
                break
        original_value = sum((m.total_nilai for m in moves), Decimal(0))
        # Average returns use cumulative rounding so the final return restores exactly the original COGS.
        value = money(original_value*(previous+qty)/original_qty) - money(original_value*previous/original_qty)
        if method(item) != 'AVERAGE' and remaining:
            raise ValueError('Snapshot layer pengiriman tidak lengkap')
        movement = update_stok_barang(db, item_id, qty, 'TAMBAH', ref_module=RefModule.SALES_RETUR,
            ref_id=obj.id, ref_no=obj.no_retur, gudang_id=obj.gudang_id, harga_satuan=money(value/qty),
            incoming_parts=parts or None, exact_total=value if method(item) == 'AVERAGE' else None)
        total = movement['total_nilai']
        if total:
            entries += [JurnalEntryItem(inventory, debit=total), JurnalEntryItem(expense, kredit=total)]
    if entries:
        auto_posting_jurnal(db, RefModule.SALES_RETUR, obj.no_retur, entries, ref_id=obj.id,
            tanggal=obj.tanggal, created_by=obj.created_by, tipe_transaksi='RETUR_HPP')
