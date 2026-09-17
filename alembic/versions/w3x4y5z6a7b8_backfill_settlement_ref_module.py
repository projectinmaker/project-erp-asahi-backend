"""backfill_settlement_ref_module

Phase 1.B — Distinguish AR/AP Settlement from generic Penerimaan/Pembayaran.

Sesuai Master Roadmap §8 & §15 & §21:
- Generic payment (expense / advance)         → RefModule.PEMBAYARAN  (legacy)
- AP settlement (pelunasan hutang ke supplier) → RefModule.AP_SETTLEMENT
- Generic receipt (other income / refund)     → RefModule.PENERIMAAN (legacy)
- AR settlement (pelunasan piutang pelanggan)  → RefModule.AR_SETTLEMENT

Backfill ini meng-identifikasi historical jurnal PENERIMAAN/PEMBAYARAN yang
**sebenarnya adalah settlement** (karena punya allocation ke invoice via
payment_allocation table) dan mengubah ref_module-nya ke AR_SETTLEMENT /
AP_SETTLEMENT supaya konsisten dengan code baru.

Logic:
1. Cari semua PembayaranKas / PenerimaanKas yang punya row di payment_allocation
2. Untuk PenerimaanKas dengan allocation → ref_module = AR_SETTLEMENT
3. Untuk PembayaranKas dengan allocation → ref_module = AP_SETTLEMENT
4. Update jurnal_umum yang ref_id nunjuk ke pembayaran/penerimaan tersebut

Migration ini idempotent (aman dijalankan ulang).

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "w3x4y5z6a7b8"
down_revision: Union[str, Sequence[str], None] = "v2w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Penting: pakai autocommit_block() supaya tidak kena masalah
    # "UnsafeNewEnumValueUsage" kalau migration ini di-run sebelum enum value
    # AR_SETTLEMENT / AP_SETTLEMENT ter-commit (kasus rare, tapi untuk safety).
    with op.get_context().autocommit_block():
        # ==========================================
        # 1. PENERIMAAN (with allocation) → AR_SETTLEMENT
        # ==========================================
        # PenerimaanKas yang punya allocation ke sales_invoice → ini settlement AR.
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'AR_SETTLEMENT'
            WHERE ref_module = 'PENERIMAAN'
              AND ref_id IS NOT NULL
              AND ref_id IN (
                  SELECT DISTINCT penerimaan_id
                  FROM payment_allocation
                  WHERE penerimaan_id IS NOT NULL
              )
            """
        )

        # ==========================================
        # 2. PEMBAYARAN (with allocation) → AP_SETTLEMENT
        # ==========================================
        # PembayaranKas yang punya allocation ke purchase_invoice → ini settlement AP.
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'AP_SETTLEMENT'
            WHERE ref_module = 'PEMBAYARAN'
              AND ref_id IS NOT NULL
              AND ref_id IN (
                  SELECT DISTINCT pembayaran_id
                  FROM payment_allocation
                  WHERE pembayaran_id IS NOT NULL
              )
            """
        )


def downgrade() -> None:
    # Reverse: kembalikan AR_SETTLEMENT/AP_SETTLEMENT ke legacy enum.
    # Catatan: ini akan mengembalikan ke bug lama (settlement terlihat sebagai
    # generic receipt/payment), hanya lakukan kalau yakin ingin rollback.
    with op.get_context().autocommit_block():
        op.execute(
            "UPDATE jurnal_umum SET ref_module = 'PENERIMAAN' "
            "WHERE ref_module = 'AR_SETTLEMENT'"
        )
        op.execute(
            "UPDATE jurnal_umum SET ref_module = 'PEMBAYARAN' "
            "WHERE ref_module = 'AP_SETTLEMENT'"
        )
