"""Invoice settlement allocations, frozen due dates and control accounts.

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'o6p7q8r9s0t1'
down_revision = 'n5o6p7q8r9s0'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('sales_invoice', 'purchase_invoice'):
        op.add_column(table, sa.Column('tanggal_jatuh_tempo', sa.Date(), nullable=True))
        op.add_column(table, sa.Column('akun_kontrol_id', postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f'fk_{table}_akun_kontrol', table, 'akun_perkiraan', ['akun_kontrol_id'], ['id'])
    op.add_column('purchase_invoice', sa.Column('syarat_bayar_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_purchase_invoice_syarat_bayar', 'purchase_invoice', 'syarat_bayar', ['syarat_bayar_id'], ['id'])
    for table, field, party in (('penerimaan_kas', 'pelanggan_id', 'pelanggan'), ('pembayaran_kas', 'supplier_id', 'supplier'),
                                ('purchase_retur', 'purchase_invoice_id', 'purchase_invoice')):
        op.add_column(table, sa.Column(field, postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f'fk_{table}_{field}', table, party, [field], ['id'])
    op.create_table('payment_allocation',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('penerimaan_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('penerimaan_kas.id')),
        sa.Column('pembayaran_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('pembayaran_kas.id')),
        sa.Column('sales_invoice_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sales_invoice.id')),
        sa.Column('purchase_invoice_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('purchase_invoice.id')),
        sa.Column('akun_perkiraan_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('akun_perkiraan.id'), nullable=False),
        sa.Column('nilai', sa.Numeric(18, 2), nullable=False),
        sa.CheckConstraint('nilai > 0', name='ck_allocation_positive'),
        sa.CheckConstraint('(penerimaan_id IS NOT NULL AND sales_invoice_id IS NOT NULL AND pembayaran_id IS NULL AND purchase_invoice_id IS NULL) OR (pembayaran_id IS NOT NULL AND purchase_invoice_id IS NOT NULL AND penerimaan_id IS NULL AND sales_invoice_id IS NULL)', name='ck_allocation_pair'),
        sa.UniqueConstraint('penerimaan_id', 'sales_invoice_id', name='uq_allocation_receipt_invoice'),
        sa.UniqueConstraint('pembayaran_id', 'purchase_invoice_id', name='uq_allocation_payment_invoice'))
    for field in ('penerimaan_id', 'pembayaran_id', 'sales_invoice_id', 'purchase_invoice_id'):
        op.create_index(f'ix_payment_allocation_{field}', 'payment_allocation', [field])
    # Snapshot current known terms; do not invent links between legacy cash and invoices.
    op.execute("""UPDATE sales_invoice si SET tanggal_jatuh_tempo =
        (si.tanggal AT TIME ZONE 'Asia/Jakarta')::date +
        GREATEST(COALESCE((SELECT hari FROM syarat_bayar sb WHERE sb.id = si.syarat_bayar_id), 0), 0)
        WHERE si.tanggal_jatuh_tempo IS NULL""")
    op.execute("""UPDATE purchase_invoice SET tanggal_jatuh_tempo =
        (tanggal AT TIME ZONE 'Asia/Jakarta')::date WHERE tanggal_jatuh_tempo IS NULL""")


def downgrade():
    op.drop_table('payment_allocation')
    for table, field in (('purchase_retur', 'purchase_invoice_id'), ('pembayaran_kas', 'supplier_id'), ('penerimaan_kas', 'pelanggan_id')):
        op.drop_constraint(f'fk_{table}_{field}', table, type_='foreignkey')
        op.drop_column(table, field)
    op.drop_constraint('fk_purchase_invoice_syarat_bayar', 'purchase_invoice', type_='foreignkey')
    op.drop_column('purchase_invoice', 'syarat_bayar_id')
    for table in ('purchase_invoice', 'sales_invoice'):
        op.drop_constraint(f'fk_{table}_akun_kontrol', table, type_='foreignkey')
        op.drop_column(table, 'akun_kontrol_id')
        op.drop_column(table, 'tanggal_jatuh_tempo')
