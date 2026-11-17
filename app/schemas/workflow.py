from datetime import datetime
from uuid import UUID
from typing import Optional, List
from app.schemas.base import BaseSchema, PaginatedResponse


class WorkflowEventResponse(BaseSchema):
    version: int
    action: str
    from_state: str
    to_state: str
    actor_id: UUID
    # === Update #071 — nama pelaku untuk tampilan riwayat persetujuan
    #     (frontend menampilkan nama, bukan UUID mentah). ===
    actor_name: Optional[str] = None
    reason: Optional[str] = None
    at: datetime


class WorkflowSummary(BaseSchema):
    document_type: str
    document_id: UUID
    document_number: str
    created_by: UUID
    submitted_by: Optional[UUID] = None
    approved_by: Optional[UUID] = None
    can_edit: bool
    tanggal: datetime
    total: str
    state: str
    document_status: str
    version: int
    journal_id: Optional[UUID] = None
    available_actions: List[str]


class WorkflowResponse(WorkflowSummary):
    history: List[WorkflowEventResponse]


class HardDeleteRequest(BaseSchema):
    """Body optional untuk endpoint /cancel yang kini menjadi hard delete."""
    reason: Optional[str] = None
