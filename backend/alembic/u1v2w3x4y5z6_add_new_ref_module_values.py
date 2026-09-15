"""add_new_ref_module_values

Phase 1 — RefModule refactor (Master Roadmap §8: RefModule).

Tambah nilai baru ke PostgreSQL enum type `refmodule` agar service code yang
baru dapat memakai nama-nama canonical per target architecture ASAHI:

    SALES_DELIVERY
    PURCHASE_RECEIPT
    AR_SETTLEMENT
    AP_SETTLEMENT
    INVENTORY_ADJUSTMENT
    INVENTORY_TRANSFER
    ASSET_CAPITALIZATION
    ASSET_DEPRECIATION
    ASSET_DISPOSAL
    BANK_TRANSFER
    BANK_RECONCILIATION

Catatan:
- PostgreSQL enum ADD VALUE bersifat **irreversible** di dalam transaksi (tidak
  bisa di-rollback di luar blok ALTER TYPE ... ADD VALUE yang dijalankan setelah
  commit). Karena itu migration ini menggunakan pattern standar Alembic dengan
  `op.execute()` di luar transaksi.
- Nilai-nilai lama (PEMBAYARAN, PENERIMAAN, PENYESUAIAN_STOK, PENYUSUTAN,
  TRANSFER_BANK, REKONSILIASI_BANK) **tetap dipertahankan** di enum type supaya
  historical data yang sudah pakai nilai lama tidak rusak. Backfill data
  historis dijalankan di migration terpisah (`backfill_ref_module_jurnal`).
- Karena ALTER TYPE ... ADD VALUE tidak bisa dijalankan di dalam transaksi,
  downgrade untuk DROP value tidak didukung oleh PostgreSQL < 12. Di PostgreSQL
  12+, DROP VALUE ada tapi masih experimental dan tetap tidak direkomendasikan.
  Downgrade migration ini hanya akan memberi pesan informatif.

IMPORTANT — PostgreSQL enum transaction quirk:
- ALTER TYPE ... ADD VALUE must run OUTSIDE a transaction block.
- PostgreSQL 12+ also requires that new enum values are COMMITTED before they
  can be used in DML (UPDATE/INSERT). If you add a value and try to use it in
  the same transaction, you get: `UnsafeNewEnumValueUsage: unsafe use of new
  value "X" of enum type Y`.
- Alembic by default runs all upgrades in a single transaction. To work
  around this, we use `with op.get_context().autocommit_block():` which:
  1. Commits the current transaction
  2. Runs the ALTER TYPE ADD VALUE in autocommit mode (outside transaction)
  3. Begins a new transaction

  Reference: https://alembic.sqlalchemy.org/en/latest/api/operations.html
             #alembic.operations.context.MigrationContext.autocommit_block

Revision ID: u1v2w3x4y5z6
Revises: u2v3w4x5y6z7
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op


revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, Sequence[str], None] = "u2v3w4x5y6z7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Nama enum type di DB. SQLAlchemy Enum class default menggunakan nama class
# lowercase sebagai type name, jadi `RefModule` -> `refmodule`.
# (Diverifikasi via psql: \dT+ refmodule)
ENUM_TYPE_NAME = "refmodule"

# Nilai-nilai baru yang akan ditambahkan (urutan penting untuk ADD VALUE
# BEFORE/AFTER opsional — default adalah append ke akhir).
NEW_VALUES = [
    "SALES_DELIVERY",
    "PURCHASE_RECEIPT",
    "AR_SETTLEMENT",
    "AP_SETTLEMENT",
    "INVENTORY_ADJUSTMENT",
    "INVENTORY_TRANSFER",
    "ASSET_CAPITALIZATION",
    "ASSET_DEPRECIATION",
    "ASSET_DISPOSAL",
    "BANK_TRANSFER",
    "BANK_RECONCILIATION",
]


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE tidak bisa dijalankan di dalam transaction block
    # di PostgreSQL. Selain itu, PostgreSQL 12+ mensyaratkan bahwa value enum
    # yang baru di-add harus di-COMMIT dulu sebelum bisa dipakai di DML
    # (UPDATE/INSERT). Kalau tidak, akan kena error:
    #   "UnsafeNewEnumValueUsage: unsafe use of new value X of enum type Y"
    #   HINT: New enum values must be committed before they can be used.
    #
    # Karena migration selanjutnya (v2w3x4y5z6a7_backfill_ref_module_jurnal)
    # langsung memakai enum-enum ini di UPDATE statement, kita HARUS pakai
    # autocommit_block() supaya ALTER TYPE ADD VALUE di-commit terpisah dari
    # transaction Alembic yang sedang berjalan.
    #
    # Reference: https://alembic.sqlalchemy.org/en/latest/cookbook.html#run-alter-type-in-autocommit-mode  # noqa: E501
    with op.get_context().autocommit_block():
        for value in NEW_VALUES:
            # IF NOT EXISTS didukung sejak PostgreSQL 9.3 untuk ALTER TYPE ADD VALUE
            op.execute(
                f"ALTER TYPE {ENUM_TYPE_NAME} ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    # PostgreSQL tidak mengizinkan DROP VALUE dari enum type kecuali di
    # PostgreSQL 12+ dengan syntax experimental. Bahkan di PG 12+, DROP VALUE
    # memerlukan restart server untuk menghindari cache.
    #
    # Sebagai gantinya, kita beri pesan informatif. Untuk benar-benar rollback,
    # diperlukan recreate enum type yang melibatkan banyak tabel (jurnal_umum,
    # stok_mutasi, dll) — risiko terlalu tinggi untuk dilakukan otomatis.
    #
    # Praktik yang aman: gunakan `alembic stamp` untuk skip migration ini saat
    # rollback, dan biarkan nilai enum baru tetap ada (tidak merusak apa pun).
    print(
        f"[DOWNGRADE] Migration {revision} tidak didukung untuk rollback otomatis. "
        f"Nilai-nilai enum baru ({', '.join(NEW_VALUES)}) tetap ada di type "
        f"{ENUM_TYPE_NAME}. Untuk rollback penuh, lakukan manual: recreate "
        f"enum type tanpa nilai-nilai baru, lalu UPDATE kolom-kolom yang "
        f"memakai nilai baru kembali ke nilai legacy. Hanya lakukan ini di "
        f"environment development kosong."
    )
