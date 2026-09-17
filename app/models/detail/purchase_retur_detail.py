from sqlalchemy import Column, Numeric, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PurchaseReturDetail(BaseModel, BaseMixin):
    __tablename__ = "purchase_retur_detail"

    purchase_retur_id = Column(UUID(as_uuid=True), ForeignKey("purchase_retur.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)

    # === NEW Phase 5 — Source-line trace (Roadmap §22: "Purchase Return: Source PO Detail, Source Goods Receipt Detail, Source Purchase Invoice Detail") ===
    purchase_order_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("purchase_order_detail.id", name="fk_pretur_detail_po_detail"),
        nullable=True,
        index=True,
    )
    penerimaan_barang_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("penerimaan_barang_detail.id", name="fk_pretur_detail_penerimaan_detail"),
        nullable=True,
        index=True,
    )
    purchase_invoice_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("purchase_invoice_detail.id", name="fk_pretur_detail_pinvoice_detail"),
        nullable=True,
        index=True,
    )

    # Relationships
    purchase_retur = relationship("PurchaseRetur", back_populates="details")
    barang = relationship("Barang")
    # Source-line trace
    purchase_order_detail = relationship("PurchaseOrderDetail", foreign_keys=[purchase_order_detail_id])
    penerimaan_barang_detail = relationship("PenerimaanBarangDetail", foreign_keys=[penerimaan_barang_detail_id])
    purchase_invoice_detail = relationship("PurchaseInvoiceDetail", foreign_keys=[purchase_invoice_detail_id])
