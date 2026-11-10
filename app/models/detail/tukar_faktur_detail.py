from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class TukarFakturDetail(BaseModel, BaseMixin):
    """Baris detail Tukar Faktur — snapshot qty/satuan per barang dari invoice.

    === Update #045 — barang_id & satuan_id OPSIONAL + item_nama/item_satuan:
    baris ITEM BEBAS (jasa/custom dari penawaran → SO → invoice; tidak ada
    di master barang) kini bisa masuk Tukar Faktur. Mirror pola #032
    (sales_order_detail/sales_invoice_detail) & #041 (pengiriman_barang_detail).
    Wajib item_nama bila barang_id NULL. ===
    """

    __tablename__ = "tukar_faktur_detail"

    tukar_faktur_id = Column(UUID(as_uuid=True), ForeignKey("tukar_faktur.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=True)
    item_nama = Column(String(200), nullable=True)
    item_satuan = Column(String(50), nullable=True)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=True)
    qty = Column(Integer, default=0, nullable=False)

    # Relationships
    tukar_faktur = relationship("TukarFaktur", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan")
