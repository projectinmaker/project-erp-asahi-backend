"""AUD-08 + AUD-09 — kolom pajak: bukti potong, masa pajak, PPh23 Purchase Invoice.

Temuan audit 7-b:
- AUD-08: tidak ada fitur bukti potong (BUPTI) — potongan PPh 23 (kita dipotong
  pelanggan / kita memotong supplier) tercatat nilainya saja, tanpa nomor bukti
  potong dan masa pajak yang dibutuhkan untuk rekap SPT Masa.
- AUD-09: Purchase Invoice tidak punya kolom PPh 23 — potongan PPh 23 atas
  jasa dari supplier tidak bisa tercatat di PI (hutang selalu bruto; PPh 23
  hanya muncul kebetulan saat pelunasan).

Migrasi ini (semua kolom opsional/nullable → backward compatible penuh):
1. ``payment_allocation.no_bukti_potong`` VARCHAR(50) NULL — nomor bukti
   potong per alokasi pelunasan piutang/hutang.
2. ``payment_allocation.masa_pajak`` VARCHAR(7) NULL — masa pajak 'YYYY-MM'.
3. ``kas_identifikasi_rincian.no_bukti_potong`` / ``masa_pajak`` — snapshot
   sama untuk jalur identifikasi dokumen Belum Teridentifikasi.
4. ``purchase_invoice.pph23`` NUMERIC(5,2) default 2, ``pph23_applicable``
   BOOLEAN default false, ``total_pph23`` NUMERIC(18,2) default 0 — mirror
   persis kolom sales_invoice/purchase_order (cp0000000004).

Dokumen lama: pph23_applicable=false → total_pph23=0 → perilaku lama utuh.
"""
from alembic import op
import sqlalchemy as sa

revision = 'cp0000000015'
down_revision = 'cp0000000014'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('payment_allocation',
                  sa.Column('no_bukti_potong', sa.String(50), nullable=True))
    op.add_column('payment_allocation',
                  sa.Column('masa_pajak', sa.String(7), nullable=True))

    op.add_column('kas_identifikasi_rincian',
                  sa.Column('no_bukti_potong', sa.String(50), nullable=True))
    op.add_column('kas_identifikasi_rincian',
                  sa.Column('masa_pajak', sa.String(7), nullable=True))

    op.add_column('purchase_invoice',
                  sa.Column('pph23', sa.Numeric(5, 2), nullable=False,
                            server_default='2'))
    op.add_column('purchase_invoice',
                  sa.Column('pph23_applicable', sa.Boolean(), nullable=False,
                            server_default='false'))
    op.add_column('purchase_invoice',
                  sa.Column('total_pph23', sa.Numeric(18, 2), nullable=False,
                            server_default='0'))


def downgrade():
    op.drop_column('purchase_invoice', 'total_pph23')
    op.drop_column('purchase_invoice', 'pph23_applicable')
    op.drop_column('purchase_invoice', 'pph23')

    op.drop_column('kas_identifikasi_rincian', 'masa_pajak')
    op.drop_column('kas_identifikasi_rincian', 'no_bukti_potong')

    op.drop_column('payment_allocation', 'masa_pajak')
    op.drop_column('payment_allocation', 'no_bukti_potong')
