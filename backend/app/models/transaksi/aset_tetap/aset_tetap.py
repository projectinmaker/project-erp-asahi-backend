import enum
from sqlalchemy import Column, String, Text, Integer, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
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


class AsetTetap(BaseModel, BaseMixin):
    __tablename__ = "aset_tetap"

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
    auto_post_jurnal = Column(Boolean, default=True, nullable=False)
    status = Column(SQLEnum(StatusAsetTetap), default=StatusAsetTetap.AKTIF, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationship
    kategori_aset = relationship("KategoriAset")
    akun_aset = relationship("AkunPerkiraan", foreign_keys=[akun_aset_id])
    akun_akumulasi = relationship("AkunPerkiraan", foreign_keys=[akun_akumulasi_id])
    akun_beban = relationship("AkunPerkiraan", foreign_keys=[akun_beban_id])
    creator = relationship("Pengguna", foreign_keys=[created_by])