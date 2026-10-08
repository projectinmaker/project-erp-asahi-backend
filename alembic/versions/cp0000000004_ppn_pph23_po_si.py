"""Pilihan pajak PPh23 & PPN opsional pada PO dan Invoice Penjualan.

Update ASAHI (permintaan user):
Saat input Pesanan Pembelian (PO) dan Invoice Penjualan (SI), user dapat
memilih PPh23 dan/atau PPN — bisa dua-duanya, salah satu, atau tidak
sama sekali.

Kolom baru di tabel purchase_order dan sales_invoice:
1. ppn_applicable  (bool, default true)  — PPN diterapkan? false → total_ppn = 0.
2. pph23_applicable (bool, default false) — PPh23 diterapkan? true → total_pph23 dihitung.
3. pph23           (numeric 5,2, default 2) — tarif PPh23 (persen, standar 2% jasa).
4. total_pph23     (numeric 18,2, default 0) — nilai PPh23 yang memotong grand_total.

Rumus total (di service document_totals.refresh_totals):
    DPP        = sub_total - total_diskon
    total_ppn  = DPP * ppn%   (hanya bila ppn_applicable)
    total_pph23= DPP * pph23% (hanya bila pph23_applicable)
    grand_total= DPP + total_ppn + total_biaya_tambahan - total_pph23

Backfill: dokumen lama diberi default kompatibel (ppn_applicable=true
mengikuti perilaku lama; PPh23 tidak aktif).
"""
import sqlalchemy as sa
from alembic import op

revision = 'cp0000000004'
down_revision = 'cp0000000003'
branch_labels = None
depends_on = None

TABELS = ('purchase_order', 'sales_invoice')


def upgrade() -> None:
    for table in TABELS:
        with op.batch_alter_table(table, schema=None) as batch:
            batch.add_column(sa.Column('ppn_applicable', sa.Boolean(), nullable=False, server_default=sa.text('true')))
            batch.add_column(sa.Column('pph23_applicable', sa.Boolean(), nullable=False, server_default=sa.text('false')))
            batch.add_column(sa.Column('pph23', sa.Numeric(5, 2), nullable=False, server_default=sa.text('2')))
            batch.add_column(sa.Column('total_pph23', sa.Numeric(18, 2), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    for table in TABELS:
        with op.batch_alter_table(table, schema=None) as batch:
            batch.drop_column('total_pph23')
            batch.drop_column('pph23')
            batch.drop_column('pph23_applicable')
            batch.drop_column('ppn_applicable')
