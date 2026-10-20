"""AUD-01 + AUD-12 — document_cancellation & pembalikan PPh 23 oleh retur.

AUD-01 (temuan audit 7-a, prioritas TINGGI): seluruh endpoint /cancel bisnis
saat ini berupa HARD DELETE fisik — dokumen + jurnal POSTED dihapus permanen
dan satu-satunya jejak adalah snapshot JSON di deleted_document_log. Migrasi
ini membuat tabel ``document_cancellation`` sebagai audit trail pembatalan
dokumen POSTED via JURNAL PEMBALIK + status BATAL/DIBATALKAN (reversal-first,
pola yang sudah dipakai cancel jurnal manual / reverse transfer / cancel
identifikasi). Dokumen tanpa jurnal (draft/order) tetap boleh hard delete.

AUD-12 (temuan audit 7-b #6): retur penjualan tidak membalik PPh 23 Dibayar
di Muka (116003) dari Sales Invoice ber-PPh 23, dan retur pembelian tidak
membalik Hutang PPh 23 (213002) dari Purchase Invoice ber-PPh 23 (AUD-09).
Migrasi ini menambah kolom ``pph23_applicable`` / ``pph23`` / ``total_pph23``
di ``sales_retur`` dan ``purchase_retur`` — flag & tarif DISALIN dari invoice
sumber saat create (bukan input user), sehingga ``grand_total`` retur menjadi
NET-of-PPh 23 dan jurnal retur membalik akun pajak secara proporsional.

Idempotent & reversible: add_column hanya bila kolom belum ada; create_table
hanya bila tabel belum ada; downgrade drop kolom + tabel.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'cp0000000017'
down_revision = 'cp0000000016'
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _columns(bind, table: str) -> set:
    return {c['name'] for c in sa.inspect(bind).get_columns(table)}


def upgrade():
    bind = op.get_bind()

    # === AUD-01: tabel document_cancellation (audit trail pembatalan) ===
    if not _has_table(bind, 'document_cancellation'):
        op.create_table(
            'document_cancellation',
            sa.Column('id', UUID(as_uuid=True), primary_key=True),
            sa.Column('document_type', sa.String(40), nullable=False),
            sa.Column('document_id', UUID(as_uuid=True), nullable=False),
            sa.Column('document_number', sa.String(60), nullable=True),
            sa.Column('document_date', sa.DateTime(timezone=True), nullable=True),
            sa.Column('document_status', sa.String(30), nullable=True),
            sa.Column('total_amount', sa.Numeric(18, 2), nullable=True),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('mode', sa.String(20), nullable=False, server_default='REVERSAL'),
            sa.Column('reversal_jurnal_ids', sa.dialects.postgresql.JSONB(), nullable=True),
            sa.Column('cancelled_by', UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=True),
            sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        )
        op.create_index('ix_document_cancellation_document_id', 'document_cancellation', ['document_id'])
        op.create_index('ix_document_cancellation_document_type', 'document_cancellation', ['document_type'])

    # === AUD-12: kolom PPh 23 di sales_retur + purchase_retur ===
    for table in ('sales_retur', 'purchase_retur'):
        cols = _columns(bind, table)
        if 'pph23_applicable' not in cols:
            op.add_column(table, sa.Column('pph23_applicable', sa.Boolean(),
                                           nullable=False, server_default='false'))
        if 'pph23' not in cols:
            op.add_column(table, sa.Column('pph23', sa.Numeric(5, 2),
                                           nullable=False, server_default='2'))
        if 'total_pph23' not in cols:
            op.add_column(table, sa.Column('total_pph23', sa.Numeric(18, 2),
                                           nullable=False, server_default='0'))


def downgrade():
    bind = op.get_bind()

    # AUD-12: lepas kolom PPh 23 retur
    for table in ('sales_retur', 'purchase_retur'):
        cols = _columns(bind, table)
        for col in ('total_pph23', 'pph23', 'pph23_applicable'):
            if col in cols:
                op.drop_column(table, col)

    # AUD-01: lepas tabel document_cancellation
    if _has_table(bind, 'document_cancellation'):
        op.drop_index('ix_document_cancellation_document_type', table_name='document_cancellation')
        op.drop_index('ix_document_cancellation_document_id', table_name='document_cancellation')
        op.drop_table('document_cancellation')
