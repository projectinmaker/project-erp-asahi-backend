"""Mata uang & alamat pengiriman (master) + kolom PO + alamat 2 baris.

Update ASAHI #3 (cetak Purchase Order & pengaturan):
1. Tabel mata_uang — daftar mata uang untuk dropdown Currency form SO/PO.
   Seed: IDR, USD, EUR (Euro), CNY (China Yuan), SGD (Singapore Dollar).
2. Tabel alamat_pengiriman — daftar gudang tujuan untuk PO; user wajib
   memilih SATU via checkbox saat input PO. Seed 5 gudang Asahi.
3. Kolom baru purchase_order: alamat_pengiriman_id (FK, SET NULL),
   alamat_pengiriman (snapshot teks untuk cetak), ppic (opsional).
4. company_profile.alamat default dipecah 2 baris setelah "Jatireja"
   (alamat satu baris terlalu panjang di kop cetak) — hanya untuk baris
   yang masih memakai nilai default lama.
"""
import sqlalchemy as sa
from alembic import op

revision = 'cp0000000002'
down_revision = 'cp0000000001'
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. Tabel mata_uang + seed ─────────────────────────────────────────
    op.create_table(
        'mata_uang',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('kode', sa.String(8), nullable=False, unique=True),
        sa.Column('nama', sa.String(100), nullable=False),
        sa.Column('is_aktif', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_mata_uang_kode', 'mata_uang', ['kode'])
    op.execute(
        "INSERT INTO mata_uang (kode, nama, is_aktif) VALUES "
        "('IDR', 'Rupiah', true), "
        "('USD', 'US Dollar', true), "
        "('EUR', 'Euro', true), "
        "('CNY', 'China Yuan', true), "
        "('SGD', 'Singapore Dollar', true)"
    )

    # ── 2. Tabel alamat_pengiriman + seed 5 gudang ────────────────────────
    op.create_table(
        'alamat_pengiriman',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('prefix', sa.String(200), nullable=False, server_default='PT ASAHI SUKSES INDUSTRI'),
        sa.Column('nama', sa.String(200), nullable=False),
        sa.Column('is_aktif', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.execute(
        "INSERT INTO alamat_pengiriman (prefix, nama, is_aktif) VALUES "
        "('PT ASAHI SUKSES INDUSTRI', 'P I - GUDANG ATAS TOOL', true), "
        "('PT ASAHI SUKSES INDUSTRI', 'P I - GUDANG ATAS PART', true), "
        "('PT ASAHI SUKSES INDUSTRI', 'P I GUDANG BAWAH', true), "
        "('PT ASAHI SUKSES INDUSTRI', 'P II GUDANG BAWAH', true), "
        "('PT ASAHI SUKSES INDUSTRI', 'P II GUDANG MATERIAL', true)"
    )

    # ── 3. Kolom baru purchase_order ──────────────────────────────────────
    op.add_column('purchase_order', sa.Column('alamat_pengiriman_id', sa.UUID(as_uuid=True), nullable=True))
    op.add_column('purchase_order', sa.Column('alamat_pengiriman', sa.Text(), nullable=True))
    op.add_column('purchase_order', sa.Column('ppic', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.create_index('ix_purchase_order_alamat_pengiriman_id', 'purchase_order', ['alamat_pengiriman_id'])
    op.create_foreign_key(
        'fk_po_alamat_pengiriman', 'purchase_order', 'alamat_pengiriman',
        ['alamat_pengiriman_id'], ['id'], ondelete='SET NULL',
    )

    # ── 4. Alamat perusahaan default → 2 baris (enter setelah "Jatireja") ─
    # Hanya baris yang masih memakai default lama yang disentuh; alamat yang
    # sudah dikustomisasi user tidak diubah.
    op.execute(
        "UPDATE company_profile SET alamat = "
        "'Jalan Simpangan No.18, RT.03/RW.06, Jatireja,\n"
        "Kec. Cikarang Tim., Kabupaten Bekasi, Jawa Barat 17530' "
        "WHERE alamat = 'Jalan Simpangan No.18, RT.03/RW.06, Jatireja, "
        "Kec. Cikarang Tim., Kabupaten Bekasi, Jawa Barat 17530'"
    )


def downgrade():
    op.drop_constraint('fk_po_alamat_pengiriman', 'purchase_order', type_='foreignkey')
    op.drop_index('ix_purchase_order_alamat_pengiriman_id', table_name='purchase_order')
    op.drop_column('purchase_order', 'ppic')
    op.drop_column('purchase_order', 'alamat_pengiriman')
    op.drop_column('purchase_order', 'alamat_pengiriman_id')
    op.drop_table('alamat_pengiriman')
    op.drop_table('mata_uang')
    # Kembalikan alamat default ke satu baris (best effort).
    op.execute(
        "UPDATE company_profile SET alamat = "
        "'Jalan Simpangan No.18, RT.03/RW.06, Jatireja, Kec. Cikarang Tim., "
        "Kabupaten Bekasi, Jawa Barat 17530' "
        "WHERE alamat = 'Jalan Simpangan No.18, RT.03/RW.06, Jatireja,\n"
        "Kec. Cikarang Tim., Kabupaten Bekasi, Jawa Barat 17530'"
    )
