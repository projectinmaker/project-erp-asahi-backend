from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.penjualan.sales_order import StatusPenjualan


class PurchaseOrder(BaseModel, BaseMixin):
    __tablename__ = "purchase_order"

    no_pesanan = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    tanggal_kirim = Column(DateTime(timezone=True), nullable=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("supplier.id"), nullable=False)
    alamat = Column(Text, nullable=True)
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
    supplier = relationship("Supplier")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship("PurchaseOrderDetail", back_populates="purchase_order", cascade="all, delete-orphan")
    biaya_tambahan = relationship("TransaksiBiaya", back_populates="purchase_order", cascade="all, delete-orphan")
    penerimaan = relationship("PenerimaanBarang", back_populates="purchase_order")
    retur = relationship("PurchaseRetur", back_populates="purchase_order")
