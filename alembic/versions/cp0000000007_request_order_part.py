"""Request Order Part — tabel baru modul Produksi (pre-PO dari Engineering).

Catatan update (Request Order Part):
1. Tabel `request_order_part` — header form: no_rop ("ROP ASI/YYYY/NNN",
   sequence tahunan via get_nomor_dokumen_tahunan), tanggal, project_name,
   model, sales_order_id (customer ditarik otomatis → pelanggan_id
   denormalisasi), catatan (kolom Note), 4 kolom nama tanda tangan
   (dibuat_oleh / diperiksa_oleh / diketahui_oleh / disetujui_oleh),
   status DRAFT/DISETUJUI, created_by.
2. Tabel `request_order_part_item` — baris part dengan dua kelompok kolom:
   ORDER (id_part, description, material, qty) dan STOK (qs = quantity stok,
   supplier_id, keterangan). Cascade delete mengikuti header.

Distribusi salinan cetak form: PUTIH = PURCHASING, KUNING = PRODUKSI,
MERAH = ENGINEERING. Dokumen ringan — tidak posting jurnal / stok.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'cp0000000007'
down_revision = 'cp0000000006'
branch_labels = None
depends_on = None

STATUS_ENUM = 'statusrequestorderpart'


def upgrade() -> None:
    op.create_table(
        'request_order_part',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('no_rop', sa.String(length=40), nullable=False),
        sa.Column('tanggal', sa.DateTime(timezone=True), nullable=False),
        sa.Column('project_name', sa.String(length=200), nullable=False),
        sa.Column('model', sa.String(length=200), nullable=True),
        sa.Column('sales_order_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('pelanggan_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('catatan', sa.Text(), nullable=True),
        sa.Column('dibuat_oleh', sa.String(length=150), nullable=True),
        sa.Column('diperiksa_oleh', sa.String(length=150), nullable=True),
        sa.Column('diketahui_oleh', sa.String(length=150), nullable=True),
        sa.Column('disetujui_oleh', sa.String(length=150), nullable=True),
        sa.Column('status', sa.Enum('DRAFT', 'DISETUJUI', name=STATUS_ENUM),
                  server_default='DRAFT', nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['sales_order_id'], ['sales_order.id'], ),
        sa.ForeignKeyConstraint(['pelanggan_id'], ['pelanggan.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['pengguna.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('no_rop'),
    )
    op.create_index(op.f('ix_request_order_part_no_rop'), 'request_order_part', ['no_rop'], unique=False)
    op.create_index(op.f('ix_request_order_part_sales_order_id'), 'request_order_part', ['sales_order_id'], unique=False)
    op.create_index(op.f('ix_request_order_part_pelanggan_id'), 'request_order_part', ['pelanggan_id'], unique=False)

    op.create_table(
        'request_order_part_item',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('request_order_part_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('no', sa.Integer(), nullable=False),
        sa.Column('id_part', sa.String(length=100), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('material', sa.String(length=200), nullable=True),
        sa.Column('qty', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('qs', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('supplier_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('keterangan', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(['request_order_part_id'], ['request_order_part.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['supplier_id'], ['supplier.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_request_order_part_item_request_order_part_id'),
                    'request_order_part_item', ['request_order_part_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_request_order_part_item_request_order_part_id'),
                  table_name='request_order_part_item')
    op.drop_table('request_order_part_item')
    op.drop_index(op.f('ix_request_order_part_pelanggan_id'), table_name='request_order_part')
    op.drop_index(op.f('ix_request_order_part_sales_order_id'), table_name='request_order_part')
    op.drop_index(op.f('ix_request_order_part_no_rop'), table_name='request_order_part')
    op.drop_table('request_order_part')
    sa.Enum(name=STATUS_ENUM).drop(op.get_bind(), checkfirst=True)
