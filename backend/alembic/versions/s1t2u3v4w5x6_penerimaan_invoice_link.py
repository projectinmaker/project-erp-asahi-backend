"""Optional link penerimaan_barang -> purchase_invoice; setting PENERIMAAN_DALAM_PROSES.

Revision ID: s1t2u3v4w5x6
Revises: r9s0t1u2v3w4
Create Date: 2026-09-11

Tahap 2 — Integrasi posting Persediaan:
- Tambah kolom `penerimaan_barang.purchase_invoice_id` (nullable, FK ke purchase_invoice.id).
- Tidak ada backfill data; penerimaan lama tetap null (kompatibel dengan alur invoice lama).
- Tidak mengubah jurnal yang sudah POSTED.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 's1t2u3v4w5x6'
down_revision = 'r9s0t1u2v3w4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'penerimaan_barang',
        sa.Column('purchase_invoice_id', UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_penerimaan_barang_purchase_invoice',
        'penerimaan_barang',
        'purchase_invoice',
        ['purchase_invoice_id'],
        ['id'],
    )
    op.create_index(
        'ix_penerimaan_barang_purchase_invoice_id',
        'penerimaan_barang',
        ['purchase_invoice_id'],
    )


def downgrade():
    op.drop_index(
        'ix_penerimaan_barang_purchase_invoice_id',
        table_name='penerimaan_barang',
    )
    op.drop_constraint(
        'fk_penerimaan_barang_purchase_invoice',
        'penerimaan_barang',
        type_='foreignkey',
    )
    op.drop_column('penerimaan_barang', 'purchase_invoice_id')
