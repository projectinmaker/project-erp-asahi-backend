"""phase3_coa_tanggal

Phase 3: Tambah kolom tanggal ke akun_perkiraan.
Digunakan untuk mencatat tanggal mulai aktif/penempatan saldo awal akun.

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
    op.add_column(
        'akun_perkiraan',
        sa.Column('tanggal', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('akun_perkiraan', 'tanggal')
