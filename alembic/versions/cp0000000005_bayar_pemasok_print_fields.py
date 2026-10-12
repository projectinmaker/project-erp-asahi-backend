"""Update cetak "Bayar Pemasok" (modul Kas & Bank — dokumen Pembayaran).

Perubahan skema untuk mendukung layout cetak baru:

1. Tabel pembayaran_kas — info header tambahan:
   - tanggal_cek (date, null)  — tanggal cek/GIRO
   - jumlah_cek  (numeric 18,2, null) — nominal cek
   - mata_uang   (varchar 8, default 'IDR') — mata uang dokumen
   - nilai_tukar (numeric 18,4, default 1)  — hasil input nilai tukar

2. Tabel payment_allocation — diskon pelunasan per faktur:
   - diskon  (numeric 18,2, default 0) — potongan pelunasan dari supplier;
     kas keluar per faktur = nilai - diskon (nilai tetap = hutang dilunasi,
     sehingga sum(nilai) di semua laporan tidak berubah makna).
   - terutang (numeric 18,2, null) — snapshot sisa tagihan faktur SEBELUM
     pembayaran ini; dipakai kolom "Terutang" pada layout cetak. Baris lama
     dibiarkan NULL (cetak menampilkan '-').

Jurnal saat diskon > 0 (posting di kas_bank_service.post_pembayaran):
    Dr Hutang usaha   Σ nilai
    Dr Penalti        (bila ada)
    Cr Kas/Bank       Σ nilai - Σ diskon + penalti
    Cr Potongan Hutang Σ diskon   (akun via setting POTONGAN_HUTANG)
"""
import sqlalchemy as sa
from alembic import op

revision = 'cp0000000005'
down_revision = 'cp0000000004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('pembayaran_kas', schema=None) as batch:
        batch.add_column(sa.Column('tanggal_cek', sa.Date(), nullable=True))
        batch.add_column(sa.Column('jumlah_cek', sa.Numeric(18, 2), nullable=True))
        batch.add_column(sa.Column('mata_uang', sa.String(length=8), nullable=False, server_default='IDR'))
        batch.add_column(sa.Column('nilai_tukar', sa.Numeric(18, 4), nullable=False, server_default='1'))

    with op.batch_alter_table('payment_allocation', schema=None) as batch:
        batch.add_column(sa.Column('diskon', sa.Numeric(18, 2), nullable=False, server_default='0'))
        batch.add_column(sa.Column('terutang', sa.Numeric(18, 2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('payment_allocation', schema=None) as batch:
        batch.drop_column('terutang')
        batch.drop_column('diskon')

    with op.batch_alter_table('pembayaran_kas', schema=None) as batch:
        batch.drop_column('nilai_tukar')
        batch.drop_column('mata_uang')
        batch.drop_column('jumlah_cek')
        batch.drop_column('tanggal_cek')
