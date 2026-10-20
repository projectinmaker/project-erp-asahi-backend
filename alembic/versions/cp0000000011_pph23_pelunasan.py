"""pph23_pelunasan

Update #01 (catatan-update-01) — integrasi Pelunasan Piutang & Hutang ke
Kas & Bank, termasuk dukungan PPh 23 saat pelunasan.

Tabel payment_allocation mendapat kolom:
- pph23 NUMERIC(18,2) NOT NULL server_default '0'

PPh 23 adalah potongan pajak atas pembayaran per faktur:
- piutang (penerimaan): pelanggan memotong PPh 23 dari setoran tunai;
  kas masuk = nilai - pph23; selisihnya didebit ke akun
  PPh 23 Dibayar di Muka (setting PPH23_DIBAYAR_DIMUKA, COA v2 116003).
- hutang (pembayaran): perusahaan menahan PPh 23 dari pembayaran ke supplier;
  kas keluar = nilai - diskon - pph23; selisihnya dikredit ke akun
  Hutang PPh 23 (setting HUTANG_PPH23, COA v2 213002).

Sisa tagihan faktur tetap berkurang sebesar ``nilai`` penuh (tidak ada
pencatatan piutang/hutang ganda; jurnal tetap tunggal per dokumen kas).
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'cp0000000011'
down_revision = 'cp0000000010'
branch_labels = None
depends_on = None

_TABLE = 'payment_allocation'


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column('pph23', sa.Numeric(18, 2), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, 'pph23')
