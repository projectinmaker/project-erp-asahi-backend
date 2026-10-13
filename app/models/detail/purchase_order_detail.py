from sqlalchemy import Column, Numeric, Integer, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PurchaseOrderDetail(BaseModel, BaseMixin):
    __tablename__ = "purchase_order_detail"

    purchase_order_id = Column(UUID(as_uuid=True), ForeignKey("purchase_order.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    diskon = Column(Numeric(5, 2), default=0, nullable=True)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)

    # === NEW Phase B — Catatan Purchase Order §12: UOM on detail ===
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id", name="fk_po_detail_satuan"), nullable=True, index=True)

    # === Update ASAHI #002 — tracking item PO ke SO + kategori belanja ===
    # Satu PO bisa memuat item dari beberapa SO sekaligus (mis. baris 1-3 SO A,
    # baris 4-6 SO B) — relasi per BARIS, bukan per dokumen. Opsional:
    # barang umum/consumable tidak terikat SO.
    # jenis_pembelian = dasar belanja (CONSUMABLE/PROJECT/ASSET/ATK) —
    # menentukan tujuan posting COA saat invoice/penerimaan di-posting.
    sales_order_id = Column(UUID(as_uuid=True), ForeignKey("sales_order.id", name="fk_po_detail_sales_order", ondelete="SET NULL"), nullable=True, index=True)
    jenis_pembelian = Column(String(20), nullable=True)

    # Relationships
    purchase_order = relationship("PurchaseOrder", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan", foreign_keys=[satuan_id])
    sales_order = relationship("SalesOrder", foreign_keys=[sales_order_id])
