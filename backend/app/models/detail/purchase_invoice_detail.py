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

    # === NEW Phase 5 — Source-line trace (Roadmap §20: "Purchase Invoice: purchase_order_detail_id, penerimaan_barang_detail_id") ===
    purchase_order_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("purchase_order_detail.id", name="fk_pinvoice_detail_po_detail"),
        nullable=True,
        index=True,
    )
    penerimaan_barang_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("penerimaan_barang_detail.id", name="fk_pinvoice_detail_penerimaan_detail"),
        nullable=True,
        index=True,
    )

    # Relationships
    purchase_invoice = relationship("PurchaseInvoice", back_populates="details")
    barang = relationship("Barang")
    # Source-line trace
    purchase_order_detail = relationship("PurchaseOrderDetail", foreign_keys=[purchase_order_detail_id])
    penerimaan_barang_detail = relationship("PenerimaanBarangDetail", foreign_keys=[penerimaan_barang_detail_id])

    # Three-way match bridge (Roadmap §20) — one invoice detail can match many receipt details
    match_entries = relationship("PurchaseInvoiceReceiptMatch", back_populates="purchase_invoice_detail", cascade="all, delete-orphan")
