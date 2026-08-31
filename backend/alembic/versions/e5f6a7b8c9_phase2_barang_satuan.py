"""phase2_barang_satuan

Phase 2: Tambah tabel barang_satuan untuk multi-satuan per barang.
Untuk sementara tanpa konversi — hanya daftar satuan saja.

Revision ID: e5f6a7b8c9
Revises: d4e5f6a7b8
Create Date: 2026-08-31 14:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'barang_satuan',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('barang_id', sa.UUID(as_uuid=True), sa.ForeignKey('barang.id'), nullable=False, index=True),
        sa.Column('satuan_id', sa.UUID(as_uuid=True), sa.ForeignKey('satuan.id'), nullable=False),
        sa.Column('is_utama', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('barang_satuan')
