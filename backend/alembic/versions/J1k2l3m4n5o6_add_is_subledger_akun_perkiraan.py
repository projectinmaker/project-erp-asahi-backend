"""add_is_subledger_akun_perkiraan

Tambah kolom is_subledger di akun_perkiraan. True untuk COA detail yang
auto-created per pelanggan/supplier (subledger "Piutang - {nama}" /
"Hutang - {nama}"), supaya bisa disembunyikan dari modul Akun Perkiraan/COA
tanpa menghapus datanya (tetap dipakai untuk jurnal).

Revision ID: j1k2l3m4n5o6
Revises: i9j0k1l2m3n4
Create Date: 2026-09-08 15:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'j1k2l3m4n5o6'
down_revision: Union[str, Sequence[str], None] = 'i9j0k1l2m3n4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'akun_perkiraan',
        sa.Column('is_subledger', sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Tandai retroactively: COA detail yang merupakan anak dari group/header
    # "Piutang Usaha" atau "Hutang Usaha" (pola auto-create existing) sebagai
    # subledger, supaya data lama ikut ke-hide juga tanpa perlu re-seed manual.
    op.execute("""
        UPDATE akun_perkiraan
        SET is_subledger = TRUE
        WHERE induk_id IN (
            SELECT id FROM akun_perkiraan
            WHERE (nama ILIKE '%PIUTANG%USAHA%' OR nama ILIKE '%HUTANG%USAHA%')
              AND tingkat IN ('GROUP', 'HEADER')
        )
        AND tingkat = 'DETAIL'
    """)


def downgrade() -> None:
    op.drop_column('akun_perkiraan', 'is_subledger')