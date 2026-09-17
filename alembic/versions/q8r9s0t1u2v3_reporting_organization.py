"""Reporting organization snapshots and audited cash-flow classifications.

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'q8r9s0t1u2v3'
down_revision = 'p7q8r9s0t1u2'
branch_labels = None
depends_on = None
FIELDS = ('company_id', 'branch_id', 'department_id', 'cost_center_id', 'project_id')


def base_columns():
    return [sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table('organization_unit', *base_columns(),
        sa.Column('kind', sa.String(16), nullable=False), sa.Column('code', sa.String(30), nullable=False),
        sa.Column('name', sa.String(150), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('parent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organization_unit.id')),
        sa.UniqueConstraint('kind', 'code', name='uq_organization_code'))
    op.create_table('document_organization', *base_columns(),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column('document_type', sa.String(40), nullable=False),
        *[sa.Column(field, postgresql.UUID(as_uuid=True), sa.ForeignKey('organization_unit.id')) for field in FIELDS])
    op.create_table('reporting_audit', *base_columns(),
        sa.Column('entity_type', sa.String(40), nullable=False),
        sa.Column('entity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action', sa.String(30), nullable=False), sa.Column('before', sa.JSON), sa.Column('after', sa.JSON),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=False))
    op.create_table('cash_flow_classification', *base_columns(),
        sa.Column('target_type', sa.String(16), nullable=False),
        sa.Column('target_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category', sa.String(24), nullable=False),
        sa.UniqueConstraint('target_type', 'target_id', name='uq_cashflow_target'))
    for field in FIELDS:
        op.add_column('jurnal_umum', sa.Column(field, postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key('fk_jurnal_'+field, 'jurnal_umum', 'organization_unit', [field], ['id'])
        op.create_index('ix_jurnal_umum_'+field, 'jurnal_umum', [field])
    # Existing journals intentionally remain unassigned: no guessed historical ownership.


def downgrade():
    for field in reversed(FIELDS):
        op.drop_index('ix_jurnal_umum_'+field, table_name='jurnal_umum')
        op.drop_constraint('fk_jurnal_'+field, 'jurnal_umum', type_='foreignkey')
        op.drop_column('jurnal_umum', field)
    for table in ('cash_flow_classification', 'reporting_audit', 'document_organization', 'organization_unit'):
        op.drop_table(table)
