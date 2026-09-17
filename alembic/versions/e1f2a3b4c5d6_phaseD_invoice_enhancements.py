"""phaseD_invoice_enhancements

Phase D — Sales Invoice + Purchase Invoice update based on audit notes.

Bagian 1 — Purchase Invoice (file 9):
- purchase_order_id (UUID, nullable) — direct PO link (Catatan PI §5)
- invoice_type (VARCHAR 20, nullable) — INVENTORY / NON_INVENTORY / FIXED_ASSET (Catatan PI §5)
- satuan_id di PurchaseInvoiceDetail (FK ke satuan.id, nullable) — UOM per line (Catatan PI §3)

Bagian 2 — Sales Invoice (file 11):
- Tidak ada field baru — hanya deprecate auto_post_jurnal default
  (service already forces auto_post_jurnal=False on create)

Revision ID: e1f2a3b4c5d6
Revises: f2a3b4c5d6e7
Create Date: 2026-09-16

Chain update (Phase C catch-up):
- Sebelumnya: down_revision = "d0e1f2a3b4c5" (Phase B)
- Sekarang:   down_revision = "f2a3b4c5d6e7" (Phase C — Delivery & Goods Receipt CHECK constraints)
- Alasan: Phase C catch-up migration (f2a3b4c5d6e7) di-insert antara Phase B dan Phase D
  agar user bisa apply Phase C tanpa harus apply Phase D lebih dulu.
- Hasil rantai: Phase B (d0e1f2a3b4c5) → Phase C (f2a3b4c5d6e7) → Phase D (e1f2a3b4c5d6)
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # Purchase Invoice header: purchase_order_id + invoice_type
    # ==========================================
    op.add_column('purchase_invoice', sa.Column('purchase_order_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('purchase_invoice', sa.Column('invoice_type', sa.String(20), nullable=True))
    op.create_foreign_key(
        'fk_pinvoice_po', 'purchase_invoice', 'purchase_order',
        ['purchase_order_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_pinvoice_po_id', 'purchase_invoice', ['purchase_order_id'])

    # ==========================================
    # Purchase Invoice Detail: satuan_id
    # ==========================================
    op.add_column('purchase_invoice_detail', sa.Column('satuan_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_pinvoice_detail_satuan', 'purchase_invoice_detail', 'satuan',
        ['satuan_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_pinvoice_detail_satuan_id', 'purchase_invoice_detail', ['satuan_id'])


def downgrade() -> None:
    op.drop_index('ix_pinvoice_detail_satuan_id', table_name='purchase_invoice_detail')
    op.drop_constraint('fk_pinvoice_detail_satuan', 'purchase_invoice_detail', type_='foreignkey')
    op.drop_column('purchase_invoice_detail', 'satuan_id')

    op.drop_index('ix_pinvoice_po_id', table_name='purchase_invoice')
    op.drop_constraint('fk_pinvoice_po', 'purchase_invoice', type_='foreignkey')
    op.drop_column('purchase_invoice', 'invoice_type')
    op.drop_column('purchase_invoice', 'purchase_order_id')
