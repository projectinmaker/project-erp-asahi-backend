"""phase5c_rekonsiliasi_bank

Phase 5c — Tabel rekonsiliasi_bank dan rekonsiliasi_bank_detail.

Revision ID: c5d6e7f8a9
Revises: b2c3d4e5f6
Create Date: 2026-09-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tambah enum REKONSILIASI_BANK ke refmodule
    op.execute("ALTER TYPE refmodule ADD VALUE IF NOT EXISTS 'REKONSILIASI_BANK'")

    # 2. Buat tabel rekonsiliasi_bank (header)
    op.create_table(
        "rekonsiliasi_bank",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("kas_bank_akun_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tanggal_akhir", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saldo_bank", sa.Numeric(18, 2), nullable=False),
        sa.Column("saldo_buku", sa.Numeric(18, 2), nullable=False),
        sa.Column("selisih", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(20), server_default="DRAFT", nullable=False),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("jurnal_penyesuaian_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["kas_bank_akun_id"], ["kas_bank_akun.id"]),
        sa.ForeignKeyConstraint(["jurnal_penyesuaian_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.UniqueConstraint("kas_bank_akun_id", "tanggal_akhir", name="uq_rekonsiliasi_bank_kas_tanggal"),
    )

    # 3. Buat tabel rekonsiliasi_bank_detail (lines)
    op.create_table(
        "rekonsiliasi_bank_detail",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("rekonsiliasi_bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipe", sa.String(20), nullable=False),
        sa.Column("keterangan", sa.Text(), nullable=False),
        sa.Column("jumlah", sa.Numeric(18, 2), nullable=False),
        sa.Column("sisi", sa.String(10), nullable=False),
        sa.Column("akun_perkiraan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["rekonsiliasi_bank_id"], ["rekonsiliasi_bank.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["akun_perkiraan_id"], ["akun_perkiraan.id"]),
    )


def downgrade() -> None:
    op.drop_table("rekonsiliasi_bank_detail")
    op.drop_table("rekonsiliasi_bank")
