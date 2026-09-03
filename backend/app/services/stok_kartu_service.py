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

def proses_stok_masuk(
    db: Session,
    barang: Barang,
    qty: int,
    harga_satuan: Decimal,
    gudang_id: Optional[UUID] = None,
    tanggal: Optional[datetime] = None,
    ref_module: Optional[RefModule] = None,
    ref_no: Optional[str] = None,
    ref_id: Optional[UUID] = None,
) -> dict:
    """Proses barang MASUK: hitung valuasi, buat layer (FIFO/FEFO), update harga_pokok (AVERAGE).

    CATATAN: Dipanggil SEBELUM barang.stok ditambah, sehingga:
      - barang.stok = stok LAMA (belum termasuk qty ini)
      - barang.harga_pokok = harga LAMA (belum diupdate)

    Return:
        {
            'harga_satuan': Decimal,
            'total_nilai': Decimal,         # qty * harga_satuan
            'saldo_nilai_sebelum': Decimal, # total nilai stok sebelum masuk
            'saldo_nilai_sesudah': Decimal,  # total nilai stok setelah masuk
            'harga_pokok_baru': Decimal,     # unit cost terbaru (utk AVERAGE)
        }
    """
    metode = MetodeValuasi(barang.metode_valuasi) if barang.metode_valuasi else MetodeValuasi.AVERAGE
    total_nilai_masuk = (qty * harga_satuan).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    tanggal = tanggal or datetime.now(timezone.utc)
    old_stok = barang.stok or 0
    old_harga = Decimal(str(barang.harga_pokok or 0))
    saldo_nilai_sebelum = (old_harga * old_stok).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    if metode == MetodeValuasi.AVERAGE:
        # Moving Average: (nilai lama + nilai masuk) / (qty lama + qty masuk)
        new_stok = old_stok + qty
        if new_stok > 0:
            harga_pokok_baru = ((saldo_nilai_sebelum + total_nilai_masuk) / new_stok).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        else:
            harga_pokok_baru = harga_satuan

        saldo_nilai_sesudah = (saldo_nilai_sebelum + total_nilai_masuk).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        # Update harga_pokok pada model Barang (caller harus db.flush/commit)
        barang.harga_pokok = harga_pokok_baru

        return {
            'harga_satuan': harga_satuan,
            'total_nilai': total_nilai_masuk,
            'saldo_nilai_sebelum': saldo_nilai_sebelum,
            'saldo_nilai_sesudah': saldo_nilai_sesudah,
            'harga_pokok_baru': harga_pokok_baru,
        }

    else:
        # FIFO / FEFO: buat layer baru
        layer = StokKartuLayer(
            barang_id=barang.id,
            gudang_id=gudang_id,
            harga_satuan=harga_satuan,
            qty_masuk=qty,
            qty_sisa=qty,
            tanggal_masuk=tanggal,
            ref_module=ref_module,
            ref_no=ref_no,
            ref_id=ref_id,
        )
        db.add(layer)

        saldo_nilai_sesudah = (saldo_nilai_sebelum + total_nilai_masuk).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        return {
            'harga_satuan': harga_satuan,
            'total_nilai': total_nilai_masuk,
            'saldo_nilai_sebelum': saldo_nilai_sebelum,
            'saldo_nilai_sesudah': saldo_nilai_sesudah,
            'harga_pokok_baru': old_harga,  # tidak berubah untuk FIFO/FEFO
        }


