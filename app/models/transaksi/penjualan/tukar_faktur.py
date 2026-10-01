from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class TukarFaktur(BaseModel, BaseMixin):
    """Tukar Faktur (proof of receipt) — dokumen tanda terima faktur (Update #4).

    Mirror modul Penawaran: TANPA jurnal, TANPA pergerakan stok, TANPA
    workflow approval. Status bergerak DRAFT -> SELESAI (manual) atau
    DIBATALKAN-via-hapus (hard delete lewat endpoint cancel).

    Header menyimpan SNAPSHOT referensi invoice saat dibuat (no_so,
    no_po_customer, no_surat_jalan, total) — immutable setelah create —
    sehingga cetak dokumen tidak bergantung pada perubahan data sumber.
    """

    __tablename__ = "tukar_faktur"

    no_tukar_faktur = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey("sales_invoice.id"), nullable=False)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    # snapshot referensi utk cetak (immutable setelah create)
    no_so = Column(String(30), nullable=True)
    no_po_customer = Column(String(50), nullable=True)
    no_surat_jalan = Column(String(255), nullable=True)  # join distinct, koma
    total = Column(Numeric(18, 2), default=0, nullable=False)  # snapshot grand_total invoice
    keterangan = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="DRAFT")
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=True)

    # Relationships
    pelanggan = relationship("Pelanggan")
    sales_invoice = relationship("SalesInvoice")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship(
        "TukarFakturDetail", back_populates="tukar_faktur", cascade="all, delete-orphan"
    )
