"""phase5_transaction_tables

Phase 5 — Semua tabel transaksi: Kas/Bank, Penjualan, Pembelian,
Persediaan, Aset Tetap, Stok Mutasi, dan Transaksi Biaya.

Revision ID: a1b2c3d4e5f6
Revises: 1f869e1d16af
Create Date: 2026-08-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '1f869e1d16af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ==========================================
# ENUM TYPE DEFINITIONS (new)
# ==========================================
NEW_ENUMS = [
    ("statustransaksi", ["DRAFT", "SELESAI", "BATAL"]),
    ("statuspenjualan", ["DRAFT", "DIPROSES", "SELESAI", "DIBATALKAN"]),
    ("statuspersediaan", ["DIAJUKAN", "DISETUJUI", "DITOLAK", "SELESAI", "BATAL"]),
    ("tipepenyesuaian", ["TAMBAH", "KURANG"]),
    ("prosespemindahan", ["KIRIM", "TERIMA"]),
    ("metodepenyusutan", ["GARIS_LURUS", "SALDO_MENURUN"]),
    ("statusasettetap", ["AKTIF", "DIHAPUSKAN", "DALAM_PERBAIKAN"]),
    ("tipemutasistok", ["MASUK", "KELUAR", "PENYESUAIAN_TAMBAH", "PENYESUAIAN_KURANG", "PEMINDAHAN_KELUAR", "PEMINDAHAN_MASUK"]),
]

# Existing enum (created in initial migration)
EXISTING_REFMODULE = postgresql.ENUM(
    "PEMBAYARAN", "PENERIMAAN", "TRANSFER_BANK",
    "SALES_ORDER", "SALES_INVOICE", "SALES_RETUR",
    "PURCHASE_ORDER", "PURCHASE_INVOICE", "PURCHASE_RETUR",
    "PENYESUAIAN_STOK", "PENYUSUTAN", "MANUAL",
    name="refmodule", create_type=False,
)


# ==========================================
# HELPER — common columns (id, created_at, updated_at)
# ==========================================
def _pk() -> list:
    """Return common primary-key + timestamp columns."""
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _create_enum(enum_name, values):
    vals = ", ".join(f"'{v}'" for v in values)
    op.execute(f"CREATE TYPE {enum_name} AS ENUM ({vals})")


def _drop_enum(enum_name):
    op.execute(f"DROP TYPE IF EXISTS {enum_name}")


def upgrade() -> None:
    """Create all Phase 5 transaction tables."""

    # --------------------------------------------------
    # 1. CREATE ENUM TYPES
    # --------------------------------------------------
    for name, values in NEW_ENUMS:
        _create_enum(name, values)

    # Shorthand for enum references
    E_STS = postgresql.ENUM(name="statustransaksi", create_type=False)
    E_SPN = postgresql.ENUM(name="statuspenjualan", create_type=False)
    E_SPR = postgresql.ENUM(name="statuspersediaan", create_type=False)
    E_TPA = postgresql.ENUM(name="tipepenyesuaian", create_type=False)
    E_PPB = postgresql.ENUM(name="prosespemindahan", create_type=False)
    E_MPY = postgresql.ENUM(name="metodepenyusutan", create_type=False)
    E_SAT = postgresql.ENUM(name="statusasettetap", create_type=False)
    E_TMS = postgresql.ENUM(name="tipemutasistok", create_type=False)

    PK = _pk  # shortcut

    # ==================================================
    # KAS & BANK
    # ==================================================

    # 1a. pembayaran_kas
    op.create_table(
        "pembayaran_kas",
        sa.Column("no_bukti", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kas_bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("no_nukti", sa.String(50), nullable=False),
        sa.Column("no_cek", sa.String(50), nullable=True),
        sa.Column("penerima", sa.String(255), nullable=True),
        sa.Column("catatan", sa.Text(), nullable=True),
        sa.Column("total_nilai", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", E_STS, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["kas_bank_id"], ["kas_bank_akun.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pembayaran_kas_no_bukti"), "pembayaran_kas", ["no_bukti"], unique=True)

    # 1b. pembayaran_rincian
    op.create_table(
        "pembayaran_rincian",
        sa.Column("pembayaran_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("akun_perkiraan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nilai", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["pembayaran_id"], ["pembayaran_kas.id"]),
        sa.ForeignKeyConstraint(["akun_perkiraan_id"], ["akun_perkiraan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 1c. penerimaan_kas
    op.create_table(
        "penerimaan_kas",
        sa.Column("no_bukti", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kas_bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("no_nukti", sa.String(50), nullable=False),
        sa.Column("no_cek", sa.String(50), nullable=True),
        sa.Column("pemberi", sa.String(255), nullable=True),
        sa.Column("catatan", sa.Text(), nullable=True),
        sa.Column("total_nilai", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", E_STS, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["kas_bank_id"], ["kas_bank_akun.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_penerimaan_kas_no_bukti"), "penerimaan_kas", ["no_bukti"], unique=True)

    # 1d. penerimaan_rincian
    op.create_table(
        "penerimaan_rincian",
        sa.Column("penerimaan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("akun_perkiraan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nilai", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["penerimaan_id"], ["penerimaan_kas.id"]),
        sa.ForeignKeyConstraint(["akun_perkiraan_id"], ["akun_perkiraan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 1e. transfer_bank
    op.create_table(
        "transfer_bank",
        sa.Column("no_transfer", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dari_kas_bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ke_kas_bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nilai_transfer", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("biaya_transfer", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("informasi", sa.Text(), nullable=True),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", E_STS, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["dari_kas_bank_id"], ["kas_bank_akun.id"]),
        sa.ForeignKeyConstraint(["ke_kas_bank_id"], ["kas_bank_akun.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_transfer_bank_no_transfer"), "transfer_bank", ["no_transfer"], unique=True)

    # ==================================================
    # PENJUALAN
    # ==================================================

    # 2a. sales_order
    op.create_table(
        "sales_order",
        sa.Column("no_pesanan", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("syarat_bayar_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fob", sa.String(50), nullable=True),
        sa.Column("ekspedisi", sa.String(100), nullable=True),
        sa.Column("tanggal_pengiriman", sa.DateTime(timezone=True), nullable=True),
        sa.Column("penjual", sa.String(100), nullable=True),
        sa.Column("pelanggan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alamat_pengiriman", sa.Text(), nullable=True),
        sa.Column("diskon_global", sa.Numeric(5, 2), nullable=True),
        sa.Column("ppn", sa.Numeric(5, 2), server_default="11", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_diskon", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_ppn", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_biaya_tambahan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPN, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["syarat_bayar_id"], ["syarat_bayar.id"]),
        sa.ForeignKeyConstraint(["pelanggan_id"], ["pelanggan.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sales_order_no_pesanan"), "sales_order", ["no_pesanan"], unique=True)

    # 2b. sales_order_detail
    op.create_table(
        "sales_order_detail",
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("harga", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("diskon", sa.Numeric(5, 2), nullable=True),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2c. sales_invoice
    op.create_table(
        "sales_invoice",
        sa.Column("no_invoice", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("syarat_bayar_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fob", sa.String(50), nullable=True),
        sa.Column("ekspedisi", sa.String(100), nullable=True),
        sa.Column("tanggal_pengiriman", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pelanggan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alamat_pengiriman", sa.Text(), nullable=True),
        sa.Column("mata_uang", sa.String(10), server_default="IDR", nullable=False),
        sa.Column("diskon_global", sa.Numeric(5, 2), nullable=True),
        sa.Column("ppn", sa.Numeric(5, 2), server_default="11", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_diskon", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_ppn", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_biaya_tambahan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPN, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["syarat_bayar_id"], ["syarat_bayar.id"]),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.ForeignKeyConstraint(["pelanggan_id"], ["pelanggan.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sales_invoice_no_invoice"), "sales_invoice", ["no_invoice"], unique=True)

    # 2d. sales_invoice_detail
    op.create_table(
        "sales_invoice_detail",
        sa.Column("sales_invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("harga", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("diskon", sa.Numeric(5, 2), nullable=True),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["sales_invoice_id"], ["sales_invoice.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2e. pengiriman_barang
    op.create_table(
        "pengiriman_barang",
        sa.Column("no_surat_jalan", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ekspedisi", sa.String(100), nullable=True),
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pelanggan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alamat_pengiriman", sa.Text(), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPN, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.ForeignKeyConstraint(["pelanggan_id"], ["pelanggan.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pengiriman_barang_no_surat_jalan"), "pengiriman_barang", ["no_surat_jalan"], unique=True)

    # 2f. pengiriman_barang_detail
    op.create_table(
        "pengiriman_barang_detail",
        sa.Column("pengiriman_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("satuan_id", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["pengiriman_id"], ["pengiriman_barang.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.ForeignKeyConstraint(["satuan_id"], ["satuan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2g. sales_retur
    op.create_table(
        "sales_retur",
        sa.Column("no_retur", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("no_pengembalian", sa.String(50), nullable=True),
        sa.Column("sales_invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pelanggan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alamat_pengembalian", sa.Text(), nullable=True),
        sa.Column("diskon_global", sa.Numeric(5, 2), nullable=True),
        sa.Column("ppn", sa.Numeric(5, 2), server_default="11", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_ppn", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPN, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["sales_invoice_id"], ["sales_invoice.id"]),
        sa.ForeignKeyConstraint(["pelanggan_id"], ["pelanggan.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sales_retur_no_retur"), "sales_retur", ["no_retur"], unique=True)

    # 2h. sales_retur_detail
    op.create_table(
        "sales_retur_detail",
        sa.Column("sales_retur_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("harga", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["sales_retur_id"], ["sales_retur.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ==================================================
    # PEMBELIAN
    # ==================================================

    # 3a. purchase_order
    op.create_table(
        "purchase_order",
        sa.Column("no_pesanan", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tanggal_kirim", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alamat", sa.Text(), nullable=True),
        sa.Column("diskon_global", sa.Numeric(5, 2), nullable=True),
        sa.Column("ppn", sa.Numeric(5, 2), server_default="11", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_diskon", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_ppn", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_biaya_tambahan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPN, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_purchase_order_no_pesanan"), "purchase_order", ["no_pesanan"], unique=True)

    # 3b. purchase_order_detail
    op.create_table(
        "purchase_order_detail",
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("harga", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("diskon", sa.Numeric(5, 2), nullable=True),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_order.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 3c. purchase_invoice
    op.create_table(
        "purchase_invoice",
        sa.Column("no_form", sa.String(30), nullable=False),
        sa.Column("no_faktur", sa.String(50), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alamat", sa.Text(), nullable=True),
        sa.Column("diskon_global", sa.Numeric(5, 2), nullable=True),
        sa.Column("ppn", sa.Numeric(5, 2), server_default="11", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_diskon", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_ppn", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_biaya_tambahan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPN, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_purchase_invoice_no_form"), "purchase_invoice", ["no_form"], unique=True)

    # 3d. purchase_invoice_detail
    op.create_table(
        "purchase_invoice_detail",
        sa.Column("purchase_invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("harga", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("diskon", sa.Numeric(5, 2), nullable=True),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["purchase_invoice_id"], ["purchase_invoice.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 3e. penerimaan_barang
    op.create_table(
        "penerimaan_barang",
        sa.Column("no_form", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alamat", sa.Text(), nullable=True),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPN, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_order.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_penerimaan_barang_no_form"), "penerimaan_barang", ["no_form"], unique=True)

    # 3f. penerimaan_barang_detail
    op.create_table(
        "penerimaan_barang_detail",
        sa.Column("penerimaan_barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("satuan_id", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["penerimaan_barang_id"], ["penerimaan_barang.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.ForeignKeyConstraint(["satuan_id"], ["satuan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 3g. purchase_retur
    op.create_table(
        "purchase_retur",
        sa.Column("no_retur", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alamat", sa.Text(), nullable=True),
        sa.Column("ppn", sa.Numeric(5, 2), server_default="11", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_ppn", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPN, server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_order.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_purchase_retur_no_retur"), "purchase_retur", ["no_retur"], unique=True)

    # 3h. purchase_retur_detail
    op.create_table(
        "purchase_retur_detail",
        sa.Column("purchase_retur_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("harga", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sub_total", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["purchase_retur_id"], ["purchase_retur.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ==================================================
    # PERSEDIAAN
    # ==================================================

    # 4a. penyesuaian_stok
    op.create_table(
        "penyesuaian_stok",
        sa.Column("no_adj", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipe", E_TPA, nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("biaya_satuan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("alasan", sa.Text(), nullable=True),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", E_SPR, server_default="DIAJUKAN", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_penyesuaian_stok_no_adj"), "penyesuaian_stok", ["no_adj"], unique=True)

    # 4b. pemindahan_barang
    op.create_table(
        "pemindahan_barang",
        sa.Column("no_pemindahan", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("proses", E_PPB, nullable=False),
        sa.Column("dari_gudang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ke_gudang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPR, server_default="DIAJUKAN", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["dari_gudang_id"], ["gudang.id"]),
        sa.ForeignKeyConstraint(["ke_gudang_id"], ["gudang.id"]),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pemindahan_barang_no_pemindahan"), "pemindahan_barang", ["no_pemindahan"], unique=True)

    # 4c. permintaan_barang
    op.create_table(
        "permintaan_barang",
        sa.Column("no_permintaan", sa.String(30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("diajukan_oleh", sa.String(100), nullable=False),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        sa.Column("status", E_SPR, server_default="DIAJUKAN", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.ForeignKeyConstraint(["jurnal_umum_id"], ["jurnal_umum.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_permintaan_barang_no_permintaan"), "permintaan_barang", ["no_permintaan"], unique=True)

    # ==================================================
    # ASET TETAP
    # ==================================================

    # 5. aset_tetap
    op.create_table(
        "aset_tetap",
        sa.Column("kode", sa.String(30), nullable=False),
        sa.Column("nama", sa.String(150), nullable=False),
        sa.Column("kategori_aset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("akun_aset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("akun_akumulasi_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("akun_beban_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kuantitas", sa.Integer(), server_default="1", nullable=False),
        sa.Column("umur_aset", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metode_penyusutan", E_MPY, server_default="GARIS_LURUS", nullable=False),
        sa.Column("nilai_gisa", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("nilai_perolehan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("nilai_buku", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("akumulasi_penyusutan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("penyusutan_per_bulan", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("tanggal_mulai", sa.DateTime(timezone=True), nullable=False),
        sa.Column("catatan", sa.Text(), nullable=True),
        sa.Column("auto_post_jurnal", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", E_SAT, server_default="AKTIF", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["kategori_aset_id"], ["kategori_aset.id"]),
        sa.ForeignKeyConstraint(["akun_aset_id"], ["akun_perkiraan.id"]),
        sa.ForeignKeyConstraint(["akun_akumulasi_id"], ["akun_perkiraan.id"]),
        sa.ForeignKeyConstraint(["akun_beban_id"], ["akun_perkiraan.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["pengguna.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aset_tetap_kode"), "aset_tetap", ["kode"], unique=True)

    # ==================================================
    # SUPPORT TABLES
    # ==================================================

    # 6. transaksi_biaya (FK ke SO, SINV, PO, PINV)
    op.create_table(
        "transaksi_biaya",
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sales_invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("purchase_invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("nama", sa.String(100), nullable=False),
        sa.Column("jumlah", sa.Numeric(18, 2), server_default="0", nullable=False),
        *PK(),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_order.id"]),
        sa.ForeignKeyConstraint(["sales_invoice_id"], ["sales_invoice.id"]),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_order.id"]),
        sa.ForeignKeyConstraint(["purchase_invoice_id"], ["purchase_invoice.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 7. stok_mutasi
    op.create_table(
        "stok_mutasi",
        sa.Column("barang_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipe", E_TMS, nullable=False),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("saldo_sebelum", sa.Integer(), server_default="0", nullable=False),
        sa.Column("saldo_sesudah", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ref_module", EXISTING_REFMODULE, nullable=True),
        sa.Column("ref_no", sa.String(30), nullable=True),
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gudang_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("keterangan", sa.Text(), nullable=True),
        *PK(),
        sa.ForeignKeyConstraint(["barang_id"], ["barang.id"]),
        sa.ForeignKeyConstraint(["gudang_id"], ["gudang.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_stok_mutasi_ref_no"), "stok_mutasi", ["ref_no"], unique=False)


def downgrade() -> None:
    """Drop all Phase 5 transaction tables and enum types."""
    # Drop tables in reverse order (details first, then headers, then support)
    TABLES_DROP = [
        # Support
        "stok_mutasi",
        "transaksi_biaya",
        # Aset Tetap
        "aset_tetap",
        # Persediaan
        "permintaan_barang",
        "pemindahan_barang",
        "penyesuaian_stok",
        # Pembelian (detail dulu, lalu header)
        "purchase_retur_detail",
        "purchase_retur",
        "penerimaan_barang_detail",
        "penerimaan_barang",
        "purchase_invoice_detail",
        "purchase_invoice",
        "purchase_order_detail",
        "purchase_order",
        # Penjualan (detail dulu, lalu header)
        "sales_retur_detail",
        "sales_retur",
        "pengiriman_barang_detail",
        "pengiriman_barang",
        "sales_invoice_detail",
        "sales_invoice",
        "sales_order_detail",
        "sales_order",
        # Kas Bank (detail dulu, lalu header)
        "transfer_bank",
        "penerimaan_rincian",
        "penerimaan_kas",
        "pembayaran_rincian",
        "pembayaran_kas",
    ]
    for tbl in TABLES_DROP:
        op.drop_table(tbl)

    # Drop enum types (reverse order)
    for name, _ in reversed(NEW_ENUMS):
        _drop_enum(name)
