"""phaseB_sales_order_purchase_order_enhancements

Phase B — Order Documents update based on audit notes.

Bagian 1 — Sales Order (file 14):
- customer_po_number (VARCHAR 50, nullable) — Customer PO reference
- customer_po_date (DATE, nullable) — Customer PO date
- fulfillment_status (VARCHAR 20, nullable) — OPEN/PARTIAL/FULFILLED/CLOSED
- currency (VARCHAR 3, default 'IDR')
- satuan_id di SalesOrderDetail (FK ke satuan.id, nullable)

Bagian 2 — Purchase Order (file 10):
- syarat_bayar_id di PO header (FK ke syarat_bayar.id, nullable)
- currency di PO header (VARCHAR 3, default 'IDR')
- supplier_name_snapshot (VARCHAR 200, nullable) — for historical reference
- satuan_id di PurchaseOrderDetail (FK ke satuan.id, nullable)

Backward compatibility: semua kolom baru nullable, default NULL (kecuali currency='IDR').
Tidak ada backfill data.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # Bagian 1 — Sales Order enhancements
    # ==========================================
    # 1a. SO header: tambah customer_po_number, customer_po_date, fulfillment_status, currency
    op.add_column('sales_order', sa.Column('customer_po_number', sa.String(50), nullable=True))
    op.add_column('sales_order', sa.Column('customer_po_date', sa.Date(), nullable=True))
    op.add_column('sales_order', sa.Column('fulfillment_status', sa.String(20), nullable=True))
    op.add_column('sales_order', sa.Column('currency', sa.String(3), nullable=True, server_default='IDR'))

    # 1b. SO detail: tambah satuan_id
    op.add_column('sales_order_detail', sa.Column('satuan_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_so_detail_satuan', 'sales_order_detail', 'satuan',
        ['satuan_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_so_detail_satuan_id', 'sales_order_detail', ['satuan_id'])

    # ==========================================
    # Bagian 2 — Purchase Order enhancements
    # ==========================================
    # 2a. PO header: tambah syarat_bayar_id, currency, supplier_name_snapshot
    op.add_column('purchase_order', sa.Column('syarat_bayar_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('purchase_order', sa.Column('currency', sa.String(3), nullable=True, server_default='IDR'))
    op.add_column('purchase_order', sa.Column('supplier_name_snapshot', sa.String(200), nullable=True))
    op.create_foreign_key(
        'fk_po_syarat_bayar', 'purchase_order', 'syarat_bayar',
        ['syarat_bayar_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_po_syarat_bayar_id', 'purchase_order', ['syarat_bayar_id'])

    # 2b. PO detail: tambah satuan_id
    op.add_column('purchase_order_detail', sa.Column('satuan_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_po_detail_satuan', 'purchase_order_detail', 'satuan',
        ['satuan_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_po_detail_satuan_id', 'purchase_order_detail', ['satuan_id'])


def downgrade() -> None:
    # 2b
    op.drop_index('ix_po_detail_satuan_id', table_name='purchase_order_detail')
    op.drop_constraint('fk_po_detail_satuan', 'purchase_order_detail', type_='foreignkey')
    op.drop_column('purchase_order_detail', 'satuan_id')

    # 2a
    op.drop_index('ix_po_syarat_bayar_id', table_name='purchase_order')
    op.drop_constraint('fk_po_syarat_bayar', 'purchase_order', type_='foreignkey')
    op.drop_column('purchase_order', 'supplier_name_snapshot')
    op.drop_column('purchase_order', 'currency')
    op.drop_column('purchase_order', 'syarat_bayar_id')

    # 1b
    op.drop_index('ix_so_detail_satuan_id', table_name='sales_order_detail')
    op.drop_constraint('fk_so_detail_satuan', 'sales_order_detail', type_='foreignkey')
    op.drop_column('sales_order_detail', 'satuan_id')

    # 1a
    op.drop_column('sales_order', 'currency')
    op.drop_column('sales_order', 'fulfillment_status')
    op.drop_column('sales_order', 'customer_po_date')
    op.drop_column('sales_order', 'customer_po_number')
