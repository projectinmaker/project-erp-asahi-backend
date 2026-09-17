"""Strict warehouse valuation. Never borrow layers/quantity from another location."""
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from app.models.transaksi.stock_balance import StockBalance
from app.models.transaksi.stok_kartu_layer import StokKartuLayer
from app.models.master.gudang import Gudang


def money(value):
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def method(barang):
    return getattr(barang.metode_valuasi, 'value', barang.metode_valuasi) or 'AVERAGE'


def ensure_balances(db, barang):
    """Lazy equivalent of migration for new imports/tests; no guessed warehouse."""
    db.flush()
    if db.query(StockBalance).filter_by(barang_id=barang.id).first():
        return
    if method(barang) == 'AVERAGE':
        db.add(StockBalance(barang_id=barang.id, location_key='UNASSIGNED',
                            qty=barang.stok or 0, nilai=money((barang.stok or 0) * (barang.harga_pokok or 0))))
    else:
        rows = db.query(StokKartuLayer).filter_by(barang_id=barang.id).filter(StokKartuLayer.qty_sisa > 0).all()
        locations = {}
        for layer in rows:
            qty, value = locations.get(layer.gudang_id, (0, Decimal(0)))
            locations[layer.gudang_id] = (qty + layer.qty_sisa, value + layer.qty_sisa * layer.harga_satuan)
        if not locations:
            locations[None] = (0, Decimal(0))
        for warehouse, (qty, value) in locations.items():
            db.add(StockBalance(barang_id=barang.id, gudang_id=warehouse, location_key=str(warehouse) if warehouse else 'UNASSIGNED', qty=qty, nilai=money(value)))
    db.flush()


def balance(db, barang, gudang_id):
    ensure_balances(db, barang)
    if gudang_id:
        warehouse = db.get(Gudang, gudang_id)
        if not warehouse or getattr(warehouse.status, 'value', warehouse.status) != 'AKTIF':
            raise ValueError('Gudang tidak ditemukan atau tidak aktif')
    key = str(gudang_id) if gudang_id else 'UNASSIGNED'
    item = db.query(StockBalance).filter_by(barang_id=barang.id, location_key=key).first()
    if item is None:
        item = StockBalance(barang_id=barang.id, gudang_id=gudang_id, location_key=key, qty=0, nilai=Decimal(0))
        db.add(item)
        db.flush()
    total = db.query(func.sum(StockBalance.qty)).filter_by(barang_id=barang.id).scalar() or 0
    if total != barang.stok:
        raise ValueError('Saldo gudang/layer berbeda dengan stok master; rekonsiliasi data lama terlebih dahulu')
    return item


def value_move(db, barang, qty, masuk, gudang_id, harga, tanggal, ref_module=None, ref_no=None, ref_id=None, expiry=None, incoming_parts=None):
    pos = balance(db, barang, gudang_id)
    before_qty, before = pos.qty, pos.nilai
    if not masuk and qty > pos.qty:
        raise ValueError('Stok gudang tidak mencukupi; saldo gudang lain tidak dapat dipakai')
    parts = []
    if masuk:
        if harga is None or Decimal(str(harga)) < 0:
            raise ValueError('Harga perolehan stok masuk wajib diisi dan tidak negatif')
        parts = incoming_parts or [{'qty': qty, 'harga': str(harga), 'expiry': expiry, 'tanggal': tanggal}]
        # Match Numeric(18,2) layer storage before multiplying quantity. Otherwise
        # a discounted PO price can create value that no stored layer contains.
        parts = [dict(p, harga=str(money(p['harga']))) for p in parts]
        total = sum((money(p['qty'] * Decimal(p['harga'])) for p in parts), Decimal(0))
        if method(barang) != 'AVERAGE':
            for part in parts:
                if method(barang) == 'FEFO' and not part.get('expiry'):
                    raise ValueError('Barang FEFO memerlukan tanggal kedaluwarsa')
                db.add(StokKartuLayer(barang_id=barang.id, gudang_id=gudang_id, qty_masuk=part['qty'], qty_sisa=part['qty'],
                    harga_satuan=part['harga'], tanggal_masuk=part.get('tanggal') or tanggal, tanggal_kedaluwarsa=part.get('expiry'),
                    ref_module=ref_module, ref_no=ref_no, ref_id=ref_id))
    elif method(barang) == 'AVERAGE':
        total = pos.nilai if qty == pos.qty else money(pos.nilai * qty / pos.qty)
    else:
        layers = db.query(StokKartuLayer).filter_by(barang_id=barang.id, gudang_id=gudang_id).filter(StokKartuLayer.qty_sisa > 0)
        if method(barang) == 'FEFO':
            layers = layers.order_by(StokKartuLayer.tanggal_kedaluwarsa.asc().nullslast())
        layers = layers.order_by(StokKartuLayer.tanggal_masuk, StokKartuLayer.created_at, StokKartuLayer.id).all()
        if sum(x.qty_sisa for x in layers) != pos.qty or money(sum((x.qty_sisa*x.harga_satuan for x in layers), Decimal(0))) != pos.nilai:
            raise ValueError('Layer valuasi tidak cocok dengan saldo gudang; transaksi dibatalkan')
        if method(barang) == 'FEFO' and any(x.tanggal_kedaluwarsa is None for x in layers):
            raise ValueError('Layer FEFO lama belum memiliki tanggal kedaluwarsa; rekonsiliasi terlebih dahulu')
        remaining, total = qty, Decimal(0)
        for layer in layers:
            take = min(remaining, layer.qty_sisa)
            if not take:
                break
            parts.append({'qty': take, 'harga': str(layer.harga_satuan), 'expiry': layer.tanggal_kedaluwarsa, 'tanggal': layer.tanggal_masuk})
            total += money(take * layer.harga_satuan)
            layer.qty_sisa -= take
            remaining -= take
    pos.qty += qty if masuk else -qty
    pos.nilai += total if masuk else -total
    db.flush()
    return {'harga_satuan': money(total / qty), 'total_nilai': total, 'saldo_nilai_sebelum': before,
            'saldo_nilai_sesudah': pos.nilai, 'warehouse_qty_before': before_qty, 'warehouse_qty_after': pos.qty,
            'parts': parts}


