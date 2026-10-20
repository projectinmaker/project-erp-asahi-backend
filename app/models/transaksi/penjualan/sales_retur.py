from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.penjualan.sales_order import StatusPenjualan


class SalesRetur(BaseModel, BaseMixin):
    __tablename__ = "sales_retur"

    no_retur = Column(String(30), unique=True, nullable=False, index=True)
    gudang_id = Column(UUID(as_uuid=True), ForeignKey("gudang.id"), nullable=True)
    pengiriman_id = Column(UUID(as_uuid=True), ForeignKey("pengiriman_barang.id"), nullable=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    no_pengembalian = Column(String(50), nullable=True)
    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey("sales_invoice.id"), nullable=False)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    alamat_pengembalian = Column(Text, nullable=True)
    diskon_global = Column(Numeric(5, 2), default=0, nullable=True)
    ppn = Column(Numeric(5, 2), default=11, nullable=False)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    total_ppn = Column(Numeric(18, 2), default=0, nullable=False)
    grand_total = Column(Numeric(18, 2), default=0, nullable=False)
    # === AUD-12 (update #05) — pembalikan PPh 23 oleh retur ===
    # Flag & tarif DISALIN dari SI sumber saat create (bukan input user).
    # total_pph23 dihitung refresh_totals atas DPP retur (di-cap sisa pph23 SI);
    # grand_total retur menjadi NET-of-PPh23 sehingga jurnal retur membalik
    # Dr PPH23_DIBAYAR_DIMUKA (116003) posting SI secara proporsional.
    pph23_applicable = Column(Boolean, nullable=False, default=False, server_default="false")
    pph23 = Column(Numeric(5, 2), default=2, nullable=False, server_default="2")
    total_pph23 = Column(Numeric(18, 2), default=0, nullable=False, server_default="0")
    auto_post_jurnal = Column(Boolean, default=False, nullable=False)  # Phase E: deprecated
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusPenjualan), default=StatusPenjualan.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    sales_invoice = relationship("SalesInvoice", back_populates="retur")
    pelanggan = relationship("Pelanggan")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship("SalesReturDetail", back_populates="sales_retur", cascade="all, delete-orphan")
