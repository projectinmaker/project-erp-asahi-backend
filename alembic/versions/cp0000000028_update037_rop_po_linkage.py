"""update037_rop_po_linkage

Update #037 — ROP ↔ Purchase Order linkage (alur belanja dari ROP):

1. ``request_order_part_item`` + ``barang_id`` (FK barang.id, SET NULL, index)
   — baris part ROP bisa NUAT ke master barang sehingga stok gudang
   terbaca akurat (item yang sudah ada di master tidak perlu diketik ulang;
   baris tanpa link diperlakukan "tidak ada di gudang" = stok 0).
2. ``purchase_order_detail`` + ``rop_item_id`` (FK request_order_part_item.id,
   SET NULL, index) — jejak baris PO berasal dari baris ROP mana. Satu PO
   boleh memuat item dari beberapa ROP (relasi per baris, pola sales_order_id
   update #002).

Derivation status belanja (tanpa kolom tambahan):
- "sudah dibelanjakan" per item ROP = SUM(qty) baris PO aktif
  (status != DIBATALKAN) yang menaut item tersebut → PO dibatalkan/dihapus
  otomatis mengembalikan item ke "belum dibelanjakan" (permintaan user).
- kekurangan = qty diminta − stok tersedia (beli selisihnya saja).

Backward compatibility: kedua kolom nullable → ROP/PO lama tidak berubah.

Revision ID: cp0000000028
Revises: cp0000000027
Create Date: 2026-11-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "cp0000000028"
down_revision: Union[str, Sequence[str], None] = "cp0000000027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Baris ROP → master barang (stok akurat + dasar cek gudang)
    op.add_column(
        "request_order_part_item",
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_rop_item_barang",
        "request_order_part_item",
        "barang",
        ["barang_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_request_order_part_item_barang_id"),
        "request_order_part_item",
        ["barang_id"],
        unique=False,
    )

    # 2. Baris PO → baris ROP sumber (jejak belanja dari ROP)
    op.add_column(
        "purchase_order_detail",
        sa.Column("rop_item_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_po_detail_rop_item",
        "purchase_order_detail",
        "request_order_part_item",
        ["rop_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_purchase_order_detail_rop_item_id"),
        "purchase_order_detail",
        ["rop_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_purchase_order_detail_rop_item_id"),
        table_name="purchase_order_detail",
    )
    op.drop_constraint("fk_po_detail_rop_item", "purchase_order_detail", type_="foreignkey")
    op.drop_column("purchase_order_detail", "rop_item_id")
    op.drop_index(
        op.f("ix_request_order_part_item_barang_id"),
        table_name="request_order_part_item",
    )
    op.drop_constraint("fk_rop_item_barang", "request_order_part_item", type_="foreignkey")
    op.drop_column("request_order_part_item", "barang_id")
