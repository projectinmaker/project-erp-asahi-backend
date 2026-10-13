"""Revisi Sales Order: hapus kolom ekspedisi & penjual, tambah customer_code.

Update ASAHI (catatan update user — revisi dokumen Sales Order):
1. Field Ekspedisi dan Penjual DIHAPUS dari Sales Order (kolom tabel di-drop).
2. Kolom baru `customer_code` (varchar 20, nullable) — singkatan bebas nama
   perusahaan customer yang di-input manual saat membuat SO. Singkatan ini
   menjadi segmen nomor pesanan:
       terisi  → "SO ASI/TBI/2026/001"
       kosong  → "SO ASI/-/2026/001"
   Nomor lama berformat "SO ASI/2026/001" tetap dipakai dokumen lama; sequence
   tahunan tetap berlanjut (generator membaca kedua format).

Field tanggal_pengiriman TIDAK diubah di DB — hanya label tampilannya yang
berubah dari "Tanggal Pengiriman" menjadi "Target Pengiriman" (UI + cetakan).

Downgrade mengembalikan kolom lama (ekspedisi/penjual) dan membuang
customer_code. Nilai ekspedisi/penjual lama TIDAK dipulihkan (data histori
hilang saat kolom di-drop) — backup database sebelum upgrade untuk produksi.
"""
import sqlalchemy as sa
from alembic import op

revision = 'cp0000000006'
down_revision = 'cp0000000005'
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table('sales_order', schema=None) as batch:
        batch.add_column(sa.Column('customer_code', sa.String(length=20), nullable=True))
        batch.drop_column('ekspedisi')
        batch.drop_column('penjual')
    # Perlebar no_pesanan (30 → 40) untuk format baru "SO ASI/XXXX/2026/001".
    with op.batch_alter_table('sales_order', schema=None) as batch:
        batch.alter_column('no_pesanan', existing_type=sa.String(length=30), type_=sa.String(length=40), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('sales_order', schema=None) as batch:
        batch.alter_column('no_pesanan', existing_type=sa.String(length=40), type_=sa.String(length=30), existing_nullable=False)
    with op.batch_alter_table('sales_order', schema=None) as batch:
        batch.add_column(sa.Column('ekspedisi', sa.String(length=100), nullable=True))
        batch.add_column(sa.Column('penjual', sa.String(length=100), nullable=True))
        batch.drop_column('customer_code')