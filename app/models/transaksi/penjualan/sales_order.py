import enum
from sqlalchemy import text, Column, String, Text, Numeric, Date, ForeignKey, Enum as SQLEnum, DateTime, Boolean
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

    # === Update AUD-12 — dimensi perusahaan (default ASAHI via migration cp14;
    # kolom nullable + server_default agar baris lama/single-company aman) ===
    company_id = Column(UUID(as_uuid=True), ForeignKey("organization_unit.id"),
                        nullable=True,
                        server_default=text("'0e9f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b'"))

    no_pesanan = Column(String(40), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id"), nullable=True)
    tanggal_pengiriman = Column(DateTime(timezone=True), nullable=True)  # label UI/cetak: "Target Pengiriman"
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

    # === Update ASAHI — revisi Sales Order ===
    # Kolom ekspedisi & penjual DIHAPUS dari dokumen SO (catatan update user).
    # customer_code = singkatan bebas nama perusahaan customer (input manual,
    # mis. "TBI" untuk TOYOTA BOSHUKU INDONESIA) yang menjadi segmen nomor SO:
    #   terisi  → "SO ASI/TBI/2026/001"
    #   kosong  → "SO ASI/-/2026/001"
    # Field immutable setelah create karena bagian dari nomor dokumen.
    customer_code = Column(String(20), nullable=True)

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

    @property
    def pelanggan_nama(self):
        """Nama pelanggan (Update ASAHI #002 — dipakai nested mini di baris PO)."""
        return self.pelanggan.nama if self.pelanggan else None
