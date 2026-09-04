"""add_isi_satuan_to_barang_satuan

Tambah kolom isi_satuan pada tabel barang_satuan sebagai faktor
konversi terhadap satuan utama (mis. 1 dus = 12 pcs -> isi_satuan=12).
Sebelumnya field ini dikirim frontend tapi diabaikan backend karena
kolomnya belum ada.

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'h8i9j0k1l2m3'
down_revision: Union[str, Sequence[str], None] = 'g7h8i9j0k1l2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'barang_satuan',
        sa.Column('isi_satuan', sa.Integer(), nullable=False, server_default='1'),
    )


def downgrade() -> None:
    op.drop_column('barang_satuan', 'isi_satuan')