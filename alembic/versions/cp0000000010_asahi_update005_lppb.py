"""LPPB — Laporan Pemeriksaan dan Pengujian Barang (modul Pembelian, update ASAHI #005).

Catatan update (LPPB):
1. Tabel `lppb` — header dokumen QC barang masuk: no_form ("LPPB ASI/YYYY/NNN",
   sequence tahunan via get_nomor_dokumen_tahunan), tanggal (Tanggal LPPB),
   tanggal_terima (Tgl Terima), purchase_order_id (Nomor PO — sumber tarik
   item), supplier_id (Vendor, auto dari PO), nomor_do (surat jalan vendor —
   teks manual), nomor_ri (nomor referensi inspeksi eksternal — teks manual),
   proyek (teks manual), revisi + no_format (kontrol dokumen QMS untuk kop/
   footer), keterangan (baris keterangan di bawah tabel), mengetahui_jabatan +
   mengetahui_nama (blok pengesahan), status DRAFT/SELESAI (pola ROP #003:
   Simpan -> Selesaikan, tanpa upacara approval), created_by.
2. Tabel `lppb_detail` — baris pemeriksaan/pengujian: urutan, link opsional ke
   barang + baris PO (baris jasa pengujian seperti NDT boleh manual tanpa
   barang — dibuat berpasangan di bawah barangnya), snapshot kode material /
   deskripsi spesifikasi / satuan, qty_po, qty_penerimaan (diterima kali ini),
   qty_baik + qty_tidak_baik (Hasil Pengujian dua sub-kolom), qty_sudah_diterima
   (akumulasi TERMASUK kali ini), qty_belum_diterima (sisa PO).

Dokumen RECORD QMS — tidak posting stok / jurnal (Penerimaan tetap pencatat
stok gudang).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'cp0000000010'
down_revision = 'cp0000000009'
branch_labels = None
depends_on = None

# LPPB memakai enum status yang sudah ada (statuspenjualan: DRAFT/DIPROSES/SELESAI/DIBATALKAN)
STATUS_ENUM = 'statuspenjualan'


def upgrade() -> None:
    op.create_table(
        'lppb',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('no_form', sa.String(length=40), nullable=False),
        sa.Column('tanggal', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tanggal_terima', sa.DateTime(timezone=True), nullable=True),
        sa.Column('purchase_order_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('supplier_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('nomor_do', sa.String(length=100), nullable=True),
        sa.Column('nomor_ri', sa.String(length=100), nullable=True),
        sa.Column('proyek', sa.String(length=200), nullable=True),
        sa.Column('revisi', sa.String(length=20), nullable=True),
        sa.Column('no_format', sa.String(length=50), nullable=True),
        sa.Column('keterangan', sa.Text(), nullable=True),
        sa.Column('mengetahui_jabatan', sa.String(length=100), nullable=True),
        sa.Column('mengetahui_nama', sa.String(length=150), nullable=True),
        sa.Column('status', postgresql.ENUM('DRAFT', 'DIPROSES', 'SELESAI', 'DIBATALKAN', name=STATUS_ENUM, create_type=False),
                  server_default='DRAFT', nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_order.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['supplier.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['pengguna.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('no_form'),
    )
    op.create_index(op.f('ix_lppb_no_form'), 'lppb', ['no_form'], unique=False)
    op.create_index(op.f('ix_lppb_purchase_order_id'), 'lppb', ['purchase_order_id'], unique=False)
    op.create_index(op.f('ix_lppb_supplier_id'), 'lppb', ['supplier_id'], unique=False)

    op.create_table(
        'lppb_detail',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lppb_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('urutan', sa.Integer(), nullable=False),
        sa.Column('barang_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('purchase_order_detail_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('kode_material', sa.String(length=100), nullable=True),
        sa.Column('deskripsi_spesifikasi', sa.Text(), nullable=False),
        sa.Column('satuan', sa.String(length=50), nullable=True),
        sa.Column('qty_po', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('qty_penerimaan', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('qty_baik', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('qty_tidak_baik', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('qty_sudah_diterima', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('qty_belum_diterima', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['lppb_id'], ['lppb.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['barang_id'], ['barang.id'], ),
        sa.ForeignKeyConstraint(['purchase_order_detail_id'], ['purchase_order_detail.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_lppb_detail_lppb_id'), 'lppb_detail', ['lppb_id'], unique=False)
    op.create_index(op.f('ix_lppb_detail_barang_id'), 'lppb_detail', ['barang_id'], unique=False)
    op.create_index(op.f('ix_lppb_detail_purchase_order_detail_id'), 'lppb_detail', ['purchase_order_detail_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_lppb_detail_purchase_order_detail_id'), table_name='lppb_detail')
    op.drop_index(op.f('ix_lppb_detail_barang_id'), table_name='lppb_detail')
    op.drop_index(op.f('ix_lppb_detail_lppb_id'), table_name='lppb_detail')
    op.drop_table('lppb_detail')
    op.drop_index(op.f('ix_lppb_supplier_id'), table_name='lppb')
    op.drop_index(op.f('ix_lppb_purchase_order_id'), table_name='lppb')
    op.drop_index(op.f('ix_lppb_no_form'), table_name='lppb')
    op.drop_table('lppb')
    # enum statuspenjualan dipakai tabel lain — JANGAN di-drop
