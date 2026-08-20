from sqlalchemy import Column, Numeric, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PurchaseInvoiceDetail(BaseModel, BaseMixin):
    __tablename__ = "purchase_invoice_detail"

    purchase_invoice_id = Column(UUID(as_uuid=True), ForeignKey("purchase_invoice.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    diskon = Column(Numeric(5, 2), default=0, nullable=True)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)

    # Relationships
    purchase_invoice = relationship("PurchaseInvoice", back_populates="details")
    barang = relationship("Barang")
