"""update041_dp_baris_so_invoice__pengiriman_item_bebas

Update #041 — (A) DP per baris SO & Invoice (alur DP bertahap tanpa merusak
harga satuan) + (B) Pengiriman Barang menerima ITEM BEBAS (SO manufaktur /
custom kini bisa dibuatkan Surat Jalan).

1. ``sales_order_detail`` + ``sales_invoice_detail``:
   - kolom baru ``dp`` NUMERIC(18,2) NULL — nominal uang muka (DP) yang
     ditagihkan untuk baris tsb. Saat terisi (> 0), ``sub_total`` baris = dp
     (harga satuan & qty tetap utuh sesuai penawaran/SO), grand total dokumen
     mengikuti Σ dp. Kosong/NULL = baris biasa (sub_total = harga × qty −
     diskon). Dipakai form SO (kolom "DP (Rp)" setelah Diskon) & diteruskan
     otomatis ke baris invoice saat tarik data SO/DO.
2. ``pengiriman_barang_detail``:
   - ``barang_id`` jadi NULLABLE + kolom baru ``item_nama`` (200) dan
     ``item_satuan`` (50), ``satuan_id`` jadi NULLABLE — baris ITEM BEBAS
     (SO manufaktur/jasa/custom tanpa barang master) kini bisa dikirim sebagai
     baris surat jalan TANPA pergerakan stok/HPP (finish_pengiriman skip
     baris bebas). Mirror pola #032 (sales_order_detail/sales_invoice_detail).

Downgrade: hapus kolom dp; baris bebas pengiriman harus dihapus manual lebih
dulu bila benar-benar perlu mengembalikan barang_id/satuan_id ke NOT NULL.

Revision ID: cp0000000029
Revises: cp0000000028
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "cp0000000029"
down_revision: Union[str, Sequence[str], None] = "cp0000000028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # 1. DP per baris SO & Invoice (alur DP bertahap #041-A)
    # ==========================================
    op.add_column("sales_order_detail", sa.Column("dp", sa.Numeric(18, 2), nullable=True))
    op.add_column("sales_invoice_detail", sa.Column("dp", sa.Numeric(18, 2), nullable=True))

    # ==========================================
    # 2. Item bebas di baris Pengiriman Barang (#041-B)
    # ==========================================
    op.alter_column("pengiriman_barang_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.alter_column("pengiriman_barang_detail", "satuan_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.add_column("pengiriman_barang_detail", sa.Column("item_nama", sa.String(200), nullable=True))
    op.add_column("pengiriman_barang_detail", sa.Column("item_satuan", sa.String(50), nullable=True))


def downgrade() -> None:
    # Baris bebas pengiriman tidak bisa dikembalikan ke barang_id NOT NULL
    # tanpa data — hapus baris bebas lebih dulu (manual) bila perlu downgrade.
    op.drop_column("pengiriman_barang_detail", "item_satuan")
    op.drop_column("pengiriman_barang_detail", "item_nama")
    op.alter_column("pengiriman_barang_detail", "satuan_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.alter_column("pengiriman_barang_detail", "barang_id", existing_type=UUID(as_uuid=True), nullable=False)

    op.drop_column("sales_invoice_detail", "dp")
    op.drop_column("sales_order_detail", "dp")
