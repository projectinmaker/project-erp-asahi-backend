import enum
from sqlalchemy import Column, String, Text, Integer, Numeric, Date, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class MetodePenyusutan(str, enum.Enum):
    GARIS_LURUS = "GARIS_LURUS"
    SALDO_MENURUN = "SALDO_MENURUN"


class StatusAsetTetap(str, enum.Enum):
    AKTIF = "AKTIF"
    DIHAPUSKAN = "DIHAPUSKAN"
    DALAM_PERBAIKAN = "DALAM_PERBAIKAN"


class AcquisitionSourceType(str, enum.Enum):
    """Tipe sumber akuisisi aset tetap (Roadmap §24: "Acquisition source trace")."""
    MANUAL_JOURNAL = "MANUAL_JOURNAL"   # Aset dibeli & dicatat via jurnal manual
    PURCHASE_INVOICE = "PURCHASE_INVOICE"  # Aset dibeli via Purchase Invoice
    SALDO_AWAL = "SALDO_AWAL"            # Aset dari saldo awal (opening balance)
    DIRECT = "DIRECT"                     # Aset dicatat langsung tanpa source dokumen


class AsetTetap(BaseModel, BaseMixin):
    __tablename__ = "aset_tetap"

    capitalized = Column(Boolean, nullable=False, default=False)
    lokasi = Column(String(150), nullable=True)
    kode = Column(String(30), unique=True, nullable=False, index=True)
    nama = Column(String(150), nullable=False)
    kategori_aset_id = Column(UUID(as_uuid=True), ForeignKey("kategori_aset.id"), nullable=False)
    akun_aset_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    akun_akumulasi_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    akun_beban_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    kuantitas = Column(Integer, default=1, nullable=False)
    umur_aset = Column(Integer, default=0, nullable=False)
    metode_penyusutan = Column(SQLEnum(MetodePenyusutan), default=MetodePenyusutan.GARIS_LURUS, nullable=False)
    nilai_sisa = Column(Numeric(18, 2), default=0, nullable=False)
    nilai_perolehan = Column(Numeric(18, 2), default=0, nullable=False)
    nilai_buku = Column(Numeric(18, 2), default=0, nullable=False)
    akumulasi_penyusutan = Column(Numeric(18, 2), default=0, nullable=False)
    penyusutan_per_bulan = Column(Numeric(18, 2), default=0, nullable=False)
    tanggal_mulai = Column(DateTime(timezone=True), nullable=False)
    catatan = Column(Text, nullable=True)
    auto_post_jurnal = Column(Boolean, default=False, nullable=False)  # Phase H: deprecated
    status = Column(SQLEnum(StatusAsetTetap), default=StatusAsetTetap.AKTIF, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # === NEW Phase 7 — Acquisition source trace (Roadmap §24: "Acquisition source trace") ===
    # Tipe sumber akuisisi: MANUAL_JOURNAL / PURCHASE_INVOICE / SALDO_AWAL / DIRECT
    acquisition_source_type = Column(String(30), nullable=True, index=True)
    # ID sumber akuisisi (UUID jurnal_umum.id atau purchase_invoice.id)
    acquisition_source_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    # Nomor dokumen sumber untuk display (mis. JV-2026-09-001 atau PINV-2026-09-001)
    acquisition_source_no = Column(String(30), nullable=True)
    # Tanggal akuisisi (bisa beda dari tanggal_mulai penyusutan)
    acquisition_date = Column(Date, nullable=True)

    # Relationship
    kategori_aset = relationship("KategoriAset")
    akun_aset = relationship("AkunPerkiraan", foreign_keys=[akun_aset_id])
    akun_akumulasi = relationship("AkunPerkiraan", foreign_keys=[akun_akumulasi_id])
    akun_beban = relationship("AkunPerkiraan", foreign_keys=[akun_beban_id])
    creator = relationship("Pengguna", foreign_keys=[created_by])