"""update043_supplier_singkatan

Update #043 — kolom ``supplier.singkatan`` (mirror pelanggan #003):

- String(20) nullable — singkatan nama perusahaan supplier.
- Auto-terisi dari nama saat field kosong (``generate_singkatan`` di
  app/utils/nomor_dokumen.py, dipanggil master_service create/update);
  isian user selalu dipertahankan.
- Murni data master — TIDAK dipakai segmen nomor dokumen mana pun
  (format penomoran existing tidak berubah).

Downgrade: kolom singkatan di-drop.

Revision ID: cp0000000031
Revises: cp0000000030
Create Date: 2026-11-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cp0000000031"
down_revision: Union[str, Sequence[str], None] = "cp0000000030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Singkatan nama perusahaan supplier (auto-fill dari nama bila kosong)
    op.add_column(
        "supplier",
        sa.Column("singkatan", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplier", "singkatan")
