from sqlalchemy import Column, String, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class TransaksiBiaya(BaseModel, BaseMixin):
    """Biaya tambahan per transaksi (SO, SINV, PO, PINV). 
    Hanya 1 FK yang boleh terisi per row."""
    __tablename__ = "transaksi_biaya"

    sales_order_id = Column(UUID(as_uuid=True), ForeignKey("sales_order.id"), nullable=True)
    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey("sales_invoice.id"), nullable=True)
    purchase_order_id = Column(UUID(as_uuid=True), ForeignKey("purchase_order.id"), nullable=True)
    purchase_invoice_id = Column(UUID(as_uuid=True), ForeignKey("purchase_invoice.id"), nullable=True)
    nama = Column(String(100), nullable=False)
    jumlah = Column(Numeric(18, 2), default=0, nullable=False)

    # Relationships
    sales_order = relationship("SalesOrder", back_populates="biaya_tambahan")
    sales_invoice = relationship("SalesInvoice", back_populates="biaya_tambahan")
    purchase_order = relationship("PurchaseOrder", back_populates="biaya_tambahan")
    purchase_invoice = relationship("PurchaseInvoice", back_populates="biaya_tambahan")
