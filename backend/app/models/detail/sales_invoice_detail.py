from sqlalchemy import Column, Numeric, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class SalesInvoiceDetail(BaseModel, BaseMixin):
    __tablename__ = "sales_invoice_detail"

    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey("sales_invoice.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    diskon = Column(Numeric(5, 2), default=0, nullable=True)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)

    # === NEW Phase 4 — Source-line trace (Roadmap §14: "Sales Invoice: sales_order_detail_id, delivery_detail_id") ===
    sales_order_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sales_order_detail.id", name="fk_invoice_detail_so_detail"),
        nullable=True,
        index=True,
    )
    delivery_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pengiriman_barang_detail.id", name="fk_invoice_detail_delivery_detail"),
        nullable=True,
        index=True,
    )

    # Relationships
    sales_invoice = relationship("SalesInvoice", back_populates="details")
    barang = relationship("Barang")
    # Source-line trace
    sales_order_detail = relationship("SalesOrderDetail", foreign_keys=[sales_order_detail_id])
    delivery_detail = relationship("PengirimanBarangDetail", foreign_keys=[delivery_detail_id])
