from sqlalchemy import Column, Integer, Numeric, String

from app.database import BaseModel
from app.models.base import BaseMixin


class PenawaranKatalogItem(BaseModel, BaseMixin):
    """Katalog item penawaran — Update #031.

    Item BARIS BEBAS yang diketik manual di form Penawaran (bukan dipilih dari
    master barang). Sesuai arahan user: item bebas TIDAK masuk master data —
    tersimpan di tabel katalog khusus penawaran supaya item yang berulang bisa
    dipilih lagi dengan cepat saat membuat penawaran berikutnya (harga & satuan
    terakhir ikut terisi).

    Baris dibuat/diperbarui otomatis saat penawaran DISIMPAN (upsert by nama
    setelah strip). Saat penawaran dikonversi menjadi Sales Order, item bebas
    BARU saat itu dibuatkan di master barang (stock_item=False) — sebelum itu
    katalog tetap terpisah dari master.
    """

    __tablename__ = "penawaran_katalog_item"

    # Nama item persis seperti diketik user (sudah di-strip; dedup app-level
    # case-insensitive di service — satu nama satu baris).
    nama = Column(String(200), nullable=False, index=True)
    # Satuan bebas (teks, mis. "lot", "unit", "pcs") — terakhir dipakai.
    satuan_nama = Column(String(50), nullable=True)
    # Harga satuan terakhir dipakai di penawaran.
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    # Berapa kali item ini dipakai di baris penawaran (termasuk update).
    dipakai_count = Column(Integer, default=1, nullable=False)
    keterangan = Column(String(255), nullable=True)
