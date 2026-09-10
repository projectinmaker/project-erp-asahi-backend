from datetime import datetime
from uuid import UUID
from typing import Optional, List
from app.schemas.base import BaseSchema, PaginatedResponse
from app.schemas.organization import OrganizationDimensions


class WorkflowEventResponse(BaseSchema):
    version: int
    action: str
    from_state: str
    to_state: str
    actor_id: UUID
    reason: Optional[str] = None
    at: datetime


class WorkflowSummary(BaseSchema):
    organization: OrganizationDimensions | None = None
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
