from sqlalchemy import Column, Integer, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PengirimanBarangDetail(BaseModel, BaseMixin):
    __tablename__ = "pengiriman_barang_detail"

    pengiriman_id = Column(UUID(as_uuid=True), ForeignKey("pengiriman_barang.id"), nullable=False)
    # === Update #041 — barang_id & satuan_id OPSIONAL + item_nama/item_satuan:
    #     baris ITEM BEBAS (SO manufaktur/jasa/custom tanpa barang master)
    #     kini bisa dibuatkan Surat Jalan / DO — murni dokumen pengiriman,
    #     TIDAK menggerakkan stok/HPP (finish_pengiriman melewati baris bebas).
    #     Wajib item_nama bila barang_id NULL (mirror pola #032 SO/invoice). ===
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=True)
    item_nama = Column(String(200), nullable=True)
    item_satuan = Column(String(50), nullable=True)
    qty = Column(Integer, default=0, nullable=False)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=True)

    # === NEW Phase 4 — Source-line trace (Roadmap §13: "Delivery: sales_order_detail_id") ===
    # FK ke SalesOrderDetail — identifikasi line SO mana yang dipenuhi oleh delivery line ini.
    # Nullable untuk backward compat (delivery lama tanpa SO, mis. direct sales tanpa SO).
    sales_order_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sales_order_detail.id", name="fk_pengiriman_detail_so_detail"),
        nullable=True,
        index=True,
    )

    # Relationships
    pengiriman = relationship("PengirimanBarang", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan")
    # Source-line trace
    sales_order_detail = relationship("SalesOrderDetail", foreign_keys=[sales_order_detail_id])
