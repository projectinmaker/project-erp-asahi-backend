from sqlalchemy import Column, String, Boolean

from app.database import BaseModel
from app.models.base import BaseMixin

class AlamatPengiriman(BaseModel, BaseMixin):
    """Daftar alamat pengiriman (gudang tujuan) untuk Purchase Order — update ASAHI #3.

    Dikelola di Pengaturan → Profil Perusahaan. Saat input PO, user wajib
    memilih SATU alamat via checkbox. Tersimpan di PO sebagai snapshot teks
    (prefix + nama) sehingga cetakan dokumen lama tidak berubah walau master
    diedit/dihapus; kolom ``alamat_pengiriman_id`` hanya referensi (SET NULL
    bila master dihapus).
    """
    __tablename__ = "alamat_pengiriman"

    prefix = Column(String(200), nullable=False, default="PT ASAHI SUKSES INDUSTRI")
    nama = Column(String(200), nullable=False)
    is_aktif = Column(Boolean, nullable=False, default=True)