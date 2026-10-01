"""update3_penawaran_drop_fob_potongan_setting

Update #3 — Modul Penawaran (quotation), penghapusan field FOB,
dan seed setting akun POTONGAN_PENJUALAN.

1. create_table penawaran + penawaran_detail (blueprint SalesOrder:
   header + detail + biaya tambahan; TANPA jurnal, TANPA workflow).
2. add_column transaksi_biaya.penawaran_id (FK ke penawaran.id).
3. drop_column sales_order.fob & sales_invoice.fob (field dihapus total
   dari model/schema/service/endpoint).
4. SEED setting_akun key='POTONGAN_PENJUALAN' -> akun 411005
   (idempotent: skip bila key sudah ada; skip bila akun 411005 tak ditemukan).

Revision ID: b8c9d0e1f2a3
Revises: w5x6y7z8a9b0
Create Date: 2026-10-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'k9m2n4p6q8r0'
down_revision: str | Sequence[str] | None = 'w5x6y7z8a9b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ==========================================
    # 1. Tabel penawaran + penawaran_detail
    # ==========================================
    op.create_table(
        "penawaran",
        sa.Column("no_penawaran", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("berlaku_hingga", sa.Date(), nullable=True),
        sa.Column("pelanggan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("syarat_bayar_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alamat_pengiriman", sa.String(255), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("mata_uang", sa.String(10), server_default="IDR", nullable=False),
        sa.Column("diskon_global", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("ppn", sa.Numeric(5, 2), server_default="11", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_diskon", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_ppn", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_biaya_tambahan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["pelanggan_id"], ["pelanggan.id"], name="fk_penawaran_pelanggan"),
        sa.ForeignKeyConstraint(["syarat_bayar_id"], ["syarat_bayar.id"], name="fk_penawaran_syarat_bayar"),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"], name="fk_penawaran_created_by"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_penawaran_no_penawaran"), "penawaran", ["no_penawaran"], unique=True)

    op.create_table(
        "penawaran_detail",
        sa.Column("penawaran_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("satuan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("harga", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("diskon", sa.Numeric(5, 2), server_default="0", nullable=True),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("keterangan", sa.String(255), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["penawaran_id"], ["penawaran.id"], name="fk_penawaran_detail_penawaran"),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"], name="fk_penawaran_detail_barang"),
        sa.ForeignKeyConstraint(["satuan_id"], ["satuan.id"], name="fk_penawaran_detail_satuan"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ==========================================
    # 2. transaksi_biaya.penawaran_id
    # ==========================================
    op.add_column(
        "transaksi_biaya",
        sa.Column("penawaran_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_transaksi_biaya_penawaran", "transaksi_biaya", "penawaran",
        ["penawaran_id"], ["id"], ondelete="RESTRICT",
    )

    # ==========================================
    # 3. Drop field FOB (SalesOrder & SalesInvoice)
    # ==========================================
    op.drop_column("sales_order", "fob")
    op.drop_column("sales_invoice", "fob")

    # ==========================================
    # 4. SEED setting_akun POTONGAN_PENJUALAN -> 411005
    #    (idempotent: skip bila key sudah ada / akun tak ditemukan)
    # ==========================================
    op.execute(
        """
        INSERT INTO setting_akun (id, key, label, akun_perkiraan_id, created_at, updated_at)
        SELECT gen_random_uuid(), 'POTONGAN_PENJUALAN', 'Potongan Penjualan', ap.id, now(), now()
        FROM akun_perkiraan ap
        WHERE ap.kode = '411005'
          AND NOT EXISTS (
              SELECT 1 FROM setting_akun sa WHERE sa.key = 'POTONGAN_PENJUALAN'
          )
        """
    )


def downgrade() -> None:
    # 4. Hapus seed POTONGAN_PENJUALAN
    op.execute("DELETE FROM setting_akun WHERE key = 'POTONGAN_PENJUALAN'")

    # 3. Kembalikan kolom fob
    op.add_column("sales_invoice", sa.Column("fob", sa.String(50), nullable=True))
    op.add_column("sales_order", sa.Column("fob", sa.String(50), nullable=True))

    # 2. Hapus transaksi_biaya.penawaran_id
    op.drop_constraint("fk_transaksi_biaya_penawaran", "transaksi_biaya", type_="foreignkey")
    op.drop_column("transaksi_biaya", "penawaran_id")

    # 1. Hapus tabel penawaran_detail & penawaran
    op.drop_table("penawaran_detail")
    op.drop_index(op.f("ix_penawaran_no_penawaran"), table_name="penawaran")
    op.drop_table("penawaran")
