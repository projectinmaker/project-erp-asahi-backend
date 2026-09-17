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

    # === NEW Phase 4 — Source-line trace (Roadmap §16: "Sales Return: invoice_detail_id, delivery_detail_id") ===
    sales_invoice_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sales_invoice_detail.id", name="fk_retur_detail_invoice_detail"),
        nullable=True,
        index=True,
    )
    pengiriman_barang_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pengiriman_barang_detail.id", name="fk_retur_detail_pengiriman_detail"),
        nullable=True,
        index=True,
    )

    # Relationships
    sales_retur = relationship("SalesRetur", back_populates="details")
    barang = relationship("Barang")
    # Source-line trace
    sales_invoice_detail = relationship("SalesInvoiceDetail", foreign_keys=[sales_invoice_detail_id])
    pengiriman_barang_detail = relationship("PengirimanBarangDetail", foreign_keys=[pengiriman_barang_detail_id])
