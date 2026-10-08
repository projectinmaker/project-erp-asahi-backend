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

    # === Update ASAHI (PPh23/PPN opsional) — pilihan pajak saat input PO ===
    # ppn_applicable=False → total_ppn dipaksa 0 (PPN tidak dipilih).
    # pph23_applicable=True → total_pph23 dihitung dan MEMOTONG grand_total
    # (PPh23 dipotong dari pembayaran ke supplier). Boleh keduanya, salah
    # satu, atau tidak sama sekali (sesuai permintaan user).
    ppn_applicable = Column(Boolean, nullable=False, default=True, server_default="true")
    pph23_applicable = Column(Boolean, nullable=False, default=False, server_default="false")
    pph23 = Column(Numeric(5, 2), default=2, nullable=False, server_default="2")
    total_pph23 = Column(Numeric(18, 2), default=0, nullable=False, server_default="0")

    # === LEGACY — DEPRECATED (Catatan Purchase Order §6: PO tidak boleh posting jurnal) ===
    auto_post_jurnal = Column(Boolean, default=False, nullable=False)  # Changed default to False
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)

    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusPenjualan), default=StatusPenjualan.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # === NEW Phase B — Catatan Purchase Order §10-11 ===
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id", name="fk_po_syarat_bayar"), nullable=True, index=True)
    currency = Column(String(3), nullable=False, default='IDR')
    supplier_name_snapshot = Column(String(200), nullable=True)  # For historical reference

    # === Update ASAHI #3 — alamat pengiriman (gudang tujuan) + PPIC ===
    # Referensi ke master alamat_pengiriman (SET NULL bila master dihapus);
    # snapshot teks di alamat_pengiriman dipakai untuk cetak agar dokumen
    # lama tidak berubah ketika master diedit/dihapus.
    alamat_pengiriman_id = Column(UUID(as_uuid=True), ForeignKey("alamat_pengiriman.id", name="fk_po_alamat_pengiriman", ondelete="SET NULL"), nullable=True, index=True)
    alamat_pengiriman = Column(Text, nullable=True)  # snapshot: "<prefix>\n<nama>"
    ppic = Column(Boolean, nullable=False, default=False)

    # Relationships
    supplier = relationship("Supplier")
    syarat_bayar = relationship("SyaratBayar", foreign_keys=[syarat_bayar_id])
    jurnal = relationship("JurnalUmum")  # LEGACY — should always be null for new PO
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship("PurchaseOrderDetail", back_populates="purchase_order", cascade="all, delete-orphan")
    biaya_tambahan = relationship("TransaksiBiaya", back_populates="purchase_order", cascade="all, delete-orphan")
    penerimaan = relationship("PenerimaanBarang", back_populates="purchase_order")
    retur = relationship("PurchaseRetur", back_populates="purchase_order")
