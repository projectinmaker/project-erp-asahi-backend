from sqlalchemy import Column, Numeric, Integer, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class SalesOrderDetail(BaseModel, BaseMixin):
    __tablename__ = "sales_order_detail"

    sales_order_id = Column(UUID(as_uuid=True), ForeignKey("sales_order.id"), nullable=False)
    # === Update #032 — barang_id OPSIONAL (mirror penawaran_detail #031): ===
    # baris SO bisa berupa ITEM BEBAS yang ditarik dari penawaran (nama +
    # satuan diketik manual; tidak ada di master barang, tidak menyentuh
    # gudang/stok). Baris bebas wajib punya item_nama. Item bebas TIDAK
    # BISA DIKIRIM (pengiriman hanya barang master) tapi BOLEH difakturkan
    # (jasa/custom — pendapatan posting ke akun default).
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=True)
    item_nama = Column(String(200), nullable=True)
    item_satuan = Column(String(50), nullable=True)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    diskon = Column(Numeric(5, 2), default=0, nullable=True)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)

    # === NEW Phase B — Catatan Sales Order §5: UOM on detail ===
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id", name="fk_so_detail_satuan"), nullable=True, index=True)

    # Relationships
    sales_order = relationship("SalesOrder", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan", foreign_keys=[satuan_id])
