from sqlalchemy import Column, String, Boolean

from app.database import BaseModel
from app.models.base import BaseMixin


class RekeningBank(BaseModel, BaseMixin):
    """Rekening bank perusahaan untuk cetak Invoice Penjualan — update ASAHI #6.

    Dikelola di Pengaturan → Profil Perusahaan. Saat mencetak Invoice
    Penjualan, setiap rekening AKTIF tampil di bawah Keterangan sebagai dua
    baris: nama bank lalu "Acc Nbr <nomor> (<mata uang>)".

    ``mata_uang`` menyimpan kode mata uang (IDR/USD/... , mengikuti master
    mata uang) supaya satu perusahaan bisa punya rekening beda valuta.
    """

    __tablename__ = "rekening_bank"

    nama_bank = Column(String(200), nullable=False)
    no_rekening = Column(String(100), nullable=False)
    mata_uang = Column(String(8), nullable=False, default="IDR")
    is_aktif = Column(Boolean, nullable=False, default=True)
