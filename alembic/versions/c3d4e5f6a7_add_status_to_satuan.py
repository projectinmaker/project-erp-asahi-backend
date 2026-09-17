"""add_status_to_satuan

Menambahkan kolom 'status' pada tabel 'satuan'.
Sebelumnya tabel satuan hanya punya: id, nama, created_at, updated_at.
Tanpa kolom status, soft delete tidak bisa dilakukan padahal barang.satuan_id
adalah FK NOT NULL — hard delete akan menyebabkan error referential integrity.

Revision ID: c3d4e5f6a7
Revises: b2c3d4e5f6
Create Date: 2026-08-25 10:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tambah kolom status dengan default 'AKTIF' untuk data yang sudah ada
    op.add_column('satuan', sa.Column('status', sa.String(length=20), nullable=False, server_default='AKTIF'))


def downgrade() -> None:
    # Hapus kolom status jika perlu rollback
    op.drop_column('satuan', 'status')
