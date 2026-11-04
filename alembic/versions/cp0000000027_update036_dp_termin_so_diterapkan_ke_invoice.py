"""update036_dp_termin_so_diterapkan_ke_invoice

Update #036 — Sisa tagihan SO berttermin di alur Invoice: DP (pembayaran
termin SO yang sudah dibayar sebelum difakturkan) OTOMATIS diterapkan ke
Piutang invoice saat invoice diposting, sehingga:

1. Jurnal invoice menjadi: D Piutang (grand_total − DP) + D akun lawan
   termin (umumnya Uang Muka Penjualan — saldo kewajibannya dikosongkan)
   + K Pendapatan/PPN/Angkut (tetap).
2. Kolom baru ``sales_invoice.dp_teraplikasi`` menyimpan besaran DP yang
   diterapkan; sisa tagihan di Pelunasan/aging =
   grand_total − bayar − retur − dp_teraplikasi (customer tidak ditagih
   dua kali untuk bagian DP).
3. Pembayaran termin SETELAH invoice diposting otomatis diarahkan ke akun
   Piutang invoice (bukan Uang Muka) + tercatat sebagai alokasi pembayaran
   invoice (lihat penjualan_service.terima_pembayaran_termin).

Backward compatibility:
- Kolom default 0 → invoice lama berperilaku persis seperti sebelum #036
  (sisa = grand_total − bayar − retur).
- Tidak ada backfill: DP untuk invoice lama dari SO berttermin tetap
  menggantung di Uang Muka Penjualan (kewajiban) — bila ingin rapi, user
  dapat membuat jurnal manual D Uang Muka Penjualan / K Piutang Usaha
  (Jurnal Umum) atau membatalkan & membuat ulang invoice tsb.

Revision ID: cp0000000027
Revises: cp0000000026
Create Date: 2026-11-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "cp0000000027"
down_revision: Union[str, Sequence[str], None] = "cp0000000026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column(
        "sales_invoice",
        sa.Column(
            "dp_teraplikasi",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
    )

def downgrade() -> None:
    op.drop_column("sales_invoice", "dp_teraplikasi")