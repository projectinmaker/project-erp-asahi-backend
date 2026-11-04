"""update031_penawaran_quotation_so_termin

Update #031 — Penawaran "No Quotation" + item bebas + termin pembayaran SO.

1. Tabel baru ``penawaran_katalog_item`` — katalog item BEBAS penawaran
   (tidak masuk master data; item berulang tinggal dipilih lagi).
2. Tabel baru ``sales_order_termin`` — termin pembayaran Sales Order
   (baris nominal, mis. 3x pembayaran; dibayar via Penerimaan jenis
   TERMIN_SO, link penerimaan_kas_id dilepas otomatis saat dibatalkan).
3. ``penawaran_detail``: ``barang_id`` kini NULLABLE + kolom baru
   ``katalog_id``, ``item_nama``, ``item_satuan`` — baris penawaran bisa
   berupa item bebas yang diketik manual (belum ada di master barang).

Backward compatibility:
- Semua baris penawaran lama punya barang_id terisi → tidak ada backfill.
- SO lama tanpa termin → tabel termin kosong untuk mereka (properti
  termin_total/termin_dibayar mengembalikan 0).
- No Quotation baru berformat ``QUOT ASI/001/MJE/X/2026`` — dokumen lama
  ``PEN ASI/YYYY/NNN`` tidak ter-match pattern sequence baru (parse ketat).

Revision ID: cp0000000025
Revises: cp0000000024
Create Date: 2026-11-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "cp0000000025"
down_revision: Union[str, Sequence[str], None] = "cp0000000024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # 1. Tabel katalog item penawaran (item bebas)
    # ==========================================
    op.create_table(
        "penawaran_katalog_item",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nama", sa.String(200), nullable=False, index=True),
        sa.Column("satuan_nama", sa.String(50), nullable=True),
        sa.Column("harga", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("dipakai_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("keterangan", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ==========================================
    # 2. Tabel termin pembayaran Sales Order
    # ==========================================
    op.create_table(
        "sales_order_termin",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sales_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sales_order.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("urutan", sa.Integer(), nullable=False),
        sa.Column("nominal", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "penerimaan_kas_id",
            UUID(as_uuid=True),
            sa.ForeignKey("penerimaan_kas.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("dibayar_pada", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("nominal > 0", name="ck_so_termin_positive"),
    )

    # ==========================================
    # 3. penawaran_detail — item bebas (barang_id nullable + kolom item)
    # ==========================================
    op.alter_column("penawaran_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.add_column("penawaran_detail", sa.Column("katalog_id", UUID(as_uuid=True), sa.ForeignKey("penawaran_katalog_item.id"), nullable=True))
    op.add_column("penawaran_detail", sa.Column("item_nama", sa.String(200), nullable=True))
    op.add_column("penawaran_detail", sa.Column("item_satuan", sa.String(50), nullable=True))


def downgrade() -> None:
    # Baris bebas tidak bisa dikembalikan ke barang_id NOT NULL tanpa data —
    # hapus baris bebas lebih dulu (manual) bila benar-benar perlu downgrade.
    op.drop_column("penawaran_detail", "item_satuan")
    op.drop_column("penawaran_detail", "item_nama")
    op.drop_column("penawaran_detail", "katalog_id")
    op.alter_column("penawaran_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.drop_table("sales_order_termin")
    op.drop_table("penawaran_katalog_item")
