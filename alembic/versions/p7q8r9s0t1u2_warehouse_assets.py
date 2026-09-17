"""Warehouse value balances and approved asset lifecycle documents."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'p7q8r9s0t1u2'
down_revision = 'o6p7q8r9s0t1'
branch_labels = None
depends_on = None


def base_columns():
    return [sa.Column('id', UUID(as_uuid=True), primary_key=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.alter_column('pemindahan_barang', 'dari_gudang_id', nullable=True)
    op.create_table('stock_balance', *base_columns(),
        sa.Column('barang_id', UUID(as_uuid=True), sa.ForeignKey('barang.id'), nullable=False),
        sa.Column('gudang_id', UUID(as_uuid=True), sa.ForeignKey('gudang.id')),
        sa.Column('location_key', sa.String(36), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False), sa.Column('nilai', sa.Numeric(18,2), nullable=False),
        sa.UniqueConstraint('barang_id', 'location_key', name='uq_stock_location'),
        sa.CheckConstraint('qty >= 0 AND nilai >= 0', name='ck_stock_nonnegative'))
    op.create_index('ix_stock_balance_barang_id', 'stock_balance', ['barang_id'])
    # Existing average quantities have no trustworthy warehouse split. Preserve them explicitly.
    op.execute("""INSERT INTO stock_balance (id, created_at, updated_at, barang_id, gudang_id, location_key, qty, nilai)
        SELECT md5(id::text || ':' || 'UNASSIGNED')::uuid, now(), now(), id, NULL, 'UNASSIGNED', stok, round(stok * harga_pokok,2)
        FROM barang WHERE metode_valuasi = 'AVERAGE' OR metode_valuasi IS NULL""")
    op.execute("""INSERT INTO stock_balance (id, created_at, updated_at, barang_id, gudang_id, location_key, qty, nilai)
        SELECT md5(l.barang_id::text || ':' || coalesce(l.gudang_id::text,'UNASSIGNED'))::uuid, now(), now(),
        l.barang_id, l.gudang_id, coalesce(l.gudang_id::text,'UNASSIGNED'), sum(l.qty_sisa), round(sum(l.qty_sisa*l.harga_satuan),2)
        FROM stok_kartu_layer l JOIN barang b ON b.id=l.barang_id
        WHERE b.metode_valuasi IN ('FIFO','FEFO') AND l.qty_sisa > 0 GROUP BY l.barang_id,l.gudang_id""")
    op.add_column('stok_kartu_layer', sa.Column('tanggal_kedaluwarsa', sa.Date()))
    op.add_column('stok_mutasi', sa.Column('global_value_after', sa.Numeric(18,2)))
    op.add_column('stok_mutasi', sa.Column('cost_parts', sa.JSON()))
    for field in ('inventory_account_id', 'expense_account_id'):
        op.add_column('stok_mutasi', sa.Column(field, UUID(as_uuid=True)))
        op.create_foreign_key('fk_stock_'+field, 'stok_mutasi', 'akun_perkiraan', [field], ['id'])
    op.add_column('sales_retur', sa.Column('pengiriman_id', UUID(as_uuid=True)))
    op.create_foreign_key('fk_sales_retur_pengiriman', 'sales_retur', 'pengiriman_barang', ['pengiriman_id'], ['id'])
    for field in ('warehouse_qty_before', 'warehouse_qty_after'):
        op.add_column('stok_mutasi', sa.Column(field, sa.Integer()))
    for table in ('sales_retur', 'purchase_retur', 'pengiriman_barang', 'penerimaan_barang', 'penyesuaian_stok'):
        op.add_column(table, sa.Column('gudang_id', UUID(as_uuid=True)))
        op.create_foreign_key('fk_'+table+'_warehouse', table, 'gudang', ['gudang_id'], ['id'])
    op.add_column('penerimaan_barang_detail', sa.Column('harga_perolehan', sa.Numeric(18,2)))
    op.add_column('penerimaan_barang_detail', sa.Column('tanggal_kedaluwarsa', sa.Date()))
    op.add_column('aset_tetap', sa.Column('capitalized', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('aset_tetap', sa.Column('lokasi', sa.String(150)))
    op.create_table('asset_event', *base_columns(),
        sa.Column('aset_id', UUID(as_uuid=True), sa.ForeignKey('aset_tetap.id'), nullable=False),
        sa.Column('jenis', sa.String(24), nullable=False),
        sa.Column('tanggal', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('total', sa.Numeric(18,2), nullable=False),
        sa.Column('parameter', sa.JSON(), nullable=False),
        sa.Column('sebelum', sa.JSON()), sa.Column('sesudah', sa.JSON()),
        sa.Column('jurnal_umum_id', UUID(as_uuid=True), sa.ForeignKey('jurnal_umum.id')),
        sa.Column('source_journal_id', UUID(as_uuid=True), sa.ForeignKey('jurnal_umum.id')),
        sa.Column('created_by', UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=False))
    op.create_index('ix_asset_event_aset_id', 'asset_event', ['aset_id'])


def downgrade():
    # Do not silently discard unassigned-location transfer history.
    op.alter_column('pemindahan_barang', 'dari_gudang_id', nullable=False)
    op.drop_table('asset_event')
    for field in ('capitalized','lokasi'):
        op.drop_column('aset_tetap', field)
    for field in ('harga_perolehan','tanggal_kedaluwarsa'):
        op.drop_column('penerimaan_barang_detail', field)
    for table in ('sales_retur', 'purchase_retur', 'pengiriman_barang', 'penerimaan_barang', 'penyesuaian_stok'):
        op.drop_constraint('fk_'+table+'_warehouse', table, type_='foreignkey')
        op.drop_column(table, 'gudang_id')
    for field in ('warehouse_qty_before','warehouse_qty_after'):
        op.drop_column('stok_mutasi', field)
    op.drop_column('stok_kartu_layer','tanggal_kedaluwarsa')
    op.drop_column('stok_mutasi','global_value_after')
    op.drop_column('stok_mutasi','cost_parts')
    for field in ('inventory_account_id','expense_account_id'):
        op.drop_constraint('fk_stock_'+field, 'stok_mutasi', type_='foreignkey')
        op.drop_column('stok_mutasi',field)
    op.drop_constraint('fk_sales_retur_pengiriman','sales_retur',type_='foreignkey')
    op.drop_column('sales_retur','pengiriman_id')
    op.drop_table('stock_balance')
