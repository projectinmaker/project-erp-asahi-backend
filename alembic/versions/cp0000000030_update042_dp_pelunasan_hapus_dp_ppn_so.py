"""update042_dp_pelunasan_hapus_dp_ppn_so

Update #042 — DP/Pelunasan jadi SATU field header invoice + hapus PPN dari
Sales Order & Penawaran (pajak hanya dihitung/diterapkan di INVOICE):

1. ``sales_invoice.dp_pelunasan`` NUMERIC(18,2) NOT NULL DEFAULT 0 —
   "nilai yang ditagih invoice ini" (DP bertahap / pelunasan atas SO):
   - Ter-link SO: WAJIB > 0 dan ≤ sisa tagihan SO (grand_total − Σ nilai-tagih
     invoice non-DIBATALKAN); default (null saat create) = sisa SO.
   - > 0 → refresh_totals mendistribusikan proporsional ke baris (sub_total
     baris = porsi dp_pelunasan thd gross harga×qty; Σ baris = dp persis,
     koreksi rounding di baris terakhir); PPN/PPh23 atas base = dp; grand
     total = dp + PPN + biaya − PPh23.
   - 0/NULL (invoice lama / standalone tanpa field) → perilaku LAMA
     (tagih penuh subtotal per baris, tanpa distribusi).
2. Kolom DP PER-BARIS di-drop (fitur #041 digantikan header):
   - ``sales_order_detail.dp``
   - ``sales_invoice_detail.dp``
   Baris SO/invoice kembali baris nilai penuh (harga & qty utuh).
3. PPN dihapus dari dokumen ORDER (grand total = subtotal − diskon + biaya,
   TANPA PPN; PPN tetap di purchase_order/purchase_invoice/sales_invoice/retur):
   - ``sales_order.ppn`` & ``sales_order.total_ppn``
   - ``penawaran.ppn`` & ``penawaran.total_ppn``

Downgrade: kebalikan — kolom dp kembali nullable, ppn default 11,
total_ppn default 0, dp_pelunasan di-drop.

Revision ID: cp0000000030
Revises: cp0000000029
Create Date: 2026-11-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cp0000000030"
down_revision: Union[str, Sequence[str], None] = "cp0000000029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # 1. Field header DP/Pelunasan di Sales Invoice (#042)
    # ==========================================
    op.add_column(
        "sales_invoice",
        sa.Column(
            "dp_pelunasan",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
    )

    # ==========================================
    # 2. Hapus DP per-baris (digantikan header dp_pelunasan)
    # ==========================================
    op.drop_column("sales_order_detail", "dp")
    op.drop_column("sales_invoice_detail", "dp")

    # ==========================================
    # 3. Hapus PPN dari Sales Order & Penawaran (pajak hanya di INVOICE)
    # ==========================================
    op.drop_column("sales_order", "ppn")
    op.drop_column("sales_order", "total_ppn")
    op.drop_column("penawaran", "ppn")
    op.drop_column("penawaran", "total_ppn")


def downgrade() -> None:
    # ==========================================
    # 3. Kembalikan PPN Sales Order & Penawaran (nilai default wajar)
    # ==========================================
    op.add_column(
        "sales_order",
        sa.Column("ppn", sa.Numeric(5, 2), nullable=False, server_default="11"),
    )
    op.add_column(
        "sales_order",
        sa.Column("total_ppn", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "penawaran",
        sa.Column("ppn", sa.Numeric(5, 2), nullable=False, server_default="11"),
    )
    op.add_column(
        "penawaran",
        sa.Column("total_ppn", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )

    # ==========================================
    # 2. Kembalikan DP per-baris (nullable — data lama tidak dipulihkan)
    # ==========================================
    op.add_column("sales_invoice_detail", sa.Column("dp", sa.Numeric(18, 2), nullable=True))
    op.add_column("sales_order_detail", sa.Column("dp", sa.Numeric(18, 2), nullable=True))

    # ==========================================
    # 1. Hapus field header DP/Pelunasan
    # ==========================================
    op.drop_column("sales_invoice", "dp_pelunasan")
