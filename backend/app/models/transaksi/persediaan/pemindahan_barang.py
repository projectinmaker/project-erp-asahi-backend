import enum
from sqlalchemy import Column, String, Text, Integer, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.persediaan.permintaan_barang import StatusPersediaan


class ProsesPemindahan(str, enum.Enum):
    KIRIM = "KIRIM"
    TERIMA = "TERIMA"


class PemindahanBarang(BaseModel, BaseMixin):
    __tablename__ = "pemindahan_barang"

    no_pemindahan = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    proses = Column(SQLEnum(ProsesPemindahan), nullable=False)
    dari_gudang_id = Column(UUID(as_uuid=True), ForeignKey("gudang.id"), nullable=False)
    ke_gudang_id = Column(UUID(as_uuid=True), ForeignKey("gudang.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    auto_post_jurnal = Column(Boolean, default=False, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusPersediaan), default=StatusPersediaan.DIAJUKAN, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    dari_gudang = relationship("Gudang", foreign_keys=[dari_gudang_id])
    ke_gudang = relationship("Gudang", foreign_keys=[ke_gudang_id])
    barang = relationship("Barang")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
