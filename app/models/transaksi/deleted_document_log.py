"""Log histori dokumen yang dihapus permanen (hard delete) — Task 27-a.

Semua endpoint /cancel kini melakukan HARD DELETE: dokumen + baris anaknya
dihapus permanen dari database (jurnal terkait ikut dihapus). Tabel ini
menyimpan jejak audit lengkap berupa snapshot JSON sebelum penghapusan,
sehingga histori tetap dapat ditelusuri lewat modul Histori Dokumen.
"""
from sqlalchemy import Column, String, Text, Numeric, ForeignKey, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class DeletedDocumentLog(BaseModel, BaseMixin):
    __tablename__ = 'deleted_document_log'

    # Identitas dokumen sumber (tablename dokumen, bukan nama Python)
    document_type = Column(String(50), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    document_number = Column(String(60), nullable=True)
    document_date = Column(DateTime, nullable=True)
    document_status = Column(String(30), nullable=True)  # status saat dihapus
    total_amount = Column(Numeric(18, 2), nullable=True)
    # Snapshot lengkap (header, children, jurnal, workflow + events) — JSON apa adanya
    snapshot = Column(JSONB, nullable=False)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=True)
    deleted_at = Column(DateTime, server_default=func.now(), nullable=False)
    reason = Column(Text, nullable=True)

    # Relationships
    deleter = relationship("Pengguna", foreign_keys=[deleted_by])
