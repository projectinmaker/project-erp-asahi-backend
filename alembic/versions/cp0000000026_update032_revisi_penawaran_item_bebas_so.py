"""update032_revisi_penawaran_item_bebas_so

Update #032 — Revisi Penawaran (Rev. N) + item bebas sampai SO/Invoice +
repeat order (satu penawaran dipakai beberapa SO).

1. ``penawaran``:
   - kolom baru ``revisi_ke`` (INT NOT NULL, default 1; 2 = Rev. 2, dst).
   - ``no_penawaran`` dilebarkan 30 → 60 dan TIDAK lagi unik sendirian
     (dokumen revisi memakai NOMOR SAMA) — index unik lama
     ``ix_penawaran_no_penawaran`` di-drop, diganti index biasa + index
     unik komposit (no_penawaran, revisi_ke).
2. ``sales_order``:
   - kolom baru ``penawaran_id`` (FK penawaran.id ON DELETE SET NULL,
     nullable, index) — link SO ke penawaran sumber. Repeat order: satu
     penawaran boleh dipakai banyak SO.
   - BACKFILL: SO hasil konversi #031 yang keterangannya berawalan
     "Dari Penawaran <no>" di-link ke penawaran bersangkutan supaya
     counter "dipakai N× SO" langsung akurat untuk data lama.
3. ``sales_order_detail`` + ``sales_invoice_detail``:
   - ``barang_id`` jadi NULLABLE + kolom baru ``item_nama`` (200) dan
     ``item_satuan`` (50) — baris item BEBAS (jasa/custom dari penawaran;
     tidak ada di master barang). SO/invoice lama semua barang_id terisi →
     tanpa backfill. Pengiriman barang tetap wajib barang master
     (pengiriman_barang_detail TIDAK diubah).

Downgrade: item bebas tidak bisa dikembalikan ke barang_id NOT NULL tanpa
data — hapus baris bebas lebih dulu (manual) bila benar-benar perlu
downgrade; link penawaran_id SO lama akan ter-backfill ulang kosong.

Revision ID: cp0000000026
Revises: cp0000000025
Create Date: 2026-11-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "cp0000000026"
down_revision: Union[str, Sequence[str], None] = "cp0000000025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # 1. penawaran — revisi_ke + nomor boleh kembar antar revisi
    # ==========================================
    op.add_column("penawaran", sa.Column("revisi_ke", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("penawaran", "no_penawaran", existing_type=sa.String(30), type_=sa.String(60))

    # Index unik lama (dibuat migrasi k9m2n4p6q8r0) di-drop: dokumen revisi
    # memakai nomor sama. Di-upgrade ke: index biasa (lookup) + unik komposit
    # (no_penawaran, revisi_ke).
    op.drop_index("ix_penawaran_no_penawaran", table_name="penawaran")
    op.create_index("ix_penawaran_no_penawaran", "penawaran", ["no_penawaran"], unique=False)
    op.create_index("uq_penawaran_no_rev", "penawaran", ["no_penawaran", "revisi_ke"], unique=True)

    # ==========================================
    # 2. sales_order — link penawaran sumber (repeat order)
    # ==========================================
    op.add_column(
        "sales_order",
        sa.Column(
            "penawaran_id",
            UUID(as_uuid=True),
            sa.ForeignKey("penawaran.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_sales_order_penawaran_id", "sales_order", ["penawaran_id"])

    # Backfill: SO konversi #031 menandai sumbernya di keterangan dengan awalan
    # "Dari Penawaran <no_penawaran>" — tautkan balik ke penawaran tsb supaya
    # counter pemakaian ("dipakai N× SO") akurat untuk data existing.
    # Pencocokan prefix aman: format nomor ketat 5 segmen dan diakhiri tahun;
    # saat migrasi ini jalan belum ada dokumen revisi (nomor kembar) sehingga
    # join tidak ambigu.
    op.execute(
        """
        UPDATE sales_order AS so
        SET penawaran_id = p.id
        FROM penawaran AS p
        WHERE so.penawaran_id IS NULL
          AND so.keterangan LIKE 'Dari Penawaran ' || p.no_penawaran || '%'
        """
    )

    # ==========================================
    # 3. Item bebas di baris SO dan Invoice
    # ==========================================
    op.alter_column("sales_order_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.add_column("sales_order_detail", sa.Column("item_nama", sa.String(200), nullable=True))
    op.add_column("sales_order_detail", sa.Column("item_satuan", sa.String(50), nullable=True))

    op.alter_column("sales_invoice_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.add_column("sales_invoice_detail", sa.Column("item_nama", sa.String(200), nullable=True))
    op.add_column("sales_invoice_detail", sa.Column("item_satuan", sa.String(50), nullable=True))


def downgrade() -> None:
    # Baris bebas tidak bisa dikembalikan ke barang_id NOT NULL tanpa data —
    # hapus baris bebas lebih dulu (manual) bila benar-benar perlu downgrade.
    op.drop_column("sales_invoice_detail", "item_satuan")
    op.drop_column("sales_invoice_detail", "item_nama")
    op.alter_column("sales_invoice_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=False)

    op.drop_column("sales_order_detail", "item_satuan")
    op.drop_column("sales_order_detail", "item_nama")
    op.alter_column("sales_order_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=False)

    op.drop_index("ix_sales_order_penawaran_id", table_name="sales_order")
    op.drop_column("sales_order", "penawaran_id")

    op.drop_index("uq_penawaran_no_rev", table_name="penawaran")
    op.drop_index("ix_penawaran_no_penawaran", table_name="penawaran")
    op.create_index("ix_penawaran_no_penawaran", "penawaran", ["no_penawaran"], unique=True)
    op.alter_column("penawaran", "no_penawaran", existing_type=sa.String(60), type_=sa.String(30))
    op.drop_column("penawaran", "revisi_ke")
