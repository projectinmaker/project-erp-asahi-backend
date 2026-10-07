from sqlalchemy import Column, String, Boolean

from app.database import BaseModel
from app.models.base import BaseMixin


class MataUang(BaseModel, BaseMixin):
    """Daftar mata uang untuk transaksi pesanan (SO/PO) — update ASAHI #3.

    Dikelola di Pengaturan → Profil Perusahaan. Dipakai dropdown Currency
    pada form Pesanan Penjualan / Pesanan Pembelian; kode tersimpan di
    kolom ``currency`` dokumen (ISO 4217, default 'IDR').

    ``is_aktif`` = False menyembunyikan dari dropdown tanpa menghapus —
    dokumen lama tetap menyimpan kode-nya (kolom string, bukan FK).
    """

    __tablename__ = "mata_uang"

    kode = Column(String(8), unique=True, nullable=False, index=True)
    nama = Column(String(100), nullable=False)
    is_aktif = Column(Boolean, nullable=False, default=True)
