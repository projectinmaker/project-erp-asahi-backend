"""update045_tukar_faktur_item_bebas

=== Update #045 — Tukar Faktur menerima baris ITEM BEBAS ===

Bugfix NotNullViolation: create_tukar_faktur gagal saat invoice berisi baris
ITEM BEBAS (jasa/custom dari penawaran → SO → invoice) karena kolom
``tukar_faktur_detail.barang_id`` masih NOT NULL.

Perbaikan (mirror pola #032 SO/invoice & #041 pengiriman):
- ``tukar_faktur_detail.barang_id``  → NULLABLE
- ``tukar_faktur_detail.satuan_id``  → tetap nullable (sudah)
- kolom baru ``item_nama``   String(200) NULL — snapshot nama item bebas
- kolom baru ``item_satuan`` String(50)  NULL — snapshot satuan item bebas

Downgrade: drop kolom item_nama/item_satuan lalu kembalikan barang_id ke
NOT NULL (baris item bebas harus dihapus manual lebih dulu bila ada).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "cp0000000033"
down_revision: Union[str, Sequence[str], None] = "cp0000000032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # barang_id jadi opsional — baris ITEM BEBAS tidak punya referensi master.
    op.alter_column("tukar_faktur_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=True)
    # Snapshot nama & satuan item bebas (mirror sales_invoice_detail #032).
    op.add_column("tukar_faktur_detail", sa.Column("item_nama", sa.String(200), nullable=True))
    op.add_column("tukar_faktur_detail", sa.Column("item_satuan", sa.String(50), nullable=True))


def downgrade() -> None:
    # Baris item bebas harus dihapus manual lebih dulu bila benar-benar
    # perlu mengembalikan barang_id ko NOT NULL.
    op.drop_column("tukar_faktur_detail", "item_satuan")
    op.drop_column("tukar_faktur_detail", "item_nama")
    op.alter_column("tukar_faktur_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=False)
