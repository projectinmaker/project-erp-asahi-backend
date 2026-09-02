"""phase5b_penutupan_periode

Phase 5b — Tabel penutupan_periode untuk lock periode akuntansi.

Revision ID: b2c3d4e5f6c9
Revises: a1b2c3d4e5f6
Create Date: 2026-09-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6c9'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tambah enum PENUTUPAN_PERIODE ke refmodule (append)
    # Gunakan ALTER TYPE untuk menambah value baru ke enum yang sudah ada
    op.execute("ALTER TYPE refmodule ADD VALUE IF NOT EXISTS 'PENUTUPAN_PERIODE'")

    # 2. Buat tabel penutupan_periode
    op.create_table(
        "penutupan_periode",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tahun", sa.Integer(), nullable=False),
        sa.Column("bulan", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="DITUTUP", nullable=False),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("jurnal_penutupan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("laba_rugi", sa.Numeric(18, 2), nullable=True),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["jurnal_penutupan_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["closed_by"], ["pengguna.id"]),
        sa.ForeignKeyConstraint(["reopened_by"], ["pengguna.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.UniqueConstraint("tahun", "bulan", name="uq_penutupan_periode_tahun_bulan"),
    )


def downgrade() -> None:
    op.drop_table("penutupan_periode")
    # Note: tidak bisa menghapus enum value di PostgreSQL tanpa recreate type
