"""update5_penalti_pelunasan

Update #5 — fitur penalti di pelunasan piutang/hutang.

Tabel penerimaan_kas & pembayaran_kas masing-masing mendapat:
- kolom penalti NUMERIC(18,2) NOT NULL server_default '0'
- kolom akun_penalti_id UUID NULL, FK ke akun_perkiraan.id (RESTRICT)

Penalti ikut menambah total_nilai pelunasan (total = Σ alokasi + penalti)
dan diposting sebagai baris jurnal tambahan (piutang: Cr akun penalti;
hutang: Dr akun penalti) lewat rincian kas/bank.

Revision ID: c2d3e4f5g6h7
Revises: b1c2d3e4f5g6
Create Date: 2026-10-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5g6h7'
down_revision: str | Sequence[str] | None = 'b1c2d3e4f5g6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ('penerimaan_kas', 'pembayaran_kas')


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column('penalti', sa.Numeric(18, 2), server_default='0', nullable=False),
        )
        op.add_column(
            table,
            sa.Column('akun_penalti_id', postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            f'fk_{table}_akun_penalti',
            table,
            'akun_perkiraan',
            ['akun_penalti_id'],
            ['id'],
            ondelete='RESTRICT',
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f'fk_{table}_akun_penalti', table, type_='foreignkey')
        op.drop_column(table, 'akun_penalti_id')
        op.drop_column(table, 'penalti')