def proses_stok_keluar(
    db: Session,
    barang: Barang,
    qty: int,
    gudang_id: Optional[UUID] = None,
) -> dict:
    """Proses barang KELUAR: hitung HPP berdasarkan metode valuasi.

    CATATAN: Dipanggil SEBELUM barang.stok dikurangi, sehingga:
      - barang.stok = stok LAMA (masih termasuk qty yang akan dikeluarkan)
      - barang.harga_pokok = harga saat ini

    Return:
        {
            'harga_satuan': Decimal,   # rata-rata harga satuan yang digunakan
            'total_nilai': Decimal,    # total HPP (qty * harga)
            'saldo_nilai_sebelum': Decimal,
            'saldo_nilai_sesudah': Decimal,
            'layers_consumed': list,
        }
    """
    metode = MetodeValuasi(barang.metode_valuasi) if barang.metode_valuasi else MetodeValuasi.AVERAGE
    old_stok = barang.stok or 0
    old_harga = Decimal(str(barang.harga_pokok or 0))
    saldo_nilai_sebelum = (old_harga * old_stok).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    if metode == MetodeValuasi.AVERAGE:
        # AVERAGE: gunakan harga_pokok saat ini, harga tidak berubah
        total_hpp = (qty * old_harga).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        new_stok = old_stok - qty
        saldo_nilai_sesudah = (old_harga * new_stok).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        return {
            'harga_satuan': old_harga,
            'total_nilai': total_hpp,
            'saldo_nilai_sebelum': saldo_nilai_sebelum,
            'saldo_nilai_sesudah': max(saldo_nilai_sesudah, Decimal('0')),
            'layers_consumed': [],
        }

    else:
        # FIFO / FEFO: konsumsi layer dari yang paling lama (FIFO)
        sisa_qty = qty
        total_hpp = Decimal('0')
        layers_consumed = []

        order_by = StokKartuLayer.tanggal_masuk.asc()

        layers = (
            db.query(StokKartuLayer)
            .filter(
                StokKartuLayer.barang_id == barang.id,
                StokKartuLayer.qty_sisa > 0,
            )
            .order_by(order_by)
            .all()
        )

        for layer in layers:
            if sisa_qty <= 0:
                break

            consume = min(sisa_qty, layer.qty_sisa)
            nilai_konsumsi = (consume * layer.harga_satuan).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            total_hpp += nilai_konsumsi
            layer.qty_sisa -= consume
            sisa_qty -= consume

            layers_consumed.append({
                'layer_id': str(layer.id),
                'harga_satuan': str(layer.harga_satuan),
                'qty_consumed': consume,
                'nilai': str(nilai_konsumsi),
            })

        if sisa_qty > 0:
            logger.warning(
                f"Stok layer tidak cukup untuk {barang.kode}: "
                f"kurang {sisa_qty} unit. Fallback ke harga_pokok."
            )
            fallback_nilai = (sisa_qty * old_harga).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            total_hpp += fallback_nilai

        saldo_nilai_sesudah = (saldo_nilai_sebelum - total_hpp).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        saldo_nilai_sesudah = max(saldo_nilai_sesudah, Decimal('0'))

        return {
            'harga_satuan': (total_hpp / qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if qty > 0 else Decimal('0'),
            'total_nilai': total_hpp,
            'saldo_nilai_sebelum': saldo_nilai_sebelum,
            'saldo_nilai_sesudah': saldo_nilai_sesudah,
            'layers_consumed': layers_consumed,
        }


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
            'saldo_qty': m.saldo_sesudah or 0,
            'saldo_harga': (
                (m.saldo_nilai_sesudah / m.saldo_sesudah).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if (m.saldo_nilai_sesudah and m.saldo_sesudah and m.saldo_sesudah > 0)
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
                'ref_no': layer.ref_no,
            })

    return {
        'barang_id': barang.id,
        'barang_kode': barang.kode,
        'barang_nama': barang.nama,
        'metode_valuasi': metode.value,
        'stok_qty': barang.stok or 0,
        'harga_pokok': Decimal(str(barang.harga_pokok or 0)),
        'total_nilai': total_nilai,
        'layers': layers,
    }


# ==========================================
# 4. REKALKULASI ULANG (repair function)
# ==========================================

