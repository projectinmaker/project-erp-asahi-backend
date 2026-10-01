"""Harga jual default + mapping COA retur & diskon penjualan pada barang.

- barang.harga_jual: harga jual default (Numeric 18,2, default 0). Bukan HPP —
  HPP tetap mengikuti valuasi stok.
- barang.akun_retur_penjualan_id: mapping COA Retur Penjualan (contra-revenue)
  per barang, ditambahkan di tab Akun form Barang & Jasa.
- barang.akun_diskon_penjualan_id: mapping COA Diskon Penjualan
  (contra-revenue) per barang.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = 'u3v4w5x6y7z8'
down_revision = 'sa0000000002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('barang', sa.Column('harga_jual', sa.Numeric(18, 2), nullable=False, server_default='0'))
    op.add_column('barang', sa.Column('akun_retur_penjualan_id', UUID(as_uuid=True), nullable=True))
    op.add_column('barang', sa.Column('akun_diskon_penjualan_id', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_barang_akun_retur_penjualan', 'barang', 'akun_perkiraan', ['akun_retur_penjualan_id'], ['id'])
    op.create_foreign_key('fk_barang_akun_diskon_penjualan', 'barang', 'akun_perkiraan', ['akun_diskon_penjualan_id'], ['id'])
    op.create_index('ix_barang_akun_retur_penjualan_id', 'barang', ['akun_retur_penjualan_id'])
    op.create_index('ix_barang_akun_diskon_penjualan_id', 'barang', ['akun_diskon_penjualan_id'])


def downgrade():
    op.drop_index('ix_barang_akun_diskon_penjualan_id', table_name='barang')
    op.drop_index('ix_barang_akun_retur_penjualan_id', table_name='barang')
    op.drop_constraint('fk_barang_akun_diskon_penjualan', 'barang', type_='foreignkey')
    op.drop_constraint('fk_barang_akun_retur_penjualan', 'barang', type_='foreignkey')
    op.drop_column('barang', 'akun_diskon_penjualan_id')
    op.drop_column('barang', 'akun_retur_penjualan_id')
    op.drop_column('barang', 'harga_jual')
