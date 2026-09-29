"""add saldo_awal to refmodule enum

Fix: nilai enum `SALDO_AWAL` ada di model Python (RefModule.SALDO_AWAL) dan
dipakai oleh coa_service (_active_opening_jurnal / save_saldo_awal), tapi
tidak pernah ditambahkan ke enum type PostgreSQL `refmodule` oleh migrasi
manapun:

- 1f869e1d16af (initial) membuat enum TANPA 'SALDO_AWAL'.
- u1v2w3x4y5z6 menambah nilai canonical baru, juga tanpa 'SALDO_AWAL'.
- g7h8i9j0k1l2 memakai 'SALDO_AWAL' di definisi kolom stok_kartu_layer dengan
  create_type=False — nilai tidak pernah di-ALTER ke enum type bersama.

Akibatnya database hasil `alembic upgrade head` dari nol akan error
`InvalidTextRepresentation: invalid input value for enum refmodule: "SALDO_AWAL"`
saat GET/POST /coa/saldo-awal (dan query lain yang memfilter RefModule.SALDO_AWAL).

Migrasi ini idempotent: ADD VALUE IF NOT EXISTS.

Revision ID: sa0000000002
Revises: hd0000000001
Create Date: 2026-09-29
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'sa0000000002'
down_revision = 'hd0000000001'
branch_labels = None
depends_on = None

ENUM_TYPE_NAME = 'refmodule'
NEW_VALUES = ['SALDO_AWAL']


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE tidak bisa dijalankan di dalam transaction block.
    # Pakai autocommit_block() supaya nilai baru di-commit sebelum dipakai DML
    # (pattern sama dengan u1v2w3x4y5z6_add_new_ref_module_values).
    with op.get_context().autocommit_block():
        for value in NEW_VALUES:
            op.execute(
                f"ALTER TYPE {ENUM_TYPE_NAME} ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    # PostgreSQL tidak mendukung DROP VALUE enum (kecuali experimental di PG 12+).
    # Downgrade bersifat informatif saja — sama seperti u1v2w3x4y5z6.
    pass
