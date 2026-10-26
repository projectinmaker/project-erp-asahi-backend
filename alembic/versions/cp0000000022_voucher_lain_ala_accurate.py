"""voucher lain ala Accurate: cek_kosong + keterangan rincian (Update #16)

Update #16 — form Penerimaan Lain & Pembayaran Lain disamakan dengan
Accurate Desktop 3 (agar klien tidak perlu adaptasi):

1. pembayaran_kas.cek_kosong (BOOLEAN NOT NULL DEFAULT FALSE)
   — checkbox "Cek Kosong" pada form Pembayaran ala Accurate; True =
   pembayaran via cek kosong (nomor cek diisi belakangan saat cek
   diformulirkan), No. Cek boleh kosong.
2. pembayaran_rincian.keterangan (TEXT NULL)
   — kolom "Catatan" per baris rincian pada grid detail (paritas
   Accurate "No. Akun | Nama Akun | Jumlah | Catatan").
3. penerimaan_rincian.keterangan (TEXT NULL) — idem.

Tanpa perubahan data lama (kolom baru additive; default aman).
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'cp0000000022'
down_revision = 'cp0000000021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. "Cek Kosong" pada pembayaran (paritas Accurate "Pembayaran Lain").
    op.add_column(
        'pembayaran_kas',
        sa.Column('cek_kosong', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    # 2. Keterangan per baris rincian pembayaran.
    op.add_column('pembayaran_rincian', sa.Column('keterangan', sa.Text(), nullable=True))
    # 3. Keterangan per baris rincian penerimaan.
    op.add_column('penerimaan_rincian', sa.Column('keterangan', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('penerimaan_rincian', 'keterangan')
    op.drop_column('pembayaran_rincian', 'keterangan')
    op.drop_column('pembayaran_kas', 'cek_kosong')
