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

    Update ASAHI #002 — PIC Gudang: nama & no. telepon penanggung jawab
    gudang; saat PO memilih gudang, PIC otomatis terisi di form (masih bisa
    disunting) dan disimpan sebagai snapshot di PO (``pic_gudang`` /
    ``pic_gudang_telepon``).
    """

    __tablename__ = "alamat_pengiriman"

    prefix = Column(String(200), nullable=False, default="PT ASAHI SUKSES INDUSTRI")
    nama = Column(String(200), nullable=False)
    is_aktif = Column(Boolean, nullable=False, default=True)

    # === Update ASAHI #002 — PIC Gudang (opsional) ===
    pic_nama = Column(String(100), nullable=True)
    pic_telepon = Column(String(50), nullable=True)
