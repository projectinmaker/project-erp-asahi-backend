"""Allow reuse of inactive master codes.

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
"""

from alembic import op
import sqlalchemy as sa

revision = "l3m4n5o6p7q8"
down_revision = "k2l3m4n5o6p7"
branch_labels = None
depends_on = None

TABLES = ("pelanggan", "supplier", "barang", "kas_bank_akun")


def upgrade() -> None:
    for table in TABLES:
        op.drop_index(f"ix_{table}_kode", table_name=table)
        op.create_index(
            f"ix_{table}_kode",
            table,
            ["kode"],
            unique=True,
            postgresql_where=sa.text("status = 'AKTIF'"),
        )


def downgrade() -> None:
    # Resolve reused codes first. PostgreSQL rejects the global unique index
    # if duplicates remain and rolls back this transactional migration.
    # Never delete/rename historical records automatically.
    for table in TABLES:
        op.drop_index(f"ix_{table}_kode", table_name=table)
        op.create_index(f"ix_{table}_kode", table, ["kode"], unique=True)
