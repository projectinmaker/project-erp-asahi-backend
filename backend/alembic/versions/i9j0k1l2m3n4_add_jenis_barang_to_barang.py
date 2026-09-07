"""add_jenis_barang_to_barang

Tambah kolom jenis_barang pada tabel barang. Sebelumnya field ini
dikirim frontend tapi diabaikan backend karena kolomnya belum ada.

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'i9j0k1l2m3n4'
down_revision: Union[str, Sequence[str], None] = 'h8i9j0k1l2m3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'barang',
        sa.Column('jenis_barang', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('barang', 'jenis_barang')