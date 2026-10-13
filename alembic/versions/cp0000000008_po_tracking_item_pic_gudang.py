"""PO — tracking item ke SO, kategori belanja, PIC Gudang (update ASAHI #002).

Catatan update (form PO kompleks):
1. `purchase_order_detail` + `sales_order_id` (FK sales_order.id, SET NULL,
   index) — satu PO bisa memuat item dari beberapa SO sekaligus (baris 1-3
   SO A, baris 4-6 SO B); relasi per BARIS. + `jenis_pembelian` — dasar
   belanja (CONSUMABLE/PROJECT/ASSET/ATK) untuk tracking "belanja ini buat
   apa" dan penentu tujuan posting COA saat invoice/penerimaan di-posting.
2. `purchase_order` + `pic_gudang` + `pic_gudang_telepon` — snapshot PIC
   Gudang (kolom Pengiriman PO); terisi otomatis dari master alamat
   pengiriman saat gudang dipilih, bisa disunting manual.
3. `alamat_pengiriman` + `pic_nama` + `pic_telepon` — master PIC per gudang
   tujuan (Gudang 1 siapa PIC-nya, Gudang 2 siapa PIC-nya).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'cp0000000008'
down_revision = 'cp0000000007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Master alamat pengiriman — PIC gudang
    op.add_column('alamat_pengiriman', sa.Column('pic_nama', sa.String(length=100), nullable=True))
    op.add_column('alamat_pengiriman', sa.Column('pic_telepon', sa.String(length=50), nullable=True))

    # 2. PO header — snapshot PIC gudang
    op.add_column('purchase_order', sa.Column('pic_gudang', sa.String(length=100), nullable=True))
    op.add_column('purchase_order', sa.Column('pic_gudang_telepon', sa.String(length=50), nullable=True))

    # 3. PO detail — tracking SO per baris + kategori belanja
    op.add_column('purchase_order_detail', sa.Column('sales_order_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('purchase_order_detail', sa.Column('jenis_pembelian', sa.String(length=20), nullable=True))
    op.create_foreign_key(
        'fk_po_detail_sales_order', 'purchase_order_detail', 'sales_order',
        ['sales_order_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(op.f('ix_purchase_order_detail_sales_order_id'), 'purchase_order_detail', ['sales_order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_purchase_order_detail_sales_order_id'), table_name='purchase_order_detail')
    op.drop_constraint('fk_po_detail_sales_order', 'purchase_order_detail', type_='foreignkey')
    op.drop_column('purchase_order_detail', 'jenis_pembelian')
    op.drop_column('purchase_order_detail', 'sales_order_id')
    op.drop_column('purchase_order', 'pic_gudang_telepon')
    op.drop_column('purchase_order', 'pic_gudang')
    op.drop_column('alamat_pengiriman', 'pic_telepon')
    op.drop_column('alamat_pengiriman', 'pic_nama')
