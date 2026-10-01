from sqlalchemy import Column, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class Penawaran(BaseModel, BaseMixin):
    """Penawaran (quotation) — dokumen pra-order penjualan.

    Blueprint = SalesOrder (header + detail + TransaksiBiaya), TANPA jurnal
    dan TANPA workflow (tidak pernah diposting; status hanya bergerak
    DRAFT -> SELESAI saat dikonversi menjadi Sales Order).

    Total (sub_total/total_diskon/total_ppn/total_biaya_tambahan/grand_total)
    dihitung dengan pola yang sama dengan SalesOrder:
    lihat app.services.document_totals.refresh_totals (order_docs).
    `diskon_global` bersifat PERSEN (mirror SalesOrder.diskon_global).
    """

    __tablename__ = "penawaran"

    no_penawaran = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    berlaku_hingga = Column(Date, nullable=True)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id"), nullable=True)
    alamat_pengiriman = Column(String(255), nullable=True)
    keterangan = Column(Text, nullable=True)
    mata_uang = Column(String(10), nullable=False, default="IDR")
    diskon_global = Column(Numeric(18, 2), default=0, nullable=False)
    ppn = Column(Numeric(5, 2), default=11, nullable=False)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    total_diskon = Column(Numeric(18, 2), default=0, nullable=False)
    total_ppn = Column(Numeric(18, 2), default=0, nullable=False)
    total_biaya_tambahan = Column(Numeric(18, 2), default=0, nullable=False)
    grand_total = Column(Numeric(18, 2), default=0, nullable=False)
    status = Column(String(20), nullable=False, default="DRAFT")
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=True)

    # Relationships (mirror SalesOrder)
    pelanggan = relationship("Pelanggan")
    syarat_bayar = relationship("SyaratBayar")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship(
        "PenawaranDetail", back_populates="penawaran", cascade="all, delete-orphan"
    )
    biaya_tambahan = relationship(
        "TransaksiBiaya", back_populates="penawaran", cascade="all, delete-orphan"
    )
