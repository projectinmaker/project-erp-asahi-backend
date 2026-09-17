"""Document workflow and immutable action history (no public update/delete API)."""
from sqlalchemy import Column, String, Text, ForeignKey, Integer, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.database import BaseModel
from app.models.base import BaseMixin


class DocumentWorkflow(BaseModel, BaseMixin):
    __tablename__ = 'document_workflow'
    __table_args__ = (
        UniqueConstraint('document_type', 'document_id', name='uq_workflow_document'),
        CheckConstraint("state IN ('DRAFT','PENDING','APPROVED','REJECTED','POSTED','EXECUTED','CANCELLED')", name='ck_workflow_state'),
    )
    document_type = Column(String(40), nullable=False)
    document_id = Column(UUID(as_uuid=True), nullable=False)
    state = Column(String(20), nullable=False, default='DRAFT')
    version = Column(Integer, nullable=False, default=0)
    submitted_by = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=True)
    approved_by = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=True)


class WorkflowEvent(BaseModel, BaseMixin):
    __tablename__ = 'workflow_event'
    __table_args__ = (UniqueConstraint('workflow_id', 'version', name='uq_workflow_event_version'),)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey('document_workflow.id'), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    action = Column(String(20), nullable=False)
    from_state = Column(String(20), nullable=False)
    to_state = Column(String(20), nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=False)
    reason = Column(Text, nullable=True)


class IdempotentOperation(BaseModel, BaseMixin):
    __tablename__ = 'idempotent_operation'
    __table_args__ = (UniqueConstraint('actor_id', 'request_key', name='uq_idempotent_actor_key'),)
    actor_id = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=False)
    request_key = Column(String(128), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    result_type = Column(String(80), nullable=False)
    result_id = Column(UUID(as_uuid=True), nullable=False)
