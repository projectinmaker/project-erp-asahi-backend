"""Hard delete: tabel histori dokumen terhapus (deleted_document_log).

Menyimpan snapshot JSON lengkap dokumen yang dihapus permanen lewat endpoint
/cancel (semua modul), termasuk header, baris anak, jurnal terkait, serta
workflow + events — sebagai jejak audit pengganti soft-cancel.

Revision ID: hd0000000001
Revises: rbac00000001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'hd0000000001'
down_revision = 'rbac00000001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('deleted_document_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('document_type', sa.String(50), nullable=False),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('document_number', sa.String(60), nullable=True),
        sa.Column('document_date', sa.DateTime(), nullable=True),
        sa.Column('document_status', sa.String(30), nullable=True),
        sa.Column('total_amount', sa.Numeric(18, 2), nullable=True),
        sa.Column('snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('deleted_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True))
    op.create_index('ix_deleted_document_log_document_type', 'deleted_document_log', ['document_type'])
    op.create_index('ix_deleted_document_log_document_id', 'deleted_document_log', ['document_id'])


def downgrade():
    op.drop_index('ix_deleted_document_log_document_id', table_name='deleted_document_log')
    op.drop_index('ix_deleted_document_log_document_type', table_name='deleted_document_log')
    op.drop_table('deleted_document_log')
