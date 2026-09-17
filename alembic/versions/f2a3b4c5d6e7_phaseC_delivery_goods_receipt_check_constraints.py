"""Phase C: Delivery & Goods Receipt — CHECK constraints for qty > 0

Revision ID: f2a3b4c5d6e7
Revises: d0e1f2a3b4c5
Create Date: 2026-09-16

Catatan Audit Delivery §10 & Catatan Audit Goods Receipt §10:
    "Validation minimum: qty > 0
     Gunakan validation di:
       1. API schema;        ← Phase C code fix (Field gt=0)
       2. service/backend;   ← Phase C code fix (explicit qty>0 checks)
       3. database CHECK constraint bila sesuai.  ← THIS MIGRATION"

This migration adds database-level CHECK constraints to enforce qty > 0 on:
- pengiriman_barang_detail.qty
- penerimaan_barang_detail.qty

These complement (not replace) the schema-level and service-level validations.
Defense-in-depth: even if a future bug bypasses Python validation, the DB will reject.

Chain position: inserted BETWEEN Phase B (d0e1f2a3b4c5) and Phase D (e1f2a3b4c5d6).
- Phase B adalah current head di DB user saat ini.
- Phase C (this migration) chains langsung setelah Phase B, jadi bisa di-apply
  tanpa harus apply Phase D lebih dulu.
- Phase D (e1f2a3b4c5d6) down_revision di-update untuk chain setelah Phase C,
  menjaga rantai tetap linear: Phase B → Phase C → Phase D.

NOTE: This migration is idempotent — uses CREATE CONSTRAINT IF NOT EXISTS pattern
via raw SQL with try/except for cross-PostgreSQL-version compatibility.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ==========================================
# CHECK constraints (Phase C — qty > 0)
# ==========================================
# Naming convention: ck_<table>_<rule>
# Use IF NOT EXISTS guard via DO $$ ... EXCEPTION WHEN duplicate_object
# for cross-version PostgreSQL compatibility (PG < 9.5 doesn't support IF NOT EXISTS on constraints).

_CHECK_PENGIRIMAN_QTY_POSITIVE = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_pengiriman_barang_detail_qty_positive'
          AND conrelid = 'pengiriman_barang_detail'::regclass
    ) THEN
        ALTER TABLE pengiriman_barang_detail
            ADD CONSTRAINT ck_pengiriman_barang_detail_qty_positive CHECK (qty > 0) NOT VALID;
    END IF;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;
"""

_CHECK_PENERIMAAN_QTY_POSITIVE = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_penerimaan_barang_detail_qty_positive'
          AND conrelid = 'penerimaan_barang_detail'::regclass
    ) THEN
        ALTER TABLE penerimaan_barang_detail
            ADD CONSTRAINT ck_penerimaan_barang_detail_qty_positive CHECK (qty > 0) NOT VALID;
    END IF;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;
"""

# Backfill comment: NOT VALID means existing rows are NOT re-validated.
# This is intentional — historical rows with qty=0 (if any) should be cleaned
# up manually by IT before re-enabling validation. The Python service layer
# already enforces qty > 0 for all new writes (Phase C fix).


def upgrade() -> None:
    # === Phase C — CHECK constraints qty > 0 ===
    op.execute(_CHECK_PENGIRIMAN_QTY_POSITIVE)
    op.execute(_CHECK_PENERIMAAN_QTY_POSITIVE)


def downgrade() -> None:
    # Drop constraints (use IF EXISTS for safety)
    op.execute("""
        ALTER TABLE pengiriman_barang_detail
            DROP CONSTRAINT IF EXISTS ck_pengiriman_barang_detail_qty_positive
    """)
    op.execute("""
        ALTER TABLE penerimaan_barang_detail
            DROP CONSTRAINT IF EXISTS ck_penerimaan_barang_detail_qty_positive
    """)
