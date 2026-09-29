"""Schemas modul Histori Dokumen Terhapus (audit log hard delete) — Task 27-a."""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class DeletedByUser(BaseSchema):
    """Info sederhana pengguna yang menghapus dokumen."""
    id: UUID
    username: str
    nama_lengkap: str


class DeletedDocumentLogResponse(BaseSchema):
    """Ringkasan entri log dokumen terhapus (tanpa snapshot)."""
    id: UUID
    document_type: str
    document_id: UUID
    document_number: Optional[str] = None
    document_date: Optional[datetime] = None
    document_status: Optional[str] = None
    total_amount: Optional[Decimal] = None
    # Nested info user: relasi ORM bernama `deleter` (kolom deleted_by adalah UUID);
    # key JSON tetap `deletedBy` lewat alias_generator.
    deleted_by: Optional[DeletedByUser] = Field(None, validation_alias='deleter')
    deleted_at: datetime
    reason: Optional[str] = None


class DeletedDocumentLogDetailResponse(DeletedDocumentLogResponse):
    """Detail lengkap entri log termasuk snapshot JSON apa adanya."""
    snapshot: Dict[str, Any]
