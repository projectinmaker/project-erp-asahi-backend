from sqlalchemy import Column, Numeric, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class SalesReturDetail(BaseModel, BaseMixin):
    __tablename__ = "sales_retur_detail"

    sales_retur_id = Column(UUID(as_uuid=True), ForeignKey("sales_retur.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)

    # Relationships
    sales_retur = relationship("SalesRetur", back_populates="details")
    barang = relationship("Barang")
