"""add_missing_tanggal_akun_perkiraan

Fix schema drift: kolom `tanggal` sudah lama ada di model SQLAlchemy
(app/models/akun_perkiraan.py) tapi tidak pernah dibuat lewat migration
manapun sejak migration awal (1f869e1d16af_initial_master_and_jurnal_tables).

Akibatnya SELECT * dari model AkunPerkiraan gagal dengan:
    psycopg2.errors.UndefinedColumn: column akun_perkiraan.tanggal does not exist

Ditemukan saat menjalankan `coa_revision_import.py --preview` di atas database
yang baru dibuat ulang dari nol (fresh alembic upgrade head, sebelum fix ini).

Kolom dibuat nullable karena tidak ada default value yang jelas untuk data
existing (dan juga sesuai definisi model: nullable=True).

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "u2v3w4x5y6z7"
down_revision: Union[str, Sequence[str], None] = "t1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "akun_perkiraan",
        sa.Column(
            "tanggal",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("akun_perkiraan", "tanggal")