def rekalkulasi_stok_kartu(
    db: Session,
    barang_id: UUID,
) -> dict:
    """Rekalkulasi ulang seluruh kartu stok & layer untuk satu barang.

    Proses:
    1. Hapus semua StokKartuLayer untuk barang ini
    2. Reset harga_pokok & stok ke 0
    3. Baca semua StokMutasi secara berurutan
    4. Untuk MASUK: proses_stok_masuk (buat layer baru, update average)
    5. Untuk KELUAR: proses_stok_keluar (konsumsi layer)
    6. Update kolom valuasi pada setiap StokMutasi
    7. Update saldo_sebelum/saldo_sesudah pada setiap StokMutasi

    Return:
        {'message': str, 'total_mutasi': int, 'layers_created': int}
    """
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    # 1. Hapus layer lama
    deleted = db.query(StokKartuLayer).filter(StokKartuLayer.barang_id == barang_id).delete()
    logger.info(f"Rekalkulasi {barang.kode}: dihapus {deleted} layer lama")

    # 2. Reset
    barang.harga_pokok = Decimal('0')
    barang.stok = 0
    db.flush()

    # 3. Baca semua StokMutasi berurutan
    mutasi_list = (
        db.query(StokMutasi)
        .filter(StokMutasi.barang_id == barang_id)
        .order_by(StokMutasi.created_at.asc(), StokMutasi.id.asc())
        .all()
    )

    layers_created = 0

    for m in mutasi_list:
        is_masuk = m.tipe in (
            TipeMutasiStok.MASUK,
            TipeMutasiStok.PENYESUAIAN_TAMBAH,
            TipeMutasiStok.PEMINDAHAN_MASUK,
        )

        # Tentukan harga_satuan: dari mutasi (jika ada), atau dari harga_pokok saat ini
        harga = m.harga_satuan if m.harga_satuan is not None else Decimal(str(barang.harga_pokok or 0))

        # Catat saldo qty sebelum
        old_stok = barang.stok

        # Proses valuasi (SEBELUM stok diubah)
        if is_masuk:
            if harga > 0:
                result = proses_stok_masuk(
                    db=db,
                    barang=barang,
                    qty=m.qty,
                    harga_satuan=harga,
                    gudang_id=m.gudang_id,
                    tanggal=m.created_at,
                    ref_module=m.ref_module,
                    ref_no=m.ref_no,
                    ref_id=m.ref_id,
                )
            else:
                result = {
                    'harga_satuan': Decimal('0'),
                    'total_nilai': Decimal('0'),
                    'saldo_nilai_sebelum': Decimal('0'),
                    'saldo_nilai_sesudah': Decimal('0'),
                    'harga_pokok_baru': Decimal('0'),
                }
        else:
            if barang.stok > 0:
                result = proses_stok_keluar(
                    db=db,
                    barang=barang,
                    qty=m.qty,
                    gudang_id=m.gudang_id,
                )
            else:
                result = {
                    'harga_satuan': Decimal('0'),
                    'total_nilai': Decimal('0'),
                    'saldo_nilai_sebelum': Decimal('0'),
                    'saldo_nilai_sesudah': Decimal('0'),
                    'layers_consumed': [],
                }

        # Sekarang ubah stok qty
        if is_masuk:
            barang.stok += m.qty
        else:
            barang.stok = max(0, barang.stok - m.qty)

        # Update StokMutasi dengan data valuasi
        m.harga_satuan = result.get('harga_satuan', harga)
        m.total_nilai = result.get('total_nilai', Decimal('0'))
        m.saldo_sebelum = old_stok
        m.saldo_sesudah = barang.stok
        m.saldo_nilai_sebelum = result.get('saldo_nilai_sebelum', Decimal('0'))
        m.saldo_nilai_sesudah = result.get('saldo_nilai_sesudah', Decimal('0'))

        db.flush()

        if is_masuk and result.get('total_nilai', Decimal('0')) > 0:
            layers_created += 1

    db.flush()
    logger.info(
        f"Rekalkulasi {barang.kode} selesai: "
        f"{len(mutasi_list)} mutasi, {layers_created} layer baru"
    )

    return {
        'message': f'Kartu stok {barang.kode} berhasil direkalkulasi',
        'total_mutasi': len(mutasi_list),
        'layers_created': layers_created,
    }


# ==========================================
# 5. OPTIONS: Metode Valuasi yang tersedia
# ==========================================

VALUASI_OPTIONS = [
    {'value': 'AVERAGE', 'label': 'Rata-rata Bergerak (Moving Average)'},
    {'value': 'FIFO', 'label': 'FIFO (First In First Out)'},
    {'value': 'FEFO', 'label': 'FEFO (First Expired First Out)'},
]