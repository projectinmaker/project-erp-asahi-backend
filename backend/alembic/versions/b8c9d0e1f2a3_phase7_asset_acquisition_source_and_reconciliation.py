"""phase7_asset_acquisition_source_and_reconciliation

Phase 7 — Fixed Asset (Master Roadmap §24).

Bagian 1 — Tambah kolom acquisition source trace ke tabel aset_tetap:
- acquisition_source_type (VARCHAR 30, nullable)
  Tipe sumber akuisisi: 'MANUAL_JOURNAL' | 'PURCHASE_INVOICE' | 'DIRECT' | null
- acquisition_source_id (UUID, nullable)
  ID sumber akuisisi (mis. jurnal_umum.id atau purchase_invoice.id)
- acquisition_source_no (VARCHAR 30, nullable)
  Nomor dokumen sumber untuk display (mis. JV-2026-09-001 atau PINV-2026-09-001)

Sesuai Roadmap §24: "Acquisition source trace" — setiap aset tetap harus
bisa ditelusuri sumber akuisisinya.

Bagian 2 — Tambah kolom acquisition_date ke aset_tetap:
- acquisition_date (DATE, nullable)
  Tanggal akuisisi aset (bisa beda dari tanggal_mulai penyusutan)

Backward compatibility: semua kolom baru nullable, default NULL.
Tidak ada backfill data — kolom baru nullable, default NULL.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # BAGIAN 1 — Acquisition source trace fields
    # ==========================================
    op.add_column(
        'aset_tetap',
        sa.Column('acquisition_source_type', sa.String(30), nullable=True)
    )
    op.add_column(
        'aset_tetap',
        sa.Column('acquisition_source_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        'aset_tetap',
        sa.Column('acquisition_source_no', sa.String(30), nullable=True)
    )
    op.add_column(
        'aset_tetap',
        sa.Column('acquisition_date', sa.Date, nullable=True)
    )

    # Index untuk query cepat: "aset dari sumber X"
    op.create_index(
        'ix_aset_tetap_acquisition_source_type',
        'aset_tetap',
        ['acquisition_source_type'],
    )
    op.create_index(
        'ix_aset_tetap_acquisition_source_id',
        'aset_tetap',
        ['acquisition_source_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_aset_tetap_acquisition_source_id', table_name='aset_tetap')
    op.drop_index('ix_aset_tetap_acquisition_source_type', table_name='aset_tetap')
    op.drop_column('aset_tetap', 'acquisition_date')
    op.drop_column('aset_tetap', 'acquisition_source_no')
    op.drop_column('aset_tetap', 'acquisition_source_id')
    op.drop_column('aset_tetap', 'acquisition_source_type')
