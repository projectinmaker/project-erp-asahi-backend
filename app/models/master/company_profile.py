from sqlalchemy import Column, String, Text

from app.database import BaseModel
from app.models.base import BaseMixin


class CompanyProfile(BaseModel, BaseMixin):
    """Profil perusahaan untuk header cetak/PDF (logo, nama, alamat, kontak).

    Tabel SATU BARIS — baris pertama dipakai seluruh modul cetak sebagai
    identitas pengganti nama sistem. Dikelola lewat menu
    Pengaturan → Profil Perusahaan (update ASAHI: cetak SO/PO & dokumen lain).

    Logo disimpan sebagai data URL (base64, "data:image/...") agar langsung
    bisa dirender <img> di template cetak tanpa file statis / storage eksternal.
    """

    __tablename__ = "company_profile"

    nama_perusahaan = Column(String(200), nullable=False)
    alamat = Column(Text, nullable=False)
    telepon = Column(String(50), nullable=True)
    email = Column(String(100), nullable=True)
    logo = Column(Text, nullable=True)
