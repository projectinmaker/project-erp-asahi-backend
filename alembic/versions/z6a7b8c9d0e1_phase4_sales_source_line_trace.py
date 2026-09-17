"""phase4_sales_source_line_trace

Phase 4 — Sales Transaction Chain (Master Roadmap §11-§16).

Tambah kolom FK source-line trace ke tabel detail penjualan supaya:
- PengirimanBarangDetail bisa link ke SalesOrderDetail (source SO line)
- SalesInvoiceDetail bisa link ke SalesOrderDetail + PengirimanBarangDetail (source SO + delivery line)
- SalesReturDetail bisa link ke SalesInvoiceDetail + PengirimanBarangDetail (source invoice + delivery line)

Sesuai Roadmap:
- §13: "Delivery: sales_order_detail_id"
- §14: "Sales Invoice: sales_order_detail_id, delivery_detail_id"
- §16: "Sales Return: invoice_detail_id, delivery_detail_id"

Backward compatibility: semua kolom baru nullable. Data existing tetap jalan.
Service code yang baru akan set FK ini saat create detail; service yang lama
yang tidak set akan tetap jalan (FK null).

Tidak ada backfill data — kolom baru nullable, default NULL.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "z6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "y5z6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # 1. PengirimanBarangDetail — tambah sales_order_detail_id (FK self-table)
    # ==========================================
    op.add_column(
        'pengiriman_barang_detail',
        sa.Column('sales_order_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.create_foreign_key(
        'fk_pengiriman_detail_so_detail',
        'pengiriman_barang_detail', 'sales_order_detail',
        ['sales_order_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_pengiriman_detail_so_detail_id',
        'pengiriman_barang_detail',
        ['sales_order_detail_id'],
    )

    # ==========================================
    # 2. SalesInvoiceDetail — tambah sales_order_detail_id + delivery_detail_id
    # ==========================================
    op.add_column(
        'sales_invoice_detail',
        sa.Column('sales_order_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.add_column(
        'sales_invoice_detail',
        sa.Column('delivery_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.create_foreign_key(
        'fk_invoice_detail_so_detail',
        'sales_invoice_detail', 'sales_order_detail',
        ['sales_order_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_invoice_detail_delivery_detail',
        'sales_invoice_detail', 'pengiriman_barang_detail',
        ['delivery_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_invoice_detail_so_detail_id',
        'sales_invoice_detail',
        ['sales_order_detail_id'],
    )
    op.create_index(
        'ix_invoice_detail_delivery_detail_id',
        'sales_invoice_detail',
        ['delivery_detail_id'],
    )

    # ==========================================
    # 3. SalesReturDetail — tambah invoice_detail_id + pengiriman_barang_detail_id
    # ==========================================
    op.add_column(
        'sales_retur_detail',
        sa.Column('sales_invoice_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.add_column(
        'sales_retur_detail',
        sa.Column('pengiriman_barang_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.create_foreign_key(
        'fk_retur_detail_invoice_detail',
        'sales_retur_detail', 'sales_invoice_detail',
        ['sales_invoice_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_retur_detail_pengiriman_detail',
        'sales_retur_detail', 'pengiriman_barang_detail',
        ['pengiriman_barang_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_retur_detail_invoice_detail_id',
        'sales_retur_detail',
        ['sales_invoice_detail_id'],
    )
    op.create_index(
        'ix_retur_detail_pengiriman_detail_id',
        'sales_retur_detail',
        ['pengiriman_barang_detail_id'],
    )


def downgrade() -> None:
    # 3. SalesReturDetail
    op.drop_index('ix_retur_detail_pengiriman_detail_id', table_name='sales_retur_detail')
    op.drop_index('ix_retur_detail_invoice_detail_id', table_name='sales_retur_detail')
    op.drop_constraint('fk_retur_detail_pengiriman_detail', 'sales_retur_detail', type_='foreignkey')
    op.drop_constraint('fk_retur_detail_invoice_detail', 'sales_retur_detail', type_='foreignkey')
    op.drop_column('sales_retur_detail', 'pengiriman_barang_detail_id')
    op.drop_column('sales_retur_detail', 'sales_invoice_detail_id')

    # 2. SalesInvoiceDetail
    op.drop_index('ix_invoice_detail_delivery_detail_id', table_name='sales_invoice_detail')
    op.drop_index('ix_invoice_detail_so_detail_id', table_name='sales_invoice_detail')
    op.drop_constraint('fk_invoice_detail_delivery_detail', 'sales_invoice_detail', type_='foreignkey')
    op.drop_constraint('fk_invoice_detail_so_detail', 'sales_invoice_detail', type_='foreignkey')
    op.drop_column('sales_invoice_detail', 'delivery_detail_id')
    op.drop_column('sales_invoice_detail', 'sales_order_detail_id')

    # 1. PengirimanBarangDetail
    op.drop_index('ix_pengiriman_detail_so_detail_id', table_name='pengiriman_barang_detail')
    op.drop_constraint('fk_pengiriman_detail_so_detail', 'pengiriman_barang_detail', type_='foreignkey')
    op.drop_column('pengiriman_barang_detail', 'sales_order_detail_id')
