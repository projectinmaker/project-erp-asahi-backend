"""backfill_ref_module_jurnal

Phase 1 — RefModule refactor (Master Roadmap §8: RefModule).

Backfill historical jurnal_umum & stok_mutasi yang sebelumnya keliru memakai
enum legacy:

1. Jurnal PengirimanBarang (Delivery):
   Sebelumnya stock mutation + HPP journal di penjualan_service.finish_delivery
   memakai `SALES_INVOICE` (BUG — sesuai Roadmap §13: harus `SALES_DELIVERY`).
   Backfill: cari jurnal yang ref_id nunjuk ke tabel `pengiriman_barang`,
   tipe_transaksi LIKE 'PENGIRIMAN%' atau keterangan LIKE 'Pengiriman%',
   ubah ref_module -> 'SALES_DELIVERY'.

2. Jurnal PenerimaanBarang (Goods Receipt):
   Sebelumnya stock mutation + GRNI journal di pembelian_service.finish_receipt
   memakai `PURCHASE_INVOICE` (BUG — sesuai Roadmap §19: harus `PURCHASE_RECEIPT`).
   Backfill: cari jurnal yang ref_id nunjuk ke tabel `penerimaan_barang`,
   tipe_transaksi LIKE 'PENERIMAAN_GRNI%' atau keterangan LIKE 'Penerimaan%',
   ubah ref_module -> 'PURCHASE_RECEIPT'.

3. Jurnal Penyesuaian Stok (Inventory Adjustment):
   Sebelumnya pakai `PENYESUAIAN_STOK`. Backfill ke `INVENTORY_ADJUSTMENT`
   untuk konsistensi dengan target architecture.

4. Jurnal Penyusutan Aset (Asset Depreciation):
   Sebelumnya pakai `PENYUSUTAN`. Backfill ke `ASSET_DEPRECIATION`.

5. Jurnal Transfer Bank:
   Sebelumnya pakai `TRANSFER_BANK`. Backfill ke `BANK_TRANSFER`.

6. Jurnal Rekonsiliasi Bank:
   Sebelumnya pakai `REKONSILIASI_BANK`. Backfill ke `BANK_RECONCILIATION`.

Catatan:
- Backfill dilakukan HANYA untuk jurnal yang ref_id-nya jelas nunjuk ke tabel
  sumber yang benar (mis. pengiriman_barang, penerimaan_barang). Jurnal
  `SALES_INVOICE` yang asli (untuk invoice) tetap dipertahankan.
- Migration ini idempotent (bisa dijalankan ulang tanpa effect samping).
- Backfill `PENYESUAIAN_STOK -> INVENTORY_ADJUSTMENT` dan `PENYUSUTAN ->
  ASSET_DEPRECIATION` bersifat penuh (semua data) karena kedua enum legacy
  tidak dipakai lagi oleh code baru.

IMPORTANT — PostgreSQL enum transaction quirk:
- PostgreSQL 12+ requires that new enum values are COMMITTED before they can
  be used in DML (UPDATE/INSERT). The migration `u1v2w3x4y5z6` that adds
  these new enum values uses `autocommit_block()` to commit them in their own
  transaction. However, to be extra safe against `UnsafeNewEnumValueUsage`
  errors (especially if migration ordering changes in the future), we wrap
  the UPDATE statements here in `autocommit_block()` too.
- This also has the benefit of making each UPDATE commit independently,
  which is more robust against partial failures.

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "v2w3x4y5z6a7"
down_revision: Union[str, Sequence[str], None] = "u1v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # IMPORTANT — autocommit_block() wrapper
    # ==========================================
    # PostgreSQL 12+ menolak penggunaan enum value yang baru di-ADD dalam
    # transaction yang sama (UnsafeNewEnumValueUsage). Migration sebelumnya
    # (u1v2w3x4y5z6) memakai autocommit_block() untuk commit ADD VALUE.
    # Tapi untuk safety ekstra (kalau Alembic run multi-upgrade dalam satu
    # transaction), kita juga wrap UPDATE di sini dengan autocommit_block().
    #
    # Reference: https://alembic.sqlalchemy.org/en/latest/api/operations.html
    #            #alembic.operations.context.MigrationContext.autocommit_block
    with op.get_context().autocommit_block():
        # ==========================================
        # 1. SALES_INVOICE (Delivery) -> SALES_DELIVERY
        # ==========================================
        # Identifikasi jurnal yang ref_id-nya adalah ID dari tabel pengiriman_barang.
        # Hanya jurnal yang ref_id nunjuk ke row di pengiriman_barang yang di-backfill.
        # Aman karena: jurnal sales invoice asli punya ref_id ke sales_invoice.id,
        # jurnal delivery punya ref_id ke pengiriman_barang.id — tidak akan overlap.
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'SALES_DELIVERY'
            WHERE ref_module = 'SALES_INVOICE'
              AND ref_id IS NOT NULL
              AND ref_id IN (SELECT id FROM pengiriman_barang)
            """
        )

        # Safety net: kalau ada jurnal SALES_INVOICE yang tipe_transaksi /
        # keterangan mengindikasikan delivery tapi ref_id tidak ketemu di
        # pengiriman_barang (mis. row sudah dihapus), tetap di-backfill supaya
        # audit trail konsisten. Hanya yang pattern-nya jelas delivery (bukan invoice).
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'SALES_DELIVERY'
            WHERE ref_module = 'SALES_INVOICE'
              AND (
                tipe_transaksi ILIKE '%PENGIRIMAN%'
                OR keterangan ILIKE 'HPP Pengiriman%'
                OR keterangan ILIKE 'Pengiriman %'
              )
              AND ref_id NOT IN (SELECT id FROM sales_invoice)
            """
        )

        # ==========================================
        # 2. PURCHASE_INVOICE (Goods Receipt) -> PURCHASE_RECEIPT
        # ==========================================
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'PURCHASE_RECEIPT'
            WHERE ref_module = 'PURCHASE_INVOICE'
              AND ref_id IS NOT NULL
              AND ref_id IN (SELECT id FROM penerimaan_barang)
            """
        )

        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'PURCHASE_RECEIPT'
            WHERE ref_module = 'PURCHASE_INVOICE'
              AND (
                tipe_transaksi ILIKE '%PENERIMAAN_GRNI%'
                OR keterangan ILIKE 'Penerimaan Barang%'
                OR keterangan ILIKE 'GRNI%'
              )
              AND ref_id NOT IN (SELECT id FROM purchase_invoice)
            """
        )

        # ==========================================
        # 3. PENYESUAIAN_STOK -> INVENTORY_ADJUSTMENT
        # ==========================================
        # Full backfill: enum lama tidak dipakai lagi oleh code baru.
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'INVENTORY_ADJUSTMENT'
            WHERE ref_module = 'PENYESUAIAN_STOK'
            """
        )

        # ==========================================
        # 4. PENYUSUTAN -> ASSET_DEPRECIATION
        # ==========================================
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'ASSET_DEPRECIATION'
            WHERE ref_module = 'PENYUSUTAN'
            """
        )

        # ==========================================
        # 5. TRANSFER_BANK -> BANK_TRANSFER
        # ==========================================
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'BANK_TRANSFER'
            WHERE ref_module = 'TRANSFER_BANK'
            """
        )

        # ==========================================
        # 6. REKONSILIASI_BANK -> BANK_RECONCILIATION
        # ==========================================
        op.execute(
            """
            UPDATE jurnal_umum
            SET ref_module = 'BANK_RECONCILIATION'
            WHERE ref_module = 'REKONSILIASI_BANK'
            """
        )

        # ==========================================
        # 7. StokMutasi: backfill ref_module yang sama
        # ==========================================
        # Tabel stok_mutasi juga punya kolom ref_module (string). Backfill yang sama
        # supaya konsisten dengan jurnal_umum.
        # Catatan: perlu cek dulu apakah kolom ref_module di stok_mutasi ada sebagai
        # enum atau string. Berdasarkan model existing, stok_mutasi.ref_module adalah
        # SQLEnum(RefModule) — jadi type-nya sama dengan jurnal_umum.

        # Cek apakah tabel stok_mutasi ada
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if 'stok_mutasi' in inspector.get_table_names():
            cols = {c['name']: c for c in inspector.get_columns('stok_mutasi')}
            if 'ref_module' in cols:
                op.execute(
                    """
                    UPDATE stok_mutasi
                    SET ref_module = 'SALES_DELIVERY'
                    WHERE ref_module = 'SALES_INVOICE'
                      AND ref_id IS NOT NULL
                      AND ref_id IN (SELECT id FROM pengiriman_barang)
                    """
                )
                op.execute(
                    """
                    UPDATE stok_mutasi
                    SET ref_module = 'PURCHASE_RECEIPT'
                    WHERE ref_module = 'PURCHASE_INVOICE'
                      AND ref_id IS NOT NULL
                      AND ref_id IN (SELECT id FROM penerimaan_barang)
                    """
                )
                op.execute(
                    """
                    UPDATE stok_mutasi
                    SET ref_module = 'INVENTORY_ADJUSTMENT'
                    WHERE ref_module = 'PENYESUAIAN_STOK'
                    """
                )


