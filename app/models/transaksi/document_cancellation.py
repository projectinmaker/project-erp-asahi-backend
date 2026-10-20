"""AUD-01 (update #05) — audit trail pembatalan dokumen POSTED.

Dokumen yang sudah menghasilkan jurnal (POSTED/SELESAI) dibatalkan lewat
JURNAL PEMBALIK + status BATAL/DIBATALKAN (reversal-first); baris ini mencatat
siapa/kapan/alasan + daftar jurnal pembalik yang dibuat. Dokumen tanpa jurnal
(draft/order) tetap memakai hard delete + snapshot di deleted_document_log.
"""
from sqlalchemy import Column, String, Text, Numeric, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class DocumentCancellation(BaseModel, BaseMixin):
    __tablename__ = "document_cancellation"

    document_type = Column(String(40), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    document_number = Column(String(60), nullable=True)
    document_date = Column(DateTime(timezone=True), nullable=True)
    document_status = Column(String(30), nullable=True)
    total_amount = Column(Numeric(18, 2), nullable=True)
    reason = Column(Text, nullable=True)
    # REVERSAL = jurnal pembalik + status BATAL (dokumen POSTED);
    # HARD_DELETE = delegasi ke hard_delete_service (dokumen tanpa jurnal).
    mode = Column(String(20), nullable=False, default="REVERSAL")
    # Daftar jurnal pembalik: [{id, noJurnal, reversalOf}]
    reversal_jurnal_ids = Column(JSONB, nullable=True)
    cancelled_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=False)

    # Relationships
    canceller = relationship("Pengguna", foreign_keys=[cancelled_by])
