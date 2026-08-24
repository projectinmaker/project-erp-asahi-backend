"""fix_jurnal_tanggal_to_datetime

Mengubah kolom jurnal_umum.tanggal dari VARCHAR(10) menjadi TIMESTAMP WITH TIME ZONE.
Root cause: Migration awal (1f869e1d16af) membuat kolom sebagai sa.String(length=10),
sementara model SQLAlchemy sudah menggunakan DateTime(timezone=True).

Revision ID: b2c3d4e5f6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-24 22:30:00.000000

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ubah tipe kolom dari VARCHAR(10) ke TIMESTAMP WITH TIME ZONE
    # Data yang sudah ada (format 'YYYY-MM-DD') otomatis di-cast oleh PostgreSQL
    op.execute("""
        ALTER TABLE jurnal_umum
        ALTER COLUMN tanggal TYPE TIMESTAMP WITH TIME ZONE
        USING to_timestamp(tanggal, 'YYYY-MM-DD') AT TIME ZONE 'Asia/Jakarta';
    """)

    # Pastikan kolom tetap NOT NULL
    op.alter_column('jurnal_umum', 'tanggal', nullable=False)


def downgrade() -> None:
    # Kembalikan ke VARCHAR(10) jika perlu rollback
    op.execute("""
        ALTER TABLE jurnal_umum
        ALTER COLUMN tanggal TYPE VARCHAR(10)
        USING to_char(tanggal AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD');
    """)
    op.alter_column('jurnal_umum', 'tanggal', nullable=False)
