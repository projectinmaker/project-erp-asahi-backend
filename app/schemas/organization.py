from typing import Literal
from datetime import datetime
from uuid import UUID
from pydantic import Field
from app.schemas.base import BaseSchema

class OrganizationCreate(BaseSchema):
    kind: Literal['COMPANY','BRANCH','DEPARTMENT','COST_CENTER','PROJECT']
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=150)
    parent_id: UUID | None = None

class OrganizationEdit(BaseSchema):
    name: str = Field(min_length=1, max_length=150)
    status: Literal['AKTIF','NONAKTIF'] = 'AKTIF'

class OrganizationResponse(OrganizationCreate):
    id: UUID
    status: str

class OrganizationDimensions(BaseSchema):
    company_id: UUID | None = None
    branch_id: UUID | None = None
    department_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None

class OrganizationAssignment(OrganizationDimensions):
    expected_version: int = Field(ge=0)

class CashFlowAssignment(BaseSchema):
    target_type: Literal['ACCOUNT','JOURNAL']
    target_id: UUID
    category: Literal['OPERASIONAL','INVESTASI','PEMBIAYAAN','BELUM_DIKLASIFIKASIKAN']

class CashFlowClassificationResponse(CashFlowAssignment):
    id: UUID


class ReportingAuditResponse(BaseSchema):
    id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    before: dict | None
    after: dict | None
    actor_id: UUID
    created_at: datetime
