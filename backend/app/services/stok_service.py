from decimal import Decimal
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session

from app.models.master.barang import Barang, MetodeValuasi
from app.models.transaksi.stok_mutasi import StokMutasi, TipeMutasiStok
from app.models.transaksi.jurnal import RefModule


def update_stok_barang(
    db: Session,
    barang_id: UUID,
    qty_change: int,
    mode: str = "KURANGI",
    deskripsi: str = "",
    ref_module: Optional[RefModule] = None,
    ref_no: Optional[str] = None,
    ref_id: Optional[UUID] = None,
    gudang_id: Optional[UUID] = None,
    harga_satuan: Optional[Decimal] = None,
) -> dict:
    """Update stok barang (tambah/kurangi), catat mutasi + valuasi.

    Parameter:
        db: SQLAlchemy Session
        barang_id: UUID barang yang stoknya diupdate
        qty_change: Jumlah perubahan (harus > 0)
        mode: "TAMBAH" atau "KURANGI"
        deskripsi: Keterangan perubahan (untuk log)
        ref_module: Enum RefModule (opsional, untuk StokMutasi)
        ref_no: Nomor dokumen sumber (opsional)
        ref_id: UUID dokumen sumber (opsional)
        gudang_id: UUID gudang (opsional)
        harga_satuan: Harga satuan untuk valuasi (opsional, default: barang.harga_pokok)

    Return:
        dict {
            'barang': Barang,
            'mutasi': StokMutasi,
            'harga_satuan': Decimal,
            'total_nilai': Decimal,
            'saldo_nilai': Decimal,
        }

    Raise:
        ValueError: jika barang tidak ditemukan atau stok tidak mencukupi
    """
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    if qty_change <= 0:
        raise ValueError("qty_change harus lebih dari 0")

    is_masuk = (mode == "TAMBAH")
    old_stok = barang.stok

    # Validasi stok cukup untuk KELUAR
    if not is_masuk and barang.stok < qty_change:
        raise ValueError(
            f"Stok tidak mencukupi: {barang.nama} (stok={barang.stok}, diminta={qty_change})"
        )

    # ---- VALUASI (SEBELUM stok diubah) ----
    if harga_satuan is None:
        harga_satuan = Decimal(str(barang.harga_pokok or 0))
    else:
        harga_satuan = Decimal(str(harga_satuan))

    try:
        from app.services.stok_kartu_service import proses_stok_masuk, proses_stok_keluar

        if is_masuk:
            val_result = proses_stok_masuk(
                db=db,
                barang=barang,
                qty=qty_change,
                harga_satuan=harga_satuan,
                gudang_id=gudang_id,
                ref_module=ref_module,
                ref_no=ref_no,
                ref_id=ref_id,
            )
        else:
            val_result = proses_stok_keluar(
                db=db,
                barang=barang,
                qty=qty_change,
                gudang_id=gudang_id,
            )
            # Untuk KELUAR, gunakan harga dari valuasi engine
            harga_satuan = val_result.get('harga_satuan', harga_satuan)

        total_nilai = val_result.get('total_nilai', qty_change * harga_satuan)
        saldo_nilai_sebelum = val_result.get('saldo_nilai_sebelum', Decimal('0'))
        saldo_nilai_sesudah = val_result.get('saldo_nilai_sesudah', Decimal('0'))

    except Exception as e:
        # Non-fatal: fallback ke perhitungan sederhana
        logger.warning(f"Valuasi gagal (fallback): {e}")
        total_nilai = (qty_change * harga_satuan).quantize(Decimal('0.01'))
        saldo_nilai_sebelum = Decimal(str(barang.harga_pokok or 0)) * old_stok
        if is_masuk:
            saldo_nilai_sesudah = saldo_nilai_sebelum + total_nilai
        else:
            saldo_nilai_sesudah = max(Decimal('0'), saldo_nilai_sebelum - total_nilai)

    # ---- UBAH STOK (SETELAH valuasi) ----
    if is_masuk:
        barang.stok += qty_change
        tipe_mutasi = _resolve_tipe_mutasi(ref_module, is_masuk=True)
    else:
        barang.stok -= qty_change
        tipe_mutasi = _resolve_tipe_mutasi(ref_module, is_masuk=False)

    # ---- CATAT STOK MUTASI ----
    mutasi = StokMutasi(
        barang_id=barang_id,
        tipe=tipe_mutasi,
        qty=qty_change,
        saldo_sebelum=old_stok,
        saldo_sesudah=barang.stok,
        ref_module=ref_module,
        ref_no=ref_no,
        ref_id=ref_id,
        gudang_id=gudang_id,
        keterangan=deskripsi,
        # Valuasi columns
        harga_satuan=harga_satuan,
        total_nilai=total_nilai,
        saldo_nilai_sebelum=saldo_nilai_sebelum,
        saldo_nilai_sesudah=saldo_nilai_sesudah,
    )
    db.add(mutasi)

    logger.info(
        f"Stok updated: {barang.kode} ({barang.nama}) | "
        f"{old_stok} -> {barang.stok} | {mode} {qty_change} @ {harga_satuan} | {deskripsi}"
    )

    return {
        'barang': barang,
        'mutasi': mutasi,
        'harga_satuan': harga_satuan,
        'total_nilai': total_nilai,
        'saldo_nilai': saldo_nilai_sesudah,
    }


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
