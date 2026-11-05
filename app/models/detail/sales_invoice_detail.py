from sqlalchemy import Column, Numeric, Integer, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class SalesInvoiceDetail(BaseModel, BaseMixin):
    __tablename__ = "sales_invoice_detail"

    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey("sales_invoice.id"), nullable=False)
    # === Update #032 — barang_id OPSIONAL: baris invoice bisa berupa ITEM
    # BEBAS (jasa/custom dari penawaran → SO; tidak ada di master barang).
    # Wajib punya item_nama saat barang_id NULL. Posting pendapatan baris
    # bebas jatuh ke akun PENDAPATAN_PENJUALAN default (lihat
    # post_sales_invoice: barang None → akun_rev default). ===
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=True)
    item_nama = Column(String(200), nullable=True)
    item_satuan = Column(String(50), nullable=True)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    diskon = Column(Numeric(5, 2), default=0, nullable=True)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    # === Update #042 — kolom `dp` per-baris DI-DROP: nilai yang ditagih
    #     invoice kini satu field header (sales_invoice.dp_pelunasan) yang
    #     didistribusikan proporsional ke baris oleh refresh_totals; sub_total
    #     baris = porsi dp_pelunasan proporsional thd gross baris. Migrasi
    #     cp0000000030. ===

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

    # Update #5 — nama satuan dasar barang untuk kolom Satuan di cetakan FE
    # Update #032 — fallback ke item_satuan utk baris item bebas (jasa/custom).
    @property
    def satuan(self) -> str | None:
        barang = self.barang
        if barang is not None and barang.satuan is not None:
            return barang.satuan.nama
        return self.item_satuan or None
