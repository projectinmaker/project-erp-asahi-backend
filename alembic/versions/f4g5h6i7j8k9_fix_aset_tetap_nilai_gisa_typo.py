"""fix_aset_tetap_nilai_gisa_typo

Fix typo di migrasi a1b2c3d4e5f6 (phase5_transaction_tables): kolom
"nilai_gisa" seharusnya "nilai_sisa" (salvage value), sesuai ORM model
AsetTetap.nilai_sisa (app/models/transaksi/aset_tetap/aset_tetap.py).

Tanpa fix ini, setiap query ORM full-entity AsetTetap gagal dengan
psycopg2.errors.UndefinedColumn: column aset_tetap.nilai_sisa does not exist
(memicu HTTP 500 di /aset-tetap/rekonsiliasi/register-vs-gl dan meracuni
session di /laporan/accounting-health).

Renama kolom mempertahankan data (jika ada).

Revision ID: f4g5h6i7j8k9
Revises: e1f2a3b4c5d6
Create Date: 2026-09-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "f4g5h6i7j8k9"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind) -> set:
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns("aset_tetap")}


def upgrade() -> None:
    cols = _columns(op.get_bind())
    if "nilai_gisa" in cols and "nilai_sisa" not in cols:
        op.alter_column(
            "aset_tetap",
            "nilai_gisa",
            new_column_name="nilai_sisa",
            existing_type=sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        )


def downgrade() -> None:
    cols = _columns(op.get_bind())
    if "nilai_sisa" in cols and "nilai_gisa" not in cols:
        op.alter_column(
            "aset_tetap",
            "nilai_sisa",
            new_column_name="nilai_gisa",
            existing_type=sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("0"),
        )