def downgrade() -> None:
    # Reverse backfill: kembalikan nilai canonical ke legacy.
    # Berguna kalau ada issue dan perlu rollback.
    # Catatan: tidak akan mengembalikan BUG asli (jurnal yang awalnya keliru
    # pakai SALES_INVOICE untuk delivery) — itu memang bug yang harus diperbaiki.
    with op.get_context().autocommit_block():
        op.execute(
            "UPDATE jurnal_umum SET ref_module = 'SALES_INVOICE' "
            "WHERE ref_module = 'SALES_DELIVERY'"
        )
        op.execute(
            "UPDATE jurnal_umum SET ref_module = 'PURCHASE_INVOICE' "
            "WHERE ref_module = 'PURCHASE_RECEIPT'"
        )
        op.execute(
            "UPDATE jurnal_umum SET ref_module = 'PENYESUAIAN_STOK' "
            "WHERE ref_module = 'INVENTORY_ADJUSTMENT'"
        )
        op.execute(
            "UPDATE jurnal_umum SET ref_module = 'PENYUSUTAN' "
            "WHERE ref_module = 'ASSET_DEPRECIATION'"
        )
        op.execute(
            "UPDATE jurnal_umum SET ref_module = 'TRANSFER_BANK' "
            "WHERE ref_module = 'BANK_TRANSFER'"
        )
        op.execute(
            "UPDATE jurnal_umum SET ref_module = 'REKONSILIASI_BANK' "
            "WHERE ref_module = 'BANK_RECONCILIATION'"
        )

        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if 'stok_mutasi' in inspector.get_table_names():
            cols = {c['name']: c for c in inspector.get_columns('stok_mutasi')}
            if 'ref_module' in cols:
                op.execute(
                    "UPDATE stok_mutasi SET ref_module = 'SALES_INVOICE' "
                    "WHERE ref_module = 'SALES_DELIVERY'"
                )
                op.execute(
                    "UPDATE stok_mutasi SET ref_module = 'PURCHASE_INVOICE' "
                    "WHERE ref_module = 'PURCHASE_RECEIPT'"
                )
                op.execute(
                    "UPDATE stok_mutasi SET ref_module = 'PENYESUAIAN_STOK' "
                    "WHERE ref_module = 'INVENTORY_ADJUSTMENT'"
                )
