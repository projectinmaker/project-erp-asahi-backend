from sqlalchemy import Column, String, ForeignKey, UniqueConstraint, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID
from app.database import BaseModel
from app.models.base import BaseMixin

FIELDS = ('company_id', 'branch_id', 'department_id', 'cost_center_id', 'project_id')


class OrganizationUnit(BaseModel, BaseMixin):
    __tablename__ = 'organization_unit'
    __table_args__ = (UniqueConstraint('kind', 'code', name='uq_organization_code'),)
    kind = Column(String(16), nullable=False)
    code = Column(String(30), nullable=False)
    name = Column(String(150), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'))
    status = Column(String(16), nullable=False, default='AKTIF')


class DocumentOrganization(BaseModel, BaseMixin):
    __tablename__ = 'document_organization'
    # UUIDs are globally generated; disallow ambiguous journal-source lookups.
    document_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    document_type = Column(String(40), nullable=False)
    company_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'))
    branch_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'))
    department_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'))
    cost_center_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'))
    project_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'))


class ReportingAudit(BaseModel, BaseMixin):
    __tablename__ = 'reporting_audit'
    entity_type = Column(String(40), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    action = Column(String(30), nullable=False)
    before = Column(JSON)
    after = Column(JSON)
    actor_id = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=False)


class CashFlowClassification(BaseModel, BaseMixin):
    __tablename__ = 'cash_flow_classification'
    __table_args__ = (UniqueConstraint('target_type', 'target_id', name='uq_cashflow_target'),)
    target_type = Column(String(16), nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    category = Column(String(24), nullable=False)
