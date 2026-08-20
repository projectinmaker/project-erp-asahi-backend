from sqlalchemy import Column, Numeric, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class SalesOrderDetail(BaseModel, BaseMixin):
    __tablename__ = "sales_order_detail"

    sales_order_id = Column(UUID(as_uuid=True), ForeignKey("sales_order.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    diskon = Column(Numeric(5, 2), default=0, nullable=True)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)

    # Relationships
    sales_order = relationship("SalesOrder", back_populates="details")
    barang = relationship("Barang")
