from sqlalchemy import Column, String, Text, ForeignKey, Enum as SQLEnum, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.penjualan.sales_order import StatusPenjualan


class PengirimanBarang(BaseModel, BaseMixin):
    __tablename__ = "pengiriman_barang"

    no_surat_jalan = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    ekspedisi = Column(String(100), nullable=True)
    sales_order_id = Column(UUID(as_uuid=True), ForeignKey("sales_order.id"), nullable=False)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    alamat_pengiriman = Column(Text, nullable=True)
    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusPenjualan), default=StatusPenjualan.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    sales_order = relationship("SalesOrder", back_populates="pengiriman")
    pelanggan = relationship("Pelanggan")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship("PengirimanBarangDetail", back_populates="pengiriman", cascade="all, delete-orphan")
