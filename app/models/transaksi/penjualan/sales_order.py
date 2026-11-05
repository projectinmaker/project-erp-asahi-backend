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

    no_pesanan = Column(String(40), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id"), nullable=True)
    tanggal_pengiriman = Column(DateTime(timezone=True), nullable=True)  # label UI/cetak: "Target Pengiriman"
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    alamat_pengiriman = Column(Text, nullable=True)
    diskon_global = Column(Numeric(5, 2), default=0, nullable=True)
    # === Update #042 — PPN dihapus dari SO: pajak hanya dihitung/diterapkan di
    #     INVOICE. Kolom `ppn` & `total_ppn` di-drop (grand total SO kini =
    #     subtotal − diskon + biaya, tanpa PPN; migrasi cp0000000030). ===
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    total_diskon = Column(Numeric(18, 2), default=0, nullable=False)
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

    # === Update #032 — link ke Penawaran sumber ===
    # SO dibuat dari penawaran (konversi tombol "Buat SO" maupun form SO baru
    # dengan "Pilih Penawaran"). Satu penawaran boleh menaut BANYAK SO
    # (repeat order) — link dipakai untuk counter pemakaian penawaran dan
    # menjaga integritas hapus. NULL = SO manual tanpa sumber penawaran.
    penawaran_id = Column(
        UUID(as_uuid=True), ForeignKey("penawaran.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # Relationships
    syarat_bayar = relationship("SyaratBayar")
    pelanggan = relationship("Pelanggan")
    jurnal = relationship("JurnalUmum")  # LEGACY — should always be null for new SO
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship("SalesOrderDetail", back_populates="sales_order", cascade="all, delete-orphan")
    biaya_tambahan = relationship("TransaksiBiaya", back_populates="sales_order", cascade="all, delete-orphan")
    pengiriman = relationship("PengirimanBarang", back_populates="sales_order")
    # Update #032 — penawaran sumber (repeat order: satu penawaran → banyak SO).
    penawaran = relationship("Penawaran", back_populates="sales_orders")
    # === Update #031 — termin pembayaran (baris nominal; dibayar via Penerimaan
    # jenis TERMIN_SO). Urutan baris mengikuti urutan input. ===
    termin_pembayaran = relationship(
        "SalesOrderTermin", back_populates="sales_order",
        cascade="all, delete-orphan", order_by="SalesOrderTermin.urutan",
    )

    # === Update #031 — ringkasan termin untuk list/kolom "Pembayaran" ===
    @property
    def termin_total(self):
        """Total nominal seluruh termin (0 bila SO tanpa termin)."""
        return sum((t.nominal or 0 for t in self.termin_pembayaran), 0)

    @property
    def termin_dibayar(self):
        """Total nominal termin yang sudah dibayar (link penerimaan aktif)."""
        return sum(
            (t.nominal or 0 for t in self.termin_pembayaran if t.penerimaan_kas_id is not None), 0
        )

    # === Update #032 — info penawaran sumber (repeat order; None = SO manual) ===
    @property
    def penawaran_no(self):
        return self.penawaran.no_penawaran if self.penawaran is not None else None

    @property
    def penawaran_revisi(self):
        return (self.penawaran.revisi_ke or 1) if self.penawaran is not None else None

    @property
    def pelanggan_nama(self):
        """Nama pelanggan (Update ASAHI #002 — dipakai nested mini di baris PO)."""
        return self.pelanggan.nama if self.pelanggan else None
