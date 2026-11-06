"""update044_voucher_memorial_gagal_tagih

=== Update #044 — tabel Voucher Memorial (penghapusan piutang / Gagal Tagih) ===

Alur Penerimaan Piutang modul Kas & Bank (revisi pemilik, poin 5):
Penerimaan Penuh / Sebagian → Voucher Penerimaan Bank/Kas (PenerimaanKas,
sudah ada); Gagal Tagih → Voucher Memorial (Adjustment) — dokumen BARU:

- ``voucher_memorial``        : header (pelanggan, akun beban Dr, total, jurnal).
- ``voucher_memorial_detail`` : per invoice (akun piutang kontrol Cr + snapshot
  terutang). Sisa tagihan invoice dihitung ulang settlement_service.balances
  dengan mengurangkan penghapusan aktif (total_gagal_tagih).

Downgrade: kedua tabel di-drop.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "cp0000000032"
down_revision: Union[str, Sequence[str], None] = "cp0000000031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum status dipakai bersama tabel kas/bank lain (JANGAN create ulang).
    sts = postgresql.ENUM(name="statustransaksi", create_type=False)
    op.create_table(
        "voucher_memorial",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("no_bukti", sa.String(length=30), nullable=False),
        sa.Column("tanggal", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pelanggan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pelanggan.id"), nullable=False),
        # Akun beban penghapusan piutang (Dr) — opsional; resolve dari Setting
        # Akun BEBAN_PENGHAPUSAN_PIUTANG saat posting bila kosong.
        sa.Column("akun_beban_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("akun_perkiraan.id"), nullable=True),
        sa.Column("catatan", sa.Text(), nullable=True),
        sa.Column("total_nilai", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("jurnal_umum_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jurnal_umum.id"), nullable=True),
        sa.Column(
            "status", sts,
            nullable=False, server_default="DRAFT",
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("pengguna.id"), nullable=False),
    )
    op.create_index("ix_voucher_memorial_no_bukti", "voucher_memorial", ["no_bukti"], unique=True)

    op.create_table(
        "voucher_memorial_detail",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("voucher_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("voucher_memorial.id"), nullable=False),
        sa.Column("sales_invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_invoice.id"), nullable=False),
        sa.Column("akun_piutang_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("akun_perkiraan.id"), nullable=False),
        sa.Column("nilai", sa.Numeric(18, 2), nullable=False),
        sa.Column("terutang", sa.Numeric(18, 2), nullable=True),
    )
    op.create_index("ix_voucher_memorial_detail_voucher_id", "voucher_memorial_detail", ["voucher_id"])
    op.create_index("ix_voucher_memorial_detail_sales_invoice_id", "voucher_memorial_detail", ["sales_invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_voucher_memorial_detail_sales_invoice_id", table_name="voucher_memorial_detail")
    op.drop_index("ix_voucher_memorial_detail_voucher_id", table_name="voucher_memorial_detail")
    op.drop_table("voucher_memorial_detail")
    op.drop_index("ix_voucher_memorial_no_bukti", table_name="voucher_memorial")
    op.drop_table("voucher_memorial")
