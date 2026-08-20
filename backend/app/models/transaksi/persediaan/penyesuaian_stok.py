import enum
from sqlalchemy import Column, String, Text, Integer, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.persediaan.permintaan_barang import StatusPersediaan


class TipePenyesuaian(str, enum.Enum):
    TAMBAH = "TAMBAH"
    KURANG = "KURANG"


class PenyesuaianStok(BaseModel, BaseMixin):
    __tablename__ = "penyesuaian_stok"

    no_adj = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    tipe = Column(SQLEnum(TipePenyesuaian), nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    biaya_satuan = Column(Numeric(18, 2), default=0, nullable=False)
    total = Column(Numeric(18, 2), default=0, nullable=False)
    alasan = Column(Text, nullable=True)
    auto_post_jurnal = Column(Boolean, default=True, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusPersediaan), default=StatusPersediaan.DIAJUKAN, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    barang = relationship("Barang")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
