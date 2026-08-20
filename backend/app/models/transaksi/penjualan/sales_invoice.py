from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.penjualan.sales_order import StatusPenjualan


class SalesInvoice(BaseModel, BaseMixin):
    __tablename__ = "sales_invoice"

    no_invoice = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id"), nullable=True)
    fob = Column(String(50), nullable=True)
    ekspedisi = Column(String(100), nullable=True)
    tanggal_pengiriman = Column(DateTime(timezone=True), nullable=True)
    sales_order_id = Column(UUID(as_uuid=True), ForeignKey("sales_order.id"), nullable=True)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    alamat_pengiriman = Column(Text, nullable=True)
    mata_uang = Column(String(10), default="IDR", nullable=False)
    diskon_global = Column(Numeric(5, 2), default=0, nullable=True)
    ppn = Column(Numeric(5, 2), default=11, nullable=False)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    total_diskon = Column(Numeric(18, 2), default=0, nullable=False)
    total_ppn = Column(Numeric(18, 2), default=0, nullable=False)
    total_biaya_tambahan = Column(Numeric(18, 2), default=0, nullable=False)
    grand_total = Column(Numeric(18, 2), default=0, nullable=False)
    auto_post_jurnal = Column(Boolean, default=True, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusPenjualan), default=StatusPenjualan.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    syarat_bayar = relationship("SyaratBayar")
    sales_order = relationship("SalesOrder")
    pelanggan = relationship("Pelanggan")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship("SalesInvoiceDetail", back_populates="sales_invoice", cascade="all, delete-orphan")
    biaya_tambahan = relationship("TransaksiBiaya", back_populates="sales_invoice", cascade="all, delete-orphan")
    retur = relationship("SalesRetur", back_populates="sales_invoice")
