"""
stok_service.py

Service untuk mengelola pergerakan stok barang.
Digunakan oleh modul Penjualan, Pembelian, Persediaan, dan Penyesuaian Stok.
"""

from decimal import Decimal
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session

from app.models.master.barang import Barang


def update_stok_barang(
    db: Session,
    barang_id: UUID,
    qty_change: int,
    mode: str = "KURANGI",
    deskripsi: str = "",
) -> Barang:
    """
    Update stok barang (tambah/kurangi) dan commit ke database.

    Parameter:
        db: SQLAlchemy Session
        barang_id: UUID barang yang stoknya diupdate
        qty_change: Jumlah perubahan (harus > 0)
        mode: "TAMBAH" atau "KURANGI"
        deskripsi: Keterangan perubahan (untuk log)

    Return:
        Barang object yang sudah di-refresh

    Raise:
        ValueError: jika barang tidak ditemukan atau stok tidak mencukupi
    """
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    if qty_change <= 0:
        raise ValueError("qty_change harus lebih dari 0")

    old_stok = barang.stok

    if mode == "TAMBAH":
        barang.stok += qty_change
    elif mode == "KURANGI":
        if barang.stok < qty_change:
            raise ValueError(
                f"Stok tidak mencukupi: {barang.nama} (stok={barang.stok}, diminta={qty_change})"
            )
        barang.stok -= qty_change
    else:
        raise ValueError(f"Mode tidak dikenali: {mode}. Gunakan TAMBAH atau KURANGI.")

    logger.info(
        f"Stok updated: {barang.kode} ({barang.nama}) | "
        f"{old_stok} -> {barang.stok} | {mode} {qty_change} | {deskripsi}"
    )

    # Perubahan stok akan di-commit oleh caller (dalam transaksi yang sama)
    return barang


def hitung_nilai_stok(
    db: Session,
    barang_id: UUID,
) -> Decimal:
    """
    Hitung total nilai stok untuk satu barang = harga_pokok * stok.
    """
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    return Decimal(str(barang.harga_pokok or 0)) * (barang.stok or 0)


def cek_stok_minimum(
    db: Session,
    barang_id: UUID,
) -> bool:
    """
    Cek apakah stok barang sudah di bawah stok minimum.
    Return True jika stok <= stok_minimum.
    """
    barang = db.query(Barang).filter(Barang.id == barang_id).first()
    if not barang:
        raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

    return barang.stok <= barang.stok_minimum
