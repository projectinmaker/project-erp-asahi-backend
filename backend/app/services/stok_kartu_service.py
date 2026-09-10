from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.master.barang import Barang, MetodeValuasi
from app.models.transaksi.stok_mutasi import StokMutasi, TipeMutasiStok
from app.models.transaksi.stok_kartu_layer import StokKartuLayer
from app.models.transaksi.jurnal import RefModule


# ==========================================
# 1. VALUASI ENGINE
# ==========================================
# Dipanggil oleh stok_service.update_stok_barang().
# PENTING: fungsi ini dipanggil SEBELUM barang.stok diubah,
# jadi barang.stok masih nilai LAMA (pre-transaction).

def proses_stok_masuk(*args, **kwargs):
    raise ValueError('Gunakan stok_service.update_stok_barang untuk valuasi atomik per gudang')


def proses_stok_keluar(*args, **kwargs):
    raise ValueError('Gunakan stok_service.update_stok_barang untuk valuasi atomik per gudang')


def _hitung_total_nilai_layers(
    db: Session,
    barang_id: UUID,
    gudang_id: Optional[UUID] = None,
) -> Decimal:
    """Hitung total nilai dari semua layer FIFO/FEFO yang masih ada sisa."""
    from sqlalchemy import func

    query = db.query(
        func.sum(StokKartuLayer.qty_sisa * StokKartuLayer.harga_satuan)
    ).filter(
        StokKartuLayer.barang_id == barang_id,
        StokKartuLayer.qty_sisa > 0,
    )

    if gudang_id:
        query = query.filter(StokKartuLayer.gudang_id == gudang_id)

    result = query.scalar()
    return Decimal(str(result or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


# ==========================================
# 2. QUERY: STOK KARTU (untuk endpoint GET)
# ==========================================

def get_stok_kartu(
    db: Session,
    barang_id: UUID,
    gudang_id: Optional[UUID] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[dict], int]:
    """Query kartu stok untuk satu barang.

    Return:
        (entries, total) — entries adalah list of dict
    """
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    query = db.query(StokMutasi).filter(StokMutasi.barang_id == barang_id)

    if gudang_id:
        query = query.filter(StokMutasi.gudang_id == gudang_id)
    if date_from:
        query = query.filter(StokMutasi.created_at >= date_from)
    if date_to:
        query = query.filter(StokMutasi.created_at <= date_to)

    total = query.count()

    mutasi_list = (
        query
        .options(joinedload(StokMutasi.gudang))
        .order_by(StokMutasi.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    entries = []
    for m in mutasi_list:
        is_masuk = m.tipe in (
            TipeMutasiStok.MASUK,
            TipeMutasiStok.PENYESUAIAN_TAMBAH,
            TipeMutasiStok.PEMINDAHAN_MASUK,
        )

        harga = m.harga_satuan or Decimal('0')
        total_val = m.total_nilai or (m.qty * harga)

        entry = {
            'id': m.id,
            'tanggal': m.created_at,
            'tipe': m.tipe.value if m.tipe else '',
            'ref_module': m.ref_module.value if m.ref_module else None,
            'ref_no': m.ref_no,
            'keterangan': m.keterangan,
            'masuk_qty': m.qty if is_masuk else 0,
            'masuk_harga': harga if is_masuk else Decimal('0'),
            'masuk_total': total_val if is_masuk else Decimal('0'),
            'keluar_qty': m.qty if not is_masuk else 0,
            'keluar_harga': harga if not is_masuk else Decimal('0'),
            'keluar_total': total_val if not is_masuk else Decimal('0'),
            'saldo_qty': (m.warehouse_qty_after if gudang_id and m.warehouse_qty_after is not None else m.saldo_sesudah) or 0,
            'saldo_harga': (
                (m.saldo_nilai_sesudah / (m.warehouse_qty_after if m.warehouse_qty_after is not None else m.saldo_sesudah)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if (m.saldo_nilai_sesudah and (m.warehouse_qty_after if m.warehouse_qty_after is not None else m.saldo_sesudah))
                else Decimal(str(barang.harga_pokok or 0))
            ),
            'saldo_total': m.saldo_nilai_sesudah or Decimal('0'),
            'gudang': {
                'id': m.gudang.id,
                'kode': m.gudang.kode,
                'nama': m.gudang.nama,
            } if m.gudang else None,
        }
        entries.append(entry)

        # Never mix a warehouse value with a global quantity.
        if gudang_id and m.warehouse_qty_after is None:
            entry['saldo_tersedia'] = False
            entry['saldo_qty'] = 0
            entry['saldo_total'] = Decimal(0)
            entry['saldo_harga'] = Decimal(0)
        else:
            entry['saldo_total'] = (m.saldo_nilai_sesudah if gudang_id else
                m.global_value_after if m.global_value_after is not None else m.saldo_nilai_sesudah) or Decimal(0)
            entry['saldo_harga'] = (entry['saldo_total'] / entry['saldo_qty']).quantize(Decimal('0.01')) if entry['saldo_qty'] else Decimal(0)

    return entries, total


# ==========================================
# 3. SUMMARY: Ringkasan posisi stok saat ini
# ==========================================

def get_stok_kartu_summary(
    db: Session,
    barang_id: UUID,
    gudang_id: Optional[UUID] = None,
) -> dict:
    """Ringkasan posisi stok + nilai untuk satu barang."""
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    metode = MetodeValuasi(barang.metode_valuasi) if barang.metode_valuasi else MetodeValuasi.AVERAGE

    if metode == MetodeValuasi.AVERAGE:
        total_nilai = (Decimal(str(barang.harga_pokok or 0)) * (barang.stok or 0)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        layers = []
    else:
        layer_query = db.query(StokKartuLayer).filter(
            StokKartuLayer.barang_id == barang_id,
            StokKartuLayer.qty_sisa > 0,
        )
        if gudang_id:
            layer_query = layer_query.filter(StokKartuLayer.gudang_id == gudang_id)

        active_layers = layer_query.order_by(StokKartuLayer.tanggal_masuk.asc()).all()

        total_nilai = Decimal('0')
        layers = []
        for layer in active_layers:
            layer_val = (layer.qty_sisa * layer.harga_satuan).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            total_nilai += layer_val
            layers.append({
                'id': layer.id,
                'harga_satuan': layer.harga_satuan,
                'qty_sisa': layer.qty_sisa,
                'total_nilai': layer_val,
                'tanggal_masuk': layer.tanggal_masuk,
                'tanggal_kedaluwarsa': layer.tanggal_kedaluwarsa,
                'ref_no': layer.ref_no,
            })

    from app.models.transaksi.stock_balance import StockBalance
    balances = db.query(StockBalance).filter_by(barang_id=barang.id)
    if gudang_id:
        balances = balances.filter_by(gudang_id=gudang_id)
    positions = balances.all()
    qty = sum(p.qty for p in positions)
    total_nilai = sum((p.nilai for p in positions), Decimal(0))
    if not gudang_id and not positions:
        if metode == MetodeValuasi.AVERAGE:
            qty = barang.stok or 0
            total_nilai = Decimal(str(barang.harga_pokok or 0)) * qty
        else:
            qty = sum(layer['qty_sisa'] for layer in layers)
            total_nilai = sum((layer['total_nilai'] for layer in layers), Decimal(0))
    return {
        'barang_id': barang.id,
        'barang_kode': barang.kode,
        'barang_nama': barang.nama,
        'metode_valuasi': metode.value,
        'stok_qty': qty,
        'harga_pokok': (total_nilai / qty).quantize(Decimal('0.01')) if qty else Decimal(0),
        'total_nilai': total_nilai,
        'layers': layers,
    }


# ==========================================
# 4. REKALKULASI ULANG (repair function)
# ==========================================

def rekalkulasi_stok_kartu(db, barang_id):
    raise ValueError('Rekalkulasi histori dinonaktifkan. Gunakan rekonsiliasi dan penyesuaian yang disetujui.')


VALUASI_OPTIONS = [
    {'value': 'AVERAGE', 'label': 'Rata-rata Bergerak (Moving Average)'},
    {'value': 'FIFO', 'label': 'FIFO (First In First Out)'},
    {'value': 'FEFO', 'label': 'FEFO (First Expired First Out)'},
]
