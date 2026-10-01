from sqlalchemy import Column, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class PenawaranDetail(BaseModel, BaseMixin):
    __tablename__ = "penawaran_detail"

    penawaran_id = Column(UUID(as_uuid=True), ForeignKey("penawaran.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=True)
    qty = Column(Integer, default=0, nullable=False)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    diskon = Column(Numeric(5, 2), default=0, nullable=True)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    keterangan = Column(String(255), nullable=True)

    # Relationships
    penawaran = relationship("Penawaran", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan", foreign_keys=[satuan_id])
