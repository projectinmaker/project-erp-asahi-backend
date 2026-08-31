"""phase1_pelanggan_supplier_npwp

Phase 1 perubahan:
1. Pelanggan: akun_piutang_id jadi nullable, tambah kolom npwp
2. Supplier: akun_hutang_id jadi nullable, tambah kolom npwp

Alasan: Field akun piutang/hutang di-takedown dari form (diganti NPWP).
Kolom DB tetap dipertahankan (nullable) karena masih direferensi oleh
penjualan_service & pembelian_service untuk journal posting.
Phase 3 akan mengganti mekanisme ini dengan COA-based linkage.

Revision ID: d4e5f6a7b8
Revises: c3d4e5f6a7
Create Date: 2026-08-31 10:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pelanggan: akun_piutang_id jadi nullable + tambah npwp
    op.alter_column('pelanggan', 'akun_piutang_id', nullable=True)
    op.add_column('pelanggan', sa.Column('npwp', sa.String(length=50), nullable=True))

    # Supplier: akun_hutang_id jadi nullable + tambah npwp
    op.alter_column('supplier', 'akun_hutang_id', nullable=True)
    op.add_column('supplier', sa.Column('npwp', sa.String(length=50), nullable=True))


def downgrade() -> None:
    # Hapus npwp dari supplier & pelanggan
    op.drop_column('supplier', 'npwp')
    op.drop_column('pelanggan', 'npwp')

    # Kembalikan NOT NULL
    op.alter_column('supplier', 'akun_hutang_id', nullable=False)
    op.alter_column('pelanggan', 'akun_piutang_id', nullable=False)
