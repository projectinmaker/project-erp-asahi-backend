from sqlalchemy import Column, Date, String, Text

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
    # Update ASAHI #6: slogan/tagline — tampil khusus di header cetak
    # Invoice Penjualan (di bawah nama perusahaan). Kosong = tidak tampil.
    slogan = Column(Text, nullable=True)

    # === Update ASAHI #019 — Preferensi & Pengaturan (de-hardcode) ===
    # Kolom tambahan untuk halaman Pengaturan → Umum / Periode Akuntansi /
    # Pajak (semua nullable — migration cp0000000023, baris existing aman).
    # 1. Identitas umum (halaman "Umum", diperluas dari Profil Perusahaan).
    fax = Column(String(50), nullable=True)
    negara = Column(String(100), nullable=True)
    # Mata uang default pembukuan (ISO 4217, mis. IDR) — Preferensi → Akun Default.
    mata_uang = Column(String(8), nullable=True)
    # 2. Periode akuntansi (halaman "Periode Akuntansi").
    tanggal_mulai = Column(Date, nullable=True)
    tahun_fiskal = Column(String(20), nullable=True)
    # Filter periode default laporan (BULAN_INI/BULAN_LALU/TAHUN_INI/TAHUN_LALU/SEMUA).
    periode_default = Column(String(30), nullable=True)
    # 3. Data pajak (halaman "Pajak") — identitas faktur pajak / e-Faktur.
    no_seri_faktur_pajak = Column(String(50), nullable=True)
    npwp = Column(String(50), nullable=True)
    no_pengukuhan_pkp = Column(String(50), nullable=True)
    branch_code = Column(String(10), nullable=True)
    jenis_usaha = Column(String(200), nullable=True)
    klu_spt = Column(String(200), nullable=True)
