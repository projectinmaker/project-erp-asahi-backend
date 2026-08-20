from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PengirimanBarangDetail(BaseModel, BaseMixin):
    __tablename__ = "pengiriman_barang_detail"

    pengiriman_id = Column(UUID(as_uuid=True), ForeignKey("pengiriman_barang.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=False)

    # Relationships
    pengiriman = relationship("PengirimanBarang", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan")
