"""phase6_stok_kartu_valuasi

Phase 6 — Stok Kartu + Metode Valuasi:
1. Tambah enum 'metodevaluasi' (AVERAGE, FIFO, FEFO)
2. Tambah kolom 'metode_valuasi' di tabel 'barang'
3. Tambah kolom valuasi di tabel 'stok_mutasi' (harga_satuan, total_nilai, saldo_nilai_sebelum, saldo_nilai_sesudah)
4. Buat tabel baru 'stok_kartu_layer' untuk tracking FIFO/FEFO

Revision ID: g7h8i9j0k1l2
Revises: b2c3d4e5f6
Create Date: 2026-09-03 10:00:00.000000

NOTE: Sesuaikan 'down_revision' dengan revision ID terakhir di project kamu.
Jika ada migration lain setelah b2c3d4e5f6, gunakan revision ID terakhir.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'g7h8i9j0k1l2'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 1. Buat enum metodevaluasi ----
    metodevaluasi_enum = postgresql.ENUM(
        'AVERAGE', 'FIFO', 'FEFO',
        name='metodevaluasi', create_type=True,
    )
    metodevaluasi_enum.create(op.get_bind(), checkfirst=True)

    # ---- 2. Tambah kolom metode_valuasi di tabel barang ----
    op.add_column(
        'barang',
        sa.Column(
            'metode_valuasi',
            postgresql.ENUM('AVERAGE', 'FIFO', 'FEFO', name='metodevaluasi', create_type=False),
            nullable=False,
            server_default='AVERAGE',
        ),
    )

    # ---- 3. Tambah kolom valuasi di tabel stok_mutasi ----
    op.add_column('stok_mutasi', sa.Column('harga_satuan', sa.Numeric(18, 2), nullable=True))
    op.add_column('stok_mutasi', sa.Column('total_nilai', sa.Numeric(18, 2), nullable=True))
    op.add_column('stok_mutasi', sa.Column('saldo_nilai_sebelum', sa.Numeric(18, 2), nullable=True))
    op.add_column('stok_mutasi', sa.Column('saldo_nilai_sesudah', sa.Numeric(18, 2), nullable=True))

    # ---- 4. Buat tabel stok_kartu_layer ----
    op.create_table(
        'stok_kartu_layer',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            'barang_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('barang.id'), nullable=False, index=True,
        ),
        sa.Column(
            'gudang_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('gudang.id'), nullable=True,
        ),
        sa.Column('harga_satuan', sa.Numeric(18, 2), nullable=False),
        sa.Column('qty_masuk', sa.Integer(), nullable=False),
        sa.Column('qty_sisa', sa.Integer(), nullable=False),
        sa.Column('tanggal_masuk', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ref_module', postgresql.ENUM(
            'PEMBAYARAN', 'PENERIMAAN', 'TRANSFER_BANK',
            'SALES_ORDER', 'SALES_INVOICE', 'SALES_RETUR',
            'PURCHASE_ORDER', 'PURCHASE_INVOICE', 'PURCHASE_RETUR',
            'PENYESUAIAN_STOK', 'PENYUSUTAN', 'SALDO_AWAL',
            'PENUTUPAN_PERIODE', 'MANUAL',
            name='refmodule', create_type=False,
        ), nullable=True),
        sa.Column('ref_no', sa.String(30), nullable=True),
        sa.Column('ref_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Index untuk query layer aktif per barang
    # op.create_index('ix_stok_kartu_layer_barang_id', 'stok_kartu_layer', ['barang_id'])


def downgrade() -> None:
    # Drop tabel stok_kartu_layer
    # op.drop_index('ix_stok_kartu_layer_barang_id', table_name='stok_kartu_layer')
    op.drop_table('stok_kartu_layer')

    # Hapus kolom valuasi dari stok_mutasi
    op.drop_column('stok_mutasi', 'saldo_nilai_sesudah')
    op.drop_column('stok_mutasi', 'saldo_nilai_sebelum')
    op.drop_column('stok_mutasi', 'total_nilai')
    op.drop_column('stok_mutasi', 'harga_satuan')

    # Hapus kolom metode_valuasi dari barang
    op.drop_column('barang', 'metode_valuasi')

    # Drop enum metodevaluasi
    op.execute('DROP TYPE IF EXISTS metodevaluasi')
