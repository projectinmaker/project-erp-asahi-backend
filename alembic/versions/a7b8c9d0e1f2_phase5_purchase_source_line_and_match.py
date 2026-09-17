"""phase5_purchase_source_line_and_match

Phase 5 — Purchase Transaction Chain + GRNI (Master Roadmap §17-§22).

Bagian 1 — Tambah kolom FK source-line trace ke 3 detail tables:
- penerimaan_barang_detail.purchase_order_detail_id  (Roadmap §19)
- purchase_invoice_detail.purchase_order_detail_id   (Roadmap §20)
- purchase_invoice_detail.penerimaan_barang_detail_id (Roadmap §20)
- purchase_retur_detail.purchase_order_detail_id             (Roadmap §22)
- purchase_retur_detail.penerimaan_barang_detail_id          (Roadmap §22)
- purchase_retur_detail.purchase_invoice_detail_id            (Roadmap §22)

Bagian 2 — Buat table baru `purchase_invoice_receipt_match` (three-way match bridge):
Sesuai Roadmap §20 "Recommended bridge":
    purchase_invoice_receipt_match
    --------------------------------
    purchase_invoice_detail_id
    penerimaan_barang_detail_id
    matched_qty
    matched_unit_cost
    matched_value

Dipakai untuk track match antara invoice line dan receipt line. Satu invoice line
bisa match banyak receipt lines (partial receipt), dan satu receipt line bisa
match banyak invoice lines (partial invoice).

Backward compatibility: semua kolom FK baru nullable, default NULL.
Tidak ada backfill data — kolom baru nullable, default NULL.

Revision ID: a7b8c9d0e1f2
Revises: z6a7b8c9d0e1
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "z6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # BAGIAN 1 — Source-line FK ke detail tables
    # ==========================================

    # 1a. PenerimaanBarangDetail — tambah purchase_order_detail_id
    op.add_column(
        'penerimaan_barang_detail',
        sa.Column('purchase_order_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.create_foreign_key(
        'fk_penerimaan_detail_po_detail',
        'penerimaan_barang_detail', 'purchase_order_detail',
        ['purchase_order_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_penerimaan_detail_po_detail_id',
        'penerimaan_barang_detail',
        ['purchase_order_detail_id'],
    )

    # 1b. PurchaseInvoiceDetail — tambah purchase_order_detail_id + penerimaan_barang_detail_id
    op.add_column(
        'purchase_invoice_detail',
        sa.Column('purchase_order_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.add_column(
        'purchase_invoice_detail',
        sa.Column('penerimaan_barang_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.create_foreign_key(
        'fk_pinvoice_detail_po_detail',
        'purchase_invoice_detail', 'purchase_order_detail',
        ['purchase_order_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_pinvoice_detail_penerimaan_detail',
        'purchase_invoice_detail', 'penerimaan_barang_detail',
        ['penerimaan_barang_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_pinvoice_detail_po_detail_id',
        'purchase_invoice_detail',
        ['purchase_order_detail_id'],
    )
    op.create_index(
        'ix_pinvoice_detail_penerimaan_detail_id',
        'purchase_invoice_detail',
        ['penerimaan_barang_detail_id'],
    )

    # 1c. PurchaseReturDetail — tambah 3 source-line FK
    op.add_column(
        'purchase_retur_detail',
        sa.Column('purchase_order_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.add_column(
        'purchase_retur_detail',
        sa.Column('penerimaan_barang_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.add_column(
        'purchase_retur_detail',
        sa.Column('purchase_invoice_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True)
    )
    op.create_foreign_key(
        'fk_pretur_detail_po_detail',
        'purchase_retur_detail', 'purchase_order_detail',
        ['purchase_order_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_pretur_detail_penerimaan_detail',
        'purchase_retur_detail', 'penerimaan_barang_detail',
        ['penerimaan_barang_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_pretur_detail_pinvoice_detail',
        'purchase_retur_detail', 'purchase_invoice_detail',
        ['purchase_invoice_detail_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_pretur_detail_po_detail_id',
        'purchase_retur_detail',
        ['purchase_order_detail_id'],
    )
    op.create_index(
        'ix_pretur_detail_penerimaan_detail_id',
        'purchase_retur_detail',
        ['penerimaan_barang_detail_id'],
    )
    op.create_index(
        'ix_pretur_detail_pinvoice_detail_id',
        'purchase_retur_detail',
        ['purchase_invoice_detail_id'],
    )

    # ==========================================
    # BAGIAN 2 — Three-way match bridge table
    # ==========================================
    # Sesuai Roadmap §20 recommended bridge:
    # purchase_invoice_receipt_match (purchase_invoice_detail_id, penerimaan_barang_detail_id,
    # matched_qty, matched_unit_cost, matched_value)
    op.create_table(
        'purchase_invoice_receipt_match',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('purchase_invoice_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('purchase_invoice_detail.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('penerimaan_barang_detail_id',
                  sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('penerimaan_barang_detail.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('matched_qty', sa.Integer(), nullable=False),
        sa.Column('matched_unit_cost', sa.Numeric(18, 2), nullable=False),
        sa.Column('matched_value', sa.Numeric(18, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text('now()')),
    )
    # Index untuk query cepat: "matches untuk invoice detail X" / "matches untuk receipt detail Y"
    op.create_index(
        'ix_pinv_rec_match_invoice_detail',
        'purchase_invoice_receipt_match',
        ['purchase_invoice_detail_id'],
    )
    op.create_index(
        'ix_pinv_rec_match_receipt_detail',
        'purchase_invoice_receipt_match',
        ['penerimaan_barang_detail_id'],
    )


def downgrade() -> None:
    # Bagian 2 — drop bridge table
    op.drop_index('ix_pinv_rec_match_receipt_detail', table_name='purchase_invoice_receipt_match')
    op.drop_index('ix_pinv_rec_match_invoice_detail', table_name='purchase_invoice_receipt_match')
    op.drop_table('purchase_invoice_receipt_match')

    # Bagian 1c — PurchaseReturDetail
    op.drop_index('ix_pretur_detail_pinvoice_detail_id', table_name='purchase_retur_detail')
    op.drop_index('ix_pretur_detail_penerimaan_detail_id', table_name='purchase_retur_detail')
    op.drop_index('ix_pretur_detail_po_detail_id', table_name='purchase_retur_detail')
    op.drop_constraint('fk_pretur_detail_pinvoice_detail', 'purchase_retur_detail', type_='foreignkey')
    op.drop_constraint('fk_pretur_detail_penerimaan_detail', 'purchase_retur_detail', type_='foreignkey')
    op.drop_constraint('fk_pretur_detail_po_detail', 'purchase_retur_detail', type_='foreignkey')
    op.drop_column('purchase_retur_detail', 'purchase_invoice_detail_id')
    op.drop_column('purchase_retur_detail', 'penerimaan_barang_detail_id')
    op.drop_column('purchase_retur_detail', 'purchase_order_detail_id')

    # Bagian 1b — PurchaseInvoiceDetail
    op.drop_index('ix_pinvoice_detail_penerimaan_detail_id', table_name='purchase_invoice_detail')
    op.drop_index('ix_pinvoice_detail_po_detail_id', table_name='purchase_invoice_detail')
    op.drop_constraint('fk_pinvoice_detail_penerimaan_detail', 'purchase_invoice_detail', type_='foreignkey')
    op.drop_constraint('fk_pinvoice_detail_po_detail', 'purchase_invoice_detail', type_='foreignkey')
    op.drop_column('purchase_invoice_detail', 'penerimaan_barang_detail_id')
    op.drop_column('purchase_invoice_detail', 'purchase_order_detail_id')

    # Bagian 1a — PenerimaanBarangDetail
    op.drop_index('ix_penerimaan_detail_po_detail_id', table_name='penerimaan_barang_detail')
    op.drop_constraint('fk_penerimaan_detail_po_detail', 'penerimaan_barang_detail', type_='foreignkey')
    op.drop_column('penerimaan_barang_detail', 'purchase_order_detail_id')
