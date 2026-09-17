"""phaseA_supplier_master_and_inventory_enhancements

Phase A — Foundations update based on audit notes.

Bagian 1 — Supplier Master (file 15): tambah 10 field baru
- supplier_type (VARCHAR 20, nullable) — COMPANY / INDIVIDUAL
- city (VARCHAR 100, nullable)
- province (VARCHAR 100, nullable)
- country (VARCHAR 100, nullable)
- postal_code (VARCHAR 10, nullable)
- bank_name (VARCHAR 100, nullable)
- bank_account_no (VARCHAR 50, nullable)
- bank_account_name (VARCHAR 200, nullable)
- currency (VARCHAR 3, nullable, default 'IDR')
- supplier_name_raw (VARCHAR 200, nullable) — for migration audit

Bagian 2 — Inventory Engine (file 8): FEFO adjustment expiry
- penyesuaian_stok.tanggal_kedaluwarsa (DATE, nullable) — for FEFO adjustment TAMBAH

Backward compatibility: semua kolom baru nullable, default NULL (kecuali currency='IDR').
Tidak ada backfill data.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # Bagian 1 — Supplier Master: tambah 10 field baru
    # ==========================================
    op.add_column('supplier', sa.Column('supplier_type', sa.String(20), nullable=True))
    op.add_column('supplier', sa.Column('city', sa.String(100), nullable=True))
    op.add_column('supplier', sa.Column('province', sa.String(100), nullable=True))
    op.add_column('supplier', sa.Column('country', sa.String(100), nullable=True))
    op.add_column('supplier', sa.Column('postal_code', sa.String(10), nullable=True))
    op.add_column('supplier', sa.Column('bank_name', sa.String(100), nullable=True))
    op.add_column('supplier', sa.Column('bank_account_no', sa.String(50), nullable=True))
    op.add_column('supplier', sa.Column('bank_account_name', sa.String(200), nullable=True))
    op.add_column('supplier', sa.Column('currency', sa.String(3), nullable=True, server_default='IDR'))
    op.add_column('supplier', sa.Column('supplier_name_raw', sa.String(200), nullable=True))

    # Index for enhanced search
    op.create_index('ix_supplier_npwp', 'supplier', ['npwp'])
    op.create_index('ix_supplier_supplier_type', 'supplier', ['supplier_type'])
    op.create_index('ix_supplier_country', 'supplier', ['country'])

    # ==========================================
    # Bagian 2 — PenyesuaianStok: tambah tanggal_kedaluwarsa untuk FEFO
    # ==========================================
    op.add_column(
        'penyesuaian_stok',
        sa.Column('tanggal_kedaluwarsa', sa.Date(), nullable=True)
    )


def downgrade() -> None:
    # Bagian 2
    op.drop_column('penyesuaian_stok', 'tanggal_kedaluwarsa')

    # Bagian 1
    op.drop_index('ix_supplier_country', table_name='supplier')
    op.drop_index('ix_supplier_supplier_type', table_name='supplier')
    op.drop_index('ix_supplier_npwp', table_name='supplier')
    op.drop_column('supplier', 'supplier_name_raw')
    op.drop_column('supplier', 'currency')
    op.drop_column('supplier', 'bank_account_name')
    op.drop_column('supplier', 'bank_account_no')
    op.drop_column('supplier', 'bank_name')
    op.drop_column('supplier', 'postal_code')
    op.drop_column('supplier', 'country')
    op.drop_column('supplier', 'province')
    op.drop_column('supplier', 'city')
    op.drop_column('supplier', 'supplier_type')
