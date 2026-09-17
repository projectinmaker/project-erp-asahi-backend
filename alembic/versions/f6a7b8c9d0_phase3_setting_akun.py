"""phase3_setting_akun

Phase 3: Tambah tabel setting_akun untuk konfigurasi akun default.
Digunakan oleh auto-posting jurnal di semua modul transaksi.

Revision ID: f6a7b8c9d0
Revises: e5f6a7b8c9
Create Date: 2026-09-01 10:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'setting_akun',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('key', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('akun_perkiraan_id', sa.UUID(as_uuid=True), sa.ForeignKey('akun_perkiraan.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('setting_akun')
