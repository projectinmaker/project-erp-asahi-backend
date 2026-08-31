from sqlalchemy import Column, Boolean, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class BarangSatuan(BaseModel, BaseMixin):
    """Multi-satuan per barang.

    Satuan utama tetap di barang.satuan_id.
    Tabel ini untuk satuan tambahan (secondary units).
    Untuk sementara tanpa konversi — hanya daftar satuan saja.
    """
    __tablename__ = "barang_satuan"

    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False, index=True)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=False)
    is_utama = Column(Boolean, default=False, nullable=False)

    # Relationships
    barang = relationship("Barang", backref="daftar_satuan")
    satuan = relationship("Satuan")
