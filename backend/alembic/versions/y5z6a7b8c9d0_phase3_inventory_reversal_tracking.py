"""phase3_inventory_reversal_tracking

Phase 3 — Inventory Core Engine (Master Roadmap §10: Reversal exact value/layer).

Tambah kolom `reversal_of_id` ke tabel `stok_mutasi` untuk track hubungan
antara mutasi asli dengan mutasi reversal. Ini P0 untuk:
- "Reversal exact value/layer" — reversal harus bisa identifikasi layer mana
  yang dikonsumsi saat mutasi asli, supaya bisa restore exact layer tsb
  (terutama untuk FIFO/FEFO method).

- "Reversal mempertahankan audit trail" — setiap reversal mutasi stok punya
  link ke mutasi asli, supaya audit trail bisa trace kedua arah.

Schema change:
- stok_mutasi.reversal_of_id (UUID, nullable, FK self ke stok_mutasi.id)
- Index untuk query cepat "apa saja reversal dari mutasi X"

Tidak ada backfill data — kolom baru nullable, default NULL untuk semua
mutasi existing.

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "y5z6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "x4y5z6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tambah kolom reversal_of_id (nullable, FK self)
    op.add_column(
        'stok_mutasi',
        sa.Column('reversal_of_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.create_foreign_key(
        'fk_stok_mutasi_reversal_of',
        'stok_mutasi', 'stok_mutasi',
        ['reversal_of_id'], ['id'],
        ondelete='SET NULL',
    )
    # Index untuk query "reversal dari X" cepat
    op.create_index(
        'ix_stok_mutasi_reversal_of_id',
        'stok_mutasi',
        ['reversal_of_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_stok_mutasi_reversal_of_id', table_name='stok_mutasi')
    op.drop_constraint('fk_stok_mutasi_reversal_of', 'stok_mutasi', type_='foreignkey')
    op.drop_column('stok_mutasi', 'reversal_of_id')
