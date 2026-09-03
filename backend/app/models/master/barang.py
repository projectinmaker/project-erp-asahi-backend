import enum
from sqlalchemy import Column, String, Integer, Numeric, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class MetodeValuasi(str, enum.Enum):
    AVERAGE = "AVERAGE"
    FIFO = "FIFO"
    FEFO = "FEFO"


class Barang(BaseModel, BaseMixin):
    __tablename__ = "barang"

    kode = Column(String(20), unique=True, nullable=False, index=True)
    nama = Column(String(200), nullable=False)
    kategori_id = Column(UUID(as_uuid=True), ForeignKey("kategori_barang.id"), nullable=False)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=False)
    harga_pokok = Column(Numeric(18, 2), default=0, nullable=False)
    stok = Column(Integer, default=0, nullable=False)
    stok_minimum = Column(Integer, default=0, nullable=False)
    metode_valuasi = Column(
        SQLEnum(MetodeValuasi), default=MetodeValuasi.AVERAGE, nullable=False
    )
    status = Column(String(20), default="AKTIF", nullable=False)

    kategori = relationship("KategoriBarang", backref="barangs")
    satuan = relationship("Satuan")