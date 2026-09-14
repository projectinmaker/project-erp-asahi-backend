"""asahi_coa_revisi_v2

COA ASAHI FINAL REVISI — Phase 1: Schema migration.

Tambah kolom baru di tabel akun_perkiraan sesuai target architecture dari
workbook COA_SYSTEM_MASTER (lihat CATATAN_IMPORT_COA_ASAHI.md section 9).

Kolom baru (semua nullable / punya default supaya aman untuk data existing):
- account_class         VARCHAR(20)   ASSET/LIABILITY/EQUITY/REVENUE/COGS/EXPENSE
- account_subclass      VARCHAR(50)   CASH_BANK/ACCOUNTS_RECEIVABLE/...
- allow_system_posting  BOOLEAN       default TRUE
- allow_manual_posting  BOOLEAN       default TRUE
- is_control_account    BOOLEAN       default FALSE
- subledger_type        VARCHAR(30)   AR/AP/INVENTORY/BANK_TRANSFER (nullable)
- financial_statement   VARCHAR(20)   NERACA/LABA RUGI
- report_group          VARCHAR(50)   CURRENT_ASSET/COGS/...
- system_account_type   VARCHAR(50)   AR_CONTROL/AP_CONTROL/BANK_CLEARING/...
- reconciliation_required BOOLEAN     default FALSE
- active                BOOLEAN       default TRUE (canonical, sync dari status)

Catatan:
- Existing kolom `header` (enum) dan `tingkat` (enum) dan `status` (VARCHAR)
  tetap dipertahankan untuk backward compatibility. Service layer yang
  meng-bridge keduanya (lihat app/services/coa_service.py).
- Tidak ada data backfill di migration ini — pemetaan data existing ke
  field baru dijalankan terpisah via endpoint POST /coa/apply-migration
  (lihat app/services/coa_migration_service.py).

Revision ID: t1u2v3w4x5y6
Revises: s1t2u3v4w5x6
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "t1u2v3w4x5y6"
down_revision: Union[str, Sequence[str], None] = "s1t2u3v4w5x6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Kolom kontrol posting (default TRUE supaya existing akun tetap postable)
    op.add_column(
        "akun_perkiraan",
        sa.Column(
            "allow_system_posting",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "akun_perkiraan",
        sa.Column(
            "allow_manual_posting",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    # 2. Kolom control account & subledger
    op.add_column(
        "akun_perkiraan",
        sa.Column(
            "is_control_account",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "akun_perkiraan",
        sa.Column("subledger_type", sa.String(30), nullable=True),
    )

    # 3. Kolom klasifikasi & reporting
    op.add_column(
        "akun_perkiraan",
        sa.Column("account_class", sa.String(20), nullable=True),
    )
    op.add_column(
        "akun_perkiraan",
        sa.Column("account_subclass", sa.String(50), nullable=True),
    )
    op.add_column(
        "akun_perkiraan",
        sa.Column("financial_statement", sa.String(20), nullable=True),
    )
    op.add_column(
        "akun_perkiraan",
        sa.Column("report_group", sa.String(50), nullable=True),
    )
    op.add_column(
        "akun_perkiraan",
        sa.Column("system_account_type", sa.String(50), nullable=True),
    )

    # 4. Kolom reconciliation
    op.add_column(
        "akun_perkiraan",
        sa.Column(
            "reconciliation_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # 5. Kolom `active` canonical (default TRUE), sync dari status existing
    op.add_column(
        "akun_perkiraan",
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    # Backfill: status = 'NONAKTIF' -> active = FALSE
    op.execute(
        "UPDATE akun_perkiraan SET active = FALSE WHERE status = 'NONAKTIF'"
    )

    # 6. Index untuk query yang sering dipakai (filter control account, subledger)
    op.create_index(
        "ix_akun_perkiraan_is_control_account",
        "akun_perkiraan",
        ["is_control_account"],
    )
    op.create_index(
        "ix_akun_perkiraan_subledger_type",
        "akun_perkiraan",
        ["subledger_type"],
    )
    op.create_index(
        "ix_akun_perkiraan_account_class",
        "akun_perkiraan",
        ["account_class"],
    )
    op.create_index(
        "ix_akun_perkiraan_active",
        "akun_perkiraan",
        ["active"],
    )

    # 7. Setting akun keys baru (BANK_CLEARING, COGS_FINISHED_GOODS, dll)
    # Dihandle di app/seed/phase3_setting_akun_seed.py — tidak perlu DDL karena
    # tabel setting_akun sudah ada (FK ke akun_perkiraan).


def downgrade() -> None:
    op.drop_index("ix_akun_perkiraan_active", table_name="akun_perkiraan")
    op.drop_index("ix_akun_perkiraan_account_class", table_name="akun_perkiraan")
    op.drop_index("ix_akun_perkiraan_subledger_type", table_name="akun_perkiraan")
    op.drop_index(
        "ix_akun_perkiraan_is_control_account", table_name="akun_perkiraan"
    )

    for col in (
        "active",
        "reconciliation_required",
        "system_account_type",
        "report_group",
        "financial_statement",
        "account_subclass",
        "account_class",
        "subledger_type",
        "is_control_account",
        "allow_manual_posting",
        "allow_system_posting",
    ):
        op.drop_column("akun_perkiraan", col)
