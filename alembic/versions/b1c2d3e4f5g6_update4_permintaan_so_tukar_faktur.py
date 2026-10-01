"""update4_permintaan_so_tukar_faktur

Update #4 — kolom sales_order_id di permintaan_barang + modul Tukar Faktur.

1. add_column permintaan_barang.sales_order_id (UUID NULL, FK ke sales_order.id)
   — link opsional permintaan barang ke Sales Order sumber.
2. create_table tukar_faktur + tukar_faktur_detail (proof of receipt:
   snapshot header invoice + copy detail; TANPA jurnal, TANPA stok,
   TANPA workflow).

Revision ID: b1c2d3e4f5g6
Revises: k9m2n4p6q8r0
Create Date: 2026-10-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5g6'
down_revision: str | Sequence[str] | None = 'k9m2n4p6q8r0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ==========================================
    # 1. permintaan_barang.sales_order_id
    # ==========================================
    op.add_column(
        "permintaan_barang",
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_permintaan_barang_sales_order", "permintaan_barang", "sales_order",
        ["sales_order_id"], ["id"], ondelete="RESTRICT",
    )

    # ==========================================
    # 2. Tabel tukar_faktur + tukar_faktur_detail
    # ==========================================
    op.create_table(
        "tukar_faktur",
        sa.Column("no_tukar_faktur", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sales_invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pelanggan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("no_so", sa.String(30), nullable=True),
        sa.Column("no_po_customer", sa.String(50), nullable=True),
        sa.Column("no_surat_jalan", sa.String(255), nullable=True),
        sa.Column("total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["sales_invoice_id"], ["sales_invoice.id"], name="fk_tukar_faktur_sales_invoice"),
        sa.ForeignKeyConstraint(["pelanggan_id"], ["pelanggan.id"], name="fk_tukar_faktur_pelanggan"),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"], name="fk_tukar_faktur_created_by"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tukar_faktur_no_tukar_faktur"), "tukar_faktur", ["no_tukar_faktur"], unique=True)

    op.create_table(
        "tukar_faktur_detail",
        sa.Column("tukar_faktur_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("satuan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tukar_faktur_id"], ["tukar_faktur.id"], name="fk_tukar_faktur_detail_tukar_faktur"),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"], name="fk_tukar_faktur_detail_barang"),
        sa.ForeignKeyConstraint(["satuan_id"], ["satuan.id"], name="fk_tukar_faktur_detail_satuan"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    # 2. Hapus tabel tukar_faktur_detail & tukar_faktur
    op.drop_table("tukar_faktur_detail")
    op.drop_index(op.f("ix_tukar_faktur_no_tukar_faktur"), table_name="tukar_faktur")
    op.drop_table("tukar_faktur")

    # 1. Hapus permintaan_barang.sales_order_id
    op.drop_constraint("fk_permintaan_barang_sales_order", "permintaan_barang", type_="foreignkey")
    op.drop_column("permintaan_barang", "sales_order_id")