def reconcile(db, barang):
    """Read-only discrepancy report; no recosting of posted journals."""
    rows = db.query(StockBalance).filter_by(barang_id=barang.id).all()
    layers = db.query(StokKartuLayer).filter_by(barang_id=barang.id).filter(StokKartuLayer.qty_sisa > 0).all()
    return {'barangId': barang.id, 'stokMaster': barang.stok, 'stokGudang': sum(r.qty for r in rows),
            'selisihQty': barang.stok - sum(r.qty for r in rows), 'nilaiGudang': sum((r.nilai for r in rows), Decimal(0)),
            'locations': [{'gudangId': r.gudang_id, 'locationKey': r.location_key, 'qty': r.qty, 'nilai': r.nilai} for r in rows],
            'perluInisialisasi': not rows,
            'layerQty': sum(x.qty_sisa for x in layers) if method(barang) != 'AVERAGE' else None,
            'layerNilai': sum((x.qty_sisa*x.harga_satuan for x in layers), Decimal(0)) if method(barang) != 'AVERAGE' else None,
            'layerTanpaExpiry': sum(x.tanggal_kedaluwarsa is None for x in layers) if method(barang) == 'FEFO' else 0}


def reconcile_ledger(db):
    """Current operational value versus all posted GL entries; discrepancies stay visible."""
    from app.models import Barang, JurnalUmum
    from app.models.detail.jurnal_detail import JurnalDetail
    from app.services.persediaan_service import _get_akun_persediaan_id
    grouped, missing = {}, []
    for item in db.query(Barang).all():
        try:
            account = _get_akun_persediaan_id(db, item)
        except ValueError:
            missing.append(str(item.id))
            continue
        values = db.query(StockBalance).filter_by(barang_id=item.id).all()
        if values:
            value = sum((x.nilai for x in values), Decimal(0))
        elif method(item) == 'AVERAGE':
            value = money((item.stok or 0)*(item.harga_pokok or 0))
        else:
            value = db.query(func.sum(StokKartuLayer.qty_sisa*StokKartuLayer.harga_satuan)).filter_by(barang_id=item.id).scalar() or Decimal(0)
        grouped[account] = grouped.get(account, Decimal(0)) + value
    rows = []
    for account, stock in grouped.items():
        ledger = db.query(func.sum(JurnalDetail.debit-JurnalDetail.kredit)).join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id).filter(
            JurnalUmum.status == 'POSTED', JurnalDetail.akun_perkiraan_id == account).scalar() or Decimal(0)
        rows.append({'akunId': account, 'nilaiStok': stock, 'nilaiBukuBesar': ledger, 'selisih': stock-ledger})
    return {'basis': 'CURRENT_EXECUTED_STOCK_VS_ALL_POSTED_JOURNALS', 'data': rows, 'barangTanpaMapping': missing,
            'catatan': 'Selisih dapat berasal dari saldo awal, invoice/penerimaan berbeda waktu, retur, mapping berubah, dan jurnal manual. Tidak ada koreksi otomatis.'}
