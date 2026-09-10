"""Document workflow, action audit, and idempotent request identity.

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'n5o6p7q8r9s0'
down_revision = 'm4n5o6p7q8r9'
branch_labels = None
depends_on = None


def common():
    return [sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table('document_workflow', *common(),
        sa.Column('document_type', sa.String(40), nullable=False),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('submitted_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('pengguna.id')),
        sa.Column('approved_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('pengguna.id')),
        sa.UniqueConstraint('document_type', 'document_id', name='uq_workflow_document'),
        sa.CheckConstraint("state IN ('DRAFT','PENDING','APPROVED','REJECTED','POSTED','EXECUTED','CANCELLED')", name='ck_workflow_state'))
    op.create_table('workflow_event', *common(),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('document_workflow.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('from_state', sa.String(20), nullable=False),
        sa.Column('to_state', sa.String(20), nullable=False),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=False),
        sa.Column('reason', sa.Text()),
        sa.UniqueConstraint('workflow_id', 'version', name='uq_workflow_event_version'))
    op.create_index('ix_workflow_event_workflow_id', 'workflow_event', ['workflow_id'])
    op.create_table('idempotent_operation', *common(),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=False),
        sa.Column('request_key', sa.String(128), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('result_type', sa.String(80), nullable=False),
        sa.Column('result_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint('actor_id', 'request_key', name='uq_idempotent_actor_key'))


def downgrade():
    op.drop_table('idempotent_operation')
    op.drop_index('ix_workflow_event_workflow_id', table_name='workflow_event')
    op.drop_table('workflow_event')
    op.drop_table('document_workflow')
