from sqlalchemy import Column, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class TukarFakturDetail(BaseModel, BaseMixin):
    """Baris detail Tukar Faktur — snapshot qty/satuan per barang dari invoice."""

    __tablename__ = "tukar_faktur_detail"

    tukar_faktur_id = Column(UUID(as_uuid=True), ForeignKey("tukar_faktur.id"), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=True)
    qty = Column(Integer, default=0, nullable=False)

    # Relationships
    tukar_faktur = relationship("TukarFaktur", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan")
