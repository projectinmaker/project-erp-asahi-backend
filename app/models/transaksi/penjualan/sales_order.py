import enum
from sqlalchemy import Column, String, Text, Numeric, Date, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class StatusPenjualan(str, enum.Enum):
    DRAFT = "DRAFT"
    DIPROSES = "DIPROSES"
    SELESAI = "SELESAI"
    DIBATALKAN = "DIBATALKAN"


class FulfillmentStatus(str, enum.Enum):
    """Separate fulfillment status from workflow status (Catatan Sales Order §8).
    
    workflow_status (existing `status` field): DRAFT → DIPROSES → SELESAI/DIBATALKAN
    fulfillment_status (new): OPEN → PARTIAL → FULFILLED → CLOSED
    
    These two are independent: an SO can be APPROVED (workflow) but PARTIAL (fulfillment).
    """
    OPEN = "OPEN"           # No delivery yet
    PARTIAL = "PARTIAL"     # Some items delivered/invoiced
    FULFILLED = "FULFILLED" # All items fully delivered and invoiced
    CLOSED = "CLOSED"       # Manually closed (remaining qty cancelled)


class SalesOrder(BaseModel, BaseMixin):
    __tablename__ = "sales_order"

    no_pesanan = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id"), nullable=True)
    fob = Column(String(50), nullable=True)
    ekspedisi = Column(String(100), nullable=True)
    tanggal_pengiriman = Column(DateTime(timezone=True), nullable=True)
    penjual = Column(String(100), nullable=True)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    alamat_pengiriman = Column(Text, nullable=True)
    diskon_global = Column(Numeric(5, 2), default=0, nullable=True)
    ppn = Column(Numeric(5, 2), default=11, nullable=False)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    total_diskon = Column(Numeric(18, 2), default=0, nullable=False)
    total_ppn = Column(Numeric(18, 2), default=0, nullable=False)
    total_biaya_tambahan = Column(Numeric(18, 2), default=0, nullable=False)
    grand_total = Column(Numeric(18, 2), default=0, nullable=False)

    # === LEGACY — DEPRECATED (Catatan Sales Order §3: SO tidak boleh posting jurnal) ===
    # Field tetap dipertahankan untuk backward compat, tapi service TIDAK PERNAH create jurnal dari SO.
    # Akan di-drop di phase cleanup setelah semua dependency bersih.
    auto_post_jurnal = Column(Boolean, default=False, nullable=False)  # Changed default to False
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)

    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusPenjualan), default=StatusPenjualan.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # === NEW Phase B — Catatan Sales Order §4 ===
    customer_po_number = Column(String(50), nullable=True)  # Customer PO reference (B2B)
    customer_po_date = Column(Date, nullable=True)
    fulfillment_status = Column(String(20), nullable=True)  # OPEN/PARTIAL/FULFILLED/CLOSED
    currency = Column(String(3), nullable=False, default='IDR')

    # Relationships
    syarat_bayar = relationship("SyaratBayar")
    pelanggan = relationship("Pelanggan")
    jurnal = relationship("JurnalUmum")  # LEGACY — should always be null for new SO
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship("SalesOrderDetail", back_populates="sales_order", cascade="all, delete-orphan")
    biaya_tambahan = relationship("TransaksiBiaya", back_populates="sales_order", cascade="all, delete-orphan")
    pengiriman = relationship("PengirimanBarang", back_populates="sales_order")
