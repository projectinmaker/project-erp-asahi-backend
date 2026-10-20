from sqlalchemy import text, Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.penjualan.sales_order import StatusPenjualan


class SalesRetur(BaseModel, BaseMixin):
    __tablename__ = "sales_retur"

    # === Update AUD-12 — dimensi perusahaan (default ASAHI via migration cp14;
    # kolom nullable + server_default agar baris lama/single-company aman) ===
    company_id = Column(UUID(as_uuid=True), ForeignKey("organization_unit.id"),
                        nullable=True,
                        server_default=text("'0e9f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b'"))

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
