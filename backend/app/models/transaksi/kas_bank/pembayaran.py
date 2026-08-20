import enum
from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class StatusTransaksi(str, enum.Enum):
    DRAFT = "DRAFT"
    SELESAI = "SELESAI"
    BATAL = "BATAL"


class PembayaranKas(BaseModel, BaseMixin):
    __tablename__ = "pembayaran_kas"

    no_bukti = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    kas_bank_id = Column(UUID(as_uuid=True), ForeignKey("kas_bank_akun.id"), nullable=False)
    no_bukti = Column(String(50), nullable=False)
    no_cek = Column(String(50), nullable=True)
    penerima = Column(String(255), nullable=True)
    catatan = Column(Text, nullable=True)
    total_nilai = Column(Numeric(18, 2), default=0, nullable=False)
    auto_post_jurnal = Column(Boolean, default=True, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusTransaksi), default=StatusTransaksi.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    kas_bank = relationship("KasBankAkun")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    rincian = relationship("PembayaranRincian", back_populates="pembayaran", cascade="all, delete-orphan")