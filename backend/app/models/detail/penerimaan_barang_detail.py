from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PenerimaanBarangDetail(BaseModel, BaseMixin):
    __tablename__ = "penerimaan_barang_detail"

    penerimaan_barang_id = Column(UUID(as_uuid=True), ForeignKey("penerimaan_barang.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=False)

    # Relationships
    penerimaan_barang = relationship("PenerimaanBarang", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan")
