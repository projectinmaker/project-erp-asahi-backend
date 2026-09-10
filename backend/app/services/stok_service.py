from decimal import Decimal
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session

from app.models.master.barang import Barang, MetodeValuasi
from app.models.transaksi.stok_mutasi import StokMutasi, TipeMutasiStok
from app.models.transaksi.jurnal import RefModule


from datetime import datetime, timezone
from app.services.accounting_control import atomic_accounting_write


@atomic_accounting_write
def update_stok_barang(db, barang_id, qty_change, mode="KURANGI", deskripsi="", ref_module=None,
                       ref_no=None, ref_id=None, gudang_id=None, harga_satuan=None, tanggal=None,
                       tanggal_kedaluwarsa=None, incoming_parts=None, exact_total=None):
    from app.services import warehouse_service as wh
    from app.models.transaksi.stock_balance import StockBalance
    from sqlalchemy import func
    barang = db.get(Barang, barang_id)
    if not barang:
        raise ValueError('Barang tidak ditemukan')
    if mode not in ('TAMBAH', 'KURANGI') or qty_change <= 0 or int(qty_change) != qty_change:
        raise ValueError('Mode/kuantitas stok tidak valid')
    incoming = mode == 'TAMBAH'
    old = barang.stok
    val = wh.value_move(db, barang, qty_change, incoming, gudang_id,
        harga_satuan if harga_satuan is not None else barang.harga_pokok,
        tanggal or datetime.now(timezone.utc), ref_module, ref_no, ref_id, tanggal_kedaluwarsa, incoming_parts)
    if exact_total is not None:
        if not incoming or wh.method(barang) != 'AVERAGE':
            raise ValueError('Nilai transfer hanya untuk penerimaan average')
        pos = db.query(StockBalance).filter_by(barang_id=barang.id, location_key=str(gudang_id) if gudang_id else 'UNASSIGNED').one()
        pos.nilai += exact_total - val['total_nilai']
        val['total_nilai'] = exact_total
        val['saldo_nilai_sesudah'] = pos.nilai
    barang.stok += qty_change if incoming else -qty_change
    db.flush()
    total = db.query(func.sum(StockBalance.nilai)).filter_by(barang_id=barang.id).scalar() or Decimal(0)
    if barang.stok:
        barang.harga_pokok = wh.money(total / barang.stok)
    mutasi = StokMutasi(barang_id=barang_id, tipe=_resolve_tipe_mutasi(ref_module, incoming), qty=qty_change,
        saldo_sebelum=old, saldo_sesudah=barang.stok, ref_module=ref_module, ref_no=ref_no, ref_id=ref_id,
        gudang_id=gudang_id, keterangan=deskripsi, harga_satuan=val['harga_satuan'], total_nilai=val['total_nilai'],
        saldo_nilai_sebelum=val['saldo_nilai_sebelum'], saldo_nilai_sesudah=val['saldo_nilai_sesudah'],
        warehouse_qty_before=val['warehouse_qty_before'], warehouse_qty_after=val['warehouse_qty_after'], global_value_after=total)
    mutasi.cost_parts = [{key: value.isoformat() if hasattr(value, 'isoformat') else value for key, value in part.items()} for part in val['parts']]
    db.add(mutasi)
    db.flush()
    return dict(val, barang=barang, mutasi=mutasi, saldo_nilai=val['saldo_nilai_sesudah'])


def update_stok_barang_legacy(
    db: Session,
    barang_id: UUID,
    qty_change: int,
    mode: str = "KURANGI",
    deskripsi: str = "",
    ref_module: Optional[RefModule] = None,
    ref_no: Optional[str] = None,
    ref_id: Optional[UUID] = None,
    gudang_id: Optional[UUID] = None,
) -> Barang:
    """Legacy wrapper — mengembalikan Barang object langsung.

    Dipertahankan untuk backward-compat dengan caller lama yang
    mengharapkan return type Barang.
    """
    result = update_stok_barang(
        db=db,
        barang_id=barang_id,
        qty_change=qty_change,
        mode=mode,
        deskripsi=deskripsi,
        ref_module=ref_module,
        ref_no=ref_no,
        ref_id=ref_id,
        gudang_id=gudang_id,
    )
    return result['barang']


def _resolve_tipe_mutasi(ref_module: Optional[RefModule], is_masuk: bool) -> TipeMutasiStok:
    """Resolve TipeMutasiStok berdasarkan RefModule dan arah (masuk/keluar)."""
    if ref_module is None:
        return TipeMutasiStok.MASUK if is_masuk else TipeMutasiStok.KELUAR

    mapping_masuk = {
        RefModule.PURCHASE_INVOICE: TipeMutasiStok.MASUK,
        RefModule.PURCHASE_RETUR: TipeMutasiStok.MASUK,
        RefModule.PENYESUAIAN_STOK: TipeMutasiStok.PENYESUAIAN_TAMBAH,
    }

    mapping_keluar = {
        RefModule.SALES_INVOICE: TipeMutasiStok.KELUAR,
        RefModule.SALES_RETUR: TipeMutasiStok.KELUAR,
        RefModule.PENYESUAIAN_STOK: TipeMutasiStok.PENYESUAIAN_KURANG,
    }

    if is_masuk:
        return mapping_masuk.get(ref_module, TipeMutasiStok.MASUK)
    else:
        return mapping_keluar.get(ref_module, TipeMutasiStok.KELUAR)


def hitung_nilai_stok(
    db: Session,
    barang_id: UUID,
) -> Decimal:
    """Hitung total nilai stok untuk satu barang.

    Untuk AVERAGE: harga_pokok * stok.
    Untuk FIFO/FEFO: jumlah dari semua active layers.
    """
    from app.models.transaksi.stock_balance import StockBalance
    from sqlalchemy import func
    if db.query(StockBalance).filter_by(barang_id=barang_id).first():
        return db.query(func.sum(StockBalance.nilai)).filter_by(barang_id=barang_id).scalar() or Decimal(0)
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    metode = MetodeValuasi(barang.metode_valuasi) if barang.metode_valuasi else MetodeValuasi.AVERAGE

    if metode == MetodeValuasi.AVERAGE:
        return Decimal(str(barang.harga_pokok or 0)) * (barang.stok or 0)
    else:
        from app.services.stok_kartu_service import _hitung_total_nilai_layers
        return _hitung_total_nilai_layers(db, barang_id)


def cek_stok_minimum(
    db: Session,
    barang_id: UUID,
) -> bool:
    """Cek apakah stok barang sudah di bawah stok minimum.
    Return True jika stok <= stok_minimum.
    """
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    return barang.stok <= barang.stok_minimum
