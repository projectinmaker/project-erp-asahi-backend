"""add_jurnal_umum_id_pengiriman_barang

Tambah kolom jurnal_umum_id di pengiriman_barang, untuk menyimpan link ke
jurnal HPP (D: HPP Penjualan, K: Persediaan Barang Jadi) yang sekarang
di-posting saat Pengiriman Barang difinalisasi (bukan saat Sales Invoice
dibuat) — supaya sinkron dengan pengurangan stok fisik.

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-09-09 09:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'k2l3m4n5o6p7'
down_revision: Union[str, Sequence[str], None] = 'j1k2l3m4n5o6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'pengiriman_barang',
        sa.Column('jurnal_umum_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_pengiriman_barang_jurnal_umum_id',
        'pengiriman_barang', 'jurnal_umum',
        ['jurnal_umum_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_pengiriman_barang_jurnal_umum_id', 'pengiriman_barang', type_='foreignkey')
    op.drop_column('pengiriman_barang', 'jurnal_umum_id')