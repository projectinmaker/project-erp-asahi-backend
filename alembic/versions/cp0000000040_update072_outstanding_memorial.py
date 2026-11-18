"""Update #072 — Outstanding Memorial: Voucher Memorial jenis HUTANG.

Fitur Outstanding Memorial (uraian hutang & piutang modul Kas & Bank)
menjadikan aksi Gagal Tagih berlaku untuk HUTANG juga — mirror penghapusan
piutang #044:

1. Tabel ``voucher_memorial``:
   - kolom ``jenis`` VARCHAR(10) NOT NULL DEFAULT 'PIUTANG' ('PIUTANG' | 'HUTANG');
   - ``pelanggan_id`` menjadi NULLABLE (kosong untuk jenis HUTANG);
   - kolom baru ``supplier_id`` (FK supplier.id, NULLABLE) — pihak jenis HUTANG.
2. Tabel ``voucher_memorial_detail``:
   - ``sales_invoice_id`` menjadi NULLABLE (kosong untuk jenis HUTANG);
   - kolom baru ``purchase_invoice_id`` (FK purchase_invoice.id, NULLABLE).

Data lama tidak berubah: semua baris existing bernilai jenis 'PIUTANG'
(default) dan tetap memakai pelanggan_id + sales_invoice_id.

Jurnal posting jenis HUTANG (voucher_memorial_service.post_voucher_memorial):

    Dr  Hutang Usaha (kontrol per detail, dikelompokkan per akun)
    Cr  Pendapatan Penghapusan Hutang  (akun_beban_id / Setting Akun
        PENDAPATAN_PENGHAPUSAN_HUTANG — fallback akun 421004 Pendapatan
        Lain-lain)

``settlement_service.balances`` ikut memotong sisa HUTANG dengan penghapusan
aktif jenis HUTANG (mirror total_gagal_tagih piutang #044).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'cp0000000040'
down_revision = 'rbac00000003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Header: jenis + supplier_id; pelanggan_id jadi nullable.
    op.add_column(
        'voucher_memorial',
        sa.Column('jenis', sa.String(10), nullable=False, server_default='PIUTANG'),
    )
    op.add_column(
        'voucher_memorial',
        sa.Column('supplier_id', UUID(as_uuid=True), sa.ForeignKey('supplier.id'), nullable=True),
    )
    op.alter_column('voucher_memorial', 'pelanggan_id', existing_type=UUID(as_uuid=True), nullable=True)

    # 2) Detail: purchase_invoice_id; sales_invoice_id jadi nullable.
    op.add_column(
        'voucher_memorial_detail',
        sa.Column('purchase_invoice_id', UUID(as_uuid=True), sa.ForeignKey('purchase_invoice.id'), nullable=True),
    )
    op.alter_column('voucher_memorial_detail', 'sales_invoice_id', existing_type=UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    # Kolom jenis HUTANG dihapus; kolom lama dikembalikan NOT NULL.
    # (Baris jenis HUTANG — bila ada — harus dibersihkan dulu secara manual.)
    op.alter_column('voucher_memorial_detail', 'sales_invoice_id', existing_type=UUID(as_uuid=True), nullable=False)
    op.drop_column('voucher_memorial_detail', 'purchase_invoice_id')
    op.alter_column('voucher_memorial', 'pelanggan_id', existing_type=UUID(as_uuid=True), nullable=False)
    op.drop_column('voucher_memorial', 'supplier_id')
    op.drop_column('voucher_memorial', 'jenis')
