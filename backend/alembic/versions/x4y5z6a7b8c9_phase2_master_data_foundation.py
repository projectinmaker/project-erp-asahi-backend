"""phase2_master_data_foundation

Phase 2 — Master Data Foundation (Master Roadmap §9).

Tambah kolom baru ke 6 tabel master data sesuai target P0:

1. pelanggan:
   - nitku (VARCHAR 50, nullable)            — NITKU untuk e-Faktur
   - syarat_bayar_id (FK → syarat_bayar.id)  — ganti string syarat_bayar_default
   - credit_limit (NUMERIC 18,2, nullable)
   - tax_status (VARCHAR 20, nullable)        — PKP / NON_PKP / default

2. supplier:
   - nitku, syarat_bayar_id, credit_limit, tax_status   (sama dengan pelanggan)

3. barang:
   - item_type (ENUM itemtypebarang, nullable)  — BARANG_DAGANG/BARANG_JADI/BARANG_BAKU/BARANG_BANTU/JASA
   - akun_hpp_id (FK → akun_perkiraan.id, nullable)
   - akun_penjualan_id (FK → akun_perkiraan.id, nullable)
   - stock_item (BOOLEAN, default TRUE)

4. gudang:
   - company_id (FK → organization_unit.id, nullable)
   - branch_id (FK → organization_unit.id, nullable)

5. kategori_aset:
   - akun_aset_id (FK → akun_perkiraan.id, nullable)
   - akun_akumulasi_id (FK → akun_perkiraan.id, nullable)
   - akun_beban_id (FK → akun_perkiraan.id, nullable)
   - default_useful_life (INTEGER, nullable)               — dalam bulan
   - default_method (ENUM metodepenyusutan, nullable)      — reuse enum dari aset_tetap

6. kas_bank_akun:
   - currency (VARCHAR 3, NOT NULL DEFAULT 'IDR')

Backfill:
- pelanggan/supplier: link syarat_bayar_default string → syarat_bayar_id FK
  (cari row di syarat_bayar dengan nama = string tsb; kalau tidak ketemu, buat default)
- barang: copy jenis_barang string → item_type enum (kalau match, kalau tidak null)

Catatan:
- Kolom lama tetap dipertahankan untuk backward compat (syarat_bayar_default,
  jenis_barang). Service code baca FK baru kalau tidak null, fallback ke string
  lama kalau null. Kolom lama di-drop di Phase berikutnya setelah migration
  stabil (expand → backfill → switch → cleanup, sesuai Roadmap §33).
- Tidak ada data yang dihapus. Backfill idempotent.

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-09-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "x4y5z6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "w3x4y5z6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================
    # 1. ENUM TYPES BARU
    # ==========================================
    # ItemTypeBarang — untuk barang.item_type
    # Note: ALTER TYPE ADD VALUE tidak bisa dalam transaction, pakai autocommit_block
    with op.get_context().autocommit_block():
        op.execute(
            "DO $$ BEGIN "
            "  CREATE TYPE itemtypebarang AS ENUM "
            "  ('BARANG_DAGANG', 'BARANG_JADI', 'BARANG_BAKU', 'BARANG_BANTU', 'JASA'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
        )
        # metodepenyusutan — reuse dari aset_tetap (sudah ada di DB)

    # ==========================================
    # 2. PELANGGAN — tambah 4 kolom baru
    # ==========================================
    op.add_column('pelanggan', sa.Column('nitku', sa.String(50), nullable=True))
    op.add_column('pelanggan', sa.Column('syarat_bayar_id',
                                         sa.dialects.postgresql.UUID(as_uuid=True),
                                         nullable=True))
    op.add_column('pelanggan', sa.Column('credit_limit', sa.Numeric(18, 2), nullable=True))
    op.add_column('pelanggan', sa.Column('tax_status', sa.String(20), nullable=True))
    op.create_foreign_key(
        'fk_pelanggan_syarat_bayar', 'pelanggan', 'syarat_bayar',
        ['syarat_bayar_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_pelanggan_syarat_bayar_id', 'pelanggan', ['syarat_bayar_id'])

    # ==========================================
    # 3. SUPPLIER — tambah 4 kolom baru (sama dengan pelanggan)
    # ==========================================
    op.add_column('supplier', sa.Column('nitku', sa.String(50), nullable=True))
    op.add_column('supplier', sa.Column('syarat_bayar_id',
                                         sa.dialects.postgresql.UUID(as_uuid=True),
                                         nullable=True))
    op.add_column('supplier', sa.Column('credit_limit', sa.Numeric(18, 2), nullable=True))
    op.add_column('supplier', sa.Column('tax_status', sa.String(20), nullable=True))
    op.create_foreign_key(
        'fk_supplier_syarat_bayar', 'supplier', 'syarat_bayar',
        ['syarat_bayar_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_supplier_syarat_bayar_id', 'supplier', ['syarat_bayar_id'])

    # ==========================================
    # 4. BARANG — tambah 4 kolom baru
    # ==========================================
    op.add_column('barang', sa.Column('item_type',
                                       sa.Enum(name='itemtypebarang', native_enum=True),
                                       nullable=True))
    op.add_column('barang', sa.Column('akun_hpp_id',
                                       sa.dialects.postgresql.UUID(as_uuid=True),
                                       nullable=True))
    op.add_column('barang', sa.Column('akun_penjualan_id',
                                       sa.dialects.postgresql.UUID(as_uuid=True),
                                       nullable=True))
    op.add_column('barang', sa.Column('stock_item', sa.Boolean(),
                                       nullable=False, server_default=sa.true()))
    op.create_foreign_key('fk_barang_akun_hpp', 'barang', 'akun_perkiraan',
                          ['akun_hpp_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_barang_akun_penjualan', 'barang', 'akun_perkiraan',
                          ['akun_penjualan_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_barang_item_type', 'barang', ['item_type'])
    op.create_index('ix_barang_akun_hpp_id', 'barang', ['akun_hpp_id'])
    op.create_index('ix_barang_akun_penjualan_id', 'barang', ['akun_penjualan_id'])

    # ==========================================
    # 5. GUDANG — tambah 2 kolom baru (organization scope)
    # ==========================================
    op.add_column('gudang', sa.Column('company_id',
                                       sa.dialects.postgresql.UUID(as_uuid=True),
                                       nullable=True))
    op.add_column('gudang', sa.Column('branch_id',
                                       sa.dialects.postgresql.UUID(as_uuid=True),
                                       nullable=True))
    op.create_foreign_key('fk_gudang_company', 'gudang', 'organization_unit',
                          ['company_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_gudang_branch', 'gudang', 'organization_unit',
                          ['branch_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_gudang_company_id', 'gudang', ['company_id'])
    op.create_index('ix_gudang_branch_id', 'gudang', ['branch_id'])

    # ==========================================
    # 6. KATEGORI ASET — tambah 5 kolom baru
    # ==========================================
    op.add_column('kategori_aset', sa.Column('akun_aset_id',
                                              sa.dialects.postgresql.UUID(as_uuid=True),
                                              nullable=True))
    op.add_column('kategori_aset', sa.Column('akun_akumulasi_id',
                                              sa.dialects.postgresql.UUID(as_uuid=True),
                                              nullable=True))
    op.add_column('kategori_aset', sa.Column('akun_beban_id',
                                              sa.dialects.postgresql.UUID(as_uuid=True),
                                              nullable=True))
    op.add_column('kategori_aset', sa.Column('default_useful_life', sa.Integer(), nullable=True))
    op.add_column('kategori_aset', sa.Column('default_method',
                                              sa.Enum(name='metodepenyusutan', native_enum=True),
                                              nullable=True))
    op.create_foreign_key('fk_kategori_aset_akun_aset', 'kategori_aset', 'akun_perkiraan',
                          ['akun_aset_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_kategori_aset_akun_akumulasi', 'kategori_aset', 'akun_perkiraan',
                          ['akun_akumulasi_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_kategori_aset_akun_beban', 'kategori_aset', 'akun_perkiraan',
                          ['akun_beban_id'], ['id'], ondelete='SET NULL')

    # ==========================================
    # 7. KAS_BANK_AKUN — tambah currency
    # ==========================================
    op.add_column('kas_bank_akun', sa.Column('currency', sa.String(3),
                                              nullable=False, server_default='IDR'))
    op.create_index('ix_kas_bank_akun_currency', 'kas_bank_akun', ['currency'])

    # ==========================================
    # 8. BACKFILL DATA EXISTING
    # ==========================================
    with op.get_context().autocommit_block():
        # Backfill pelanggan.syarat_bayar_id dari string syarat_bayar_default
        # Pastikan minimal 1 row syarat_bayar dengan nama='Tunai' ada (buat kalau belum)
        op.execute(
            "INSERT INTO syarat_bayar (id, nama, hari, created_at, updated_at) "
            "SELECT gen_random_uuid(), 'Tunai', NULL, now(), now() "
            "WHERE NOT EXISTS (SELECT 1 FROM syarat_bayar WHERE nama = 'Tunai')"
        )

        # Link pelanggan yang punya syarat_bayar_default string ke syarat_bayar row
        op.execute(
            """
            UPDATE pelanggan p
            SET syarat_bayar_id = sb.id
            FROM syarat_bayar sb
            WHERE p.syarat_bayar_default IS NOT NULL
              AND p.syarat_bayar_default != ''
              AND LOWER(TRIM(p.syarat_bayar_default)) = LOWER(TRIM(sb.nama))
              AND p.syarat_bayar_id IS NULL
            """
        )
        # Untuk pelanggan yang syarat_bayar_default='Tunai' tapi tidak ketemu match,
        # link ke row 'Tunai' default
        op.execute(
            """
            UPDATE pelanggan p
            SET syarat_bayar_id = (SELECT id FROM syarat_bayar WHERE nama = 'Tunai' LIMIT 1)
            WHERE p.syarat_bayar_default = 'Tunai'
              AND p.syarat_bayar_id IS NULL
            """
        )

        # Backfill supplier (sama logic)
        op.execute(
            """
            UPDATE supplier s
            SET syarat_bayar_id = sb.id
            FROM syarat_bayar sb
            WHERE s.syarat_bayar_default IS NOT NULL
              AND s.syarat_bayar_default != ''
              AND LOWER(TRIM(s.syarat_bayar_default)) = LOWER(TRIM(sb.nama))
              AND s.syarat_bayar_id IS NULL
            """
        )
        op.execute(
            """
            UPDATE supplier s
            SET syarat_bayar_id = (SELECT id FROM syarat_bayar WHERE nama = 'Tunai' LIMIT 1)
            WHERE s.syarat_bayar_default = 'Tunai'
              AND s.syarat_bayar_id IS NULL
            """
        )

        # Backfill barang.item_type dari jenis_barang string
        # Mapping: 'BARANG_DAGANG' / 'DAGANG' → BARANG_DAGANG
        #          'BARANG_JADI' / 'JADI'    → BARANG_JADI
        #          'BARANG_BAKU' / 'BAKU'    → BARANG_BAKU
        #          'BARANG_BANTU' / 'BANTU'  → BARANG_BANTU
        #          'JASA'                     → JASA
        op.execute(
            """
            UPDATE barang SET item_type = 'BARANG_DAGANG'
            WHERE jenis_barang ILIKE '%DAGANG%' AND item_type IS NULL
            """
        )
        op.execute(
            """
            UPDATE barang SET item_type = 'BARANG_JADI'
            WHERE jenis_barang ILIKE '%JADI%' AND item_type IS NULL
            """
        )
        op.execute(
            """
            UPDATE barang SET item_type = 'BARANG_BAKU'
            WHERE jenis_barang ILIKE '%BAKU%' AND item_type IS NULL
            """
        )
        op.execute(
            """
            UPDATE barang SET item_type = 'BARANG_BANTU'
            WHERE jenis_barang ILIKE '%BANTU%' AND item_type IS NULL
            """
        )
        op.execute(
            """
            UPDATE barang SET item_type = 'JASA'
            WHERE jenis_barang ILIKE '%JASA%' AND item_type IS NULL
            """
        )


def downgrade() -> None:
    # Drop semua kolom baru + enum type. Bisa rollback penuh.
    # Catatan: data yang sudah di-backfill tidak di-unbackfill (syarat_bayar_id
    # di-drop, tapi syarat_bayar_default string tetap ada dari sebelumnya).

    # 7. kas_bank_akun
    op.drop_index('ix_kas_bank_akun_currency', table_name='kas_bank_akun')
    op.drop_column('kas_bank_akun', 'currency')

    # 6. kategori_aset
    op.drop_constraint('fk_kategori_aset_akun_beban', 'kategori_aset', type_='foreignkey')
    op.drop_constraint('fk_kategori_aset_akun_akumulasi', 'kategori_aset', type_='foreignkey')
    op.drop_constraint('fk_kategori_aset_akun_aset', 'kategori_aset', type_='foreignkey')
    op.drop_column('kategori_aset', 'default_method')
    op.drop_column('kategori_aset', 'default_useful_life')
    op.drop_column('kategori_aset', 'akun_beban_id')
    op.drop_column('kategori_aset', 'akun_akumulasi_id')
    op.drop_column('kategori_aset', 'akun_aset_id')

    # 5. gudang
    op.drop_index('ix_gudang_branch_id', table_name='gudang')
    op.drop_index('ix_gudang_company_id', table_name='gudang')
    op.drop_constraint('fk_gudang_branch', 'gudang', type_='foreignkey')
    op.drop_constraint('fk_gudang_company', 'gudang', type_='foreignkey')
    op.drop_column('gudang', 'branch_id')
    op.drop_column('gudang', 'company_id')

    # 4. barang
    op.drop_index('ix_barang_akun_penjualan_id', table_name='barang')
    op.drop_index('ix_barang_akun_hpp_id', table_name='barang')
    op.drop_index('ix_barang_item_type', table_name='barang')
    op.drop_constraint('fk_barang_akun_penjualan', 'barang', type_='foreignkey')
    op.drop_constraint('fk_barang_akun_hpp', 'barang', type_='foreignkey')
    op.drop_column('barang', 'stock_item')
    op.drop_column('barang', 'akun_penjualan_id')
    op.drop_column('barang', 'akun_hpp_id')
    op.drop_column('barang', 'item_type')

    # 3. supplier
    op.drop_index('ix_supplier_syarat_bayar_id', table_name='supplier')
    op.drop_constraint('fk_supplier_syarat_bayar', 'supplier', type_='foreignkey')
    op.drop_column('supplier', 'tax_status')
    op.drop_column('supplier', 'credit_limit')
    op.drop_column('supplier', 'syarat_bayar_id')
    op.drop_column('supplier', 'nitku')

    # 2. pelanggan
    op.drop_index('ix_pelanggan_syarat_bayar_id', table_name='pelanggan')
    op.drop_constraint('fk_pelanggan_syarat_bayar', 'pelanggan', type_='foreignkey')
    op.drop_column('pelanggan', 'tax_status')
    op.drop_column('pelanggan', 'credit_limit')
    op.drop_column('pelanggan', 'syarat_bayar_id')
    op.drop_column('pelanggan', 'nitku')

    # 1. Drop enum type baru
    op.execute("DROP TYPE IF EXISTS itemtypebarang")
