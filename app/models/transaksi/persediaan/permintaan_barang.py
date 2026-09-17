import enum
from sqlalchemy import Column, String, Text, Integer, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class StatusPersediaan(str, enum.Enum):
    DIAJUKAN = "DIAJUKAN"
    DISETUJUI = "DISETUJUI"
    DITOLAK = "DITOLAK"
    SELESAI = "SELESAI"
    BATAL = "BATAL"


class PermintaanBarang(BaseModel, BaseMixin):
    __tablename__ = "permintaan_barang"

    no_permintaan = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    diajukan_oleh = Column(String(100), nullable=False)
    auto_post_jurnal = Column(Boolean, default=False, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusPersediaan), default=StatusPersediaan.DIAJUKAN, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    barang = relationship("Barang")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
