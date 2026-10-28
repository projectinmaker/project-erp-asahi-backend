"""Update ASAHI #020 — hapus fitur organisasi (company dimension lama).

Fondasi multi-company lama yang setengah jadi dan tidak terpakai dihapus
bersih dari skema database:

1. ``jurnal_umum``: drop 5 kolom dimensi reporting (company_id, branch_id,
   department_id, cost_center_id, project_id) beserta FK & index-nya
   (dibuat oleh q8r9s0t1u2v3).
2. 10 tabel dokumen transaksi (sales_order, sales_invoice, sales_retur,
   purchase_order, purchase_invoice, purchase_retur, penerimaan_kas,
   pembayaran_kas, transfer_bank, penyesuaian_stok): drop kolom
   ``company_id`` + FK + index-nya (dibuat oleh cp0000000020).
3. ``gudang``: drop ``company_id``/``branch_id`` + FK + index-nya (dibuat
   oleh x4y5z6a7b8c9).
4. ``kas_bank_akun``: drop ``company_id`` + FK + index-nya (cp0000000018).
5. ``setting_akun``: drop override per perusahaan (kolom ``company_id``,
   FK, index ``ix_setting_akun_company``, unique ``uq_setting_akun_key_company``)
   lalu buat ulang unique global ``ix_setting_akun_key`` pada ``key``
   (bentuk asli pra-cp18). Baris override perusahaan dihapus terlebih
   dahulu (pola downgrade cp18) supaya unique bisa dibuat.
6. ``penutupan_periode``/``penutupan_tahun``: drop kolom ``company_id`` +
   FK + unique 3-kolom NULLS NOT DISTINCT, kembalikan unique asli
   ``(tahun, bulan)`` / ``(tahun)`` (cp0000000018).
7. Drop tabel ``document_organization``, ``reporting_audit``,
   ``cash_flow_classification``, lalu ``organization_unit``.

Downgrade merekonstruksi struktur lama TANPA data (kolom nullable, tanpa
backfill — dimensi historis tidak dapat direkonstruksi). Multi-company baru
akan dibangun dengan arsitektur berbeda (login → picker perusahaan → header
X-Company-Id → scope penuh), jadi fondasi lama tidak pernah dipulihkan.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'cp0000000024'
down_revision = 'cp0000000023'
branch_labels = None
depends_on = None

FIELDS = ('company_id', 'branch_id', 'department_id', 'cost_center_id', 'project_id')

# Tabel dokumen transaksi yang menerima kolom company_id dari cp0000000020.
_DOC_TABLES = [
    'sales_order', 'sales_invoice', 'sales_retur',
    'purchase_order', 'purchase_invoice', 'purchase_retur',
    'penerimaan_kas', 'pembayaran_kas', 'transfer_bank', 'penyesuaian_stok',
]


def _base_columns():
    """Kolom umum tabel fondasi organisasi (pola q8r9s0t1u2v3)."""
    return [
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    # 1) jurnal_umum — 5 dimensi reporting (q8r9s0t1u2v3).
    for field in FIELDS:
        op.drop_index(f'ix_jurnal_umum_{field}', table_name='jurnal_umum')
        op.drop_constraint(f'fk_jurnal_{field}', 'jurnal_umum', type_='foreignkey')
        op.drop_column('jurnal_umum', field)

    # 2) 10 tabel dokumen (cp0000000020) — FK inline tanpa nama → nama
    #    constraint default Postgres ``{table}_company_id_fkey``.
    for table in _DOC_TABLES:
        op.drop_index(f'ix_{table}_company_id', table_name=table)
        op.drop_constraint(f'{table}_company_id_fkey', table, type_='foreignkey')
        op.drop_column(table, 'company_id')

    # 3) gudang (x4y5z6a7b8c9) — branch dulu, company kemudian.
    op.drop_index('ix_gudang_branch_id', table_name='gudang')
    op.drop_index('ix_gudang_company_id', table_name='gudang')
    op.drop_constraint('fk_gudang_branch', 'gudang', type_='foreignkey')
    op.drop_constraint('fk_gudang_company', 'gudang', type_='foreignkey')
    op.drop_column('gudang', 'branch_id')
    op.drop_column('gudang', 'company_id')

    # 4) kas_bank_akun (cp0000000018).
    op.drop_index('ix_kas_bank_akun_company', table_name='kas_bank_akun')
    op.drop_constraint('fk_kas_bank_akun_company', 'kas_bank_akun', type_='foreignkey')
    op.drop_column('kas_bank_akun', 'company_id')

    # 5) setting_akun (cp0000000018) — kembalikan unique global di key.
    #    Baris override perusahaan dibuang dulu (fallback global tetap ada)
    #    supaya unique (key) tidak bentrok dengan baris yang menumpuk.
    op.drop_index('uq_setting_akun_key_company', table_name='setting_akun')
    op.drop_index('ix_setting_akun_company', table_name='setting_akun')
    op.drop_constraint('fk_setting_akun_company', 'setting_akun', type_='foreignkey')
    op.execute("DELETE FROM setting_akun WHERE company_id IS NOT NULL")
    op.drop_column('setting_akun', 'company_id')
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_setting_akun_key') THEN
                CREATE UNIQUE INDEX ix_setting_akun_key ON setting_akun (key);
            END IF;
        END $$;
    """)

    # 6) penutupan_periode & penutupan_tahun (cp0000000018) — unique global kembali.
    op.execute("ALTER TABLE penutupan_periode DROP CONSTRAINT IF EXISTS uq_penutupan_periode_tahun_bulan_company")
    op.drop_constraint('fk_penutupan_periode_company', 'penutupan_periode', type_='foreignkey')
    op.drop_column('penutupan_periode', 'company_id')
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_penutupan_periode_tahun_bulan') THEN
                ALTER TABLE penutupan_periode ADD CONSTRAINT uq_penutupan_periode_tahun_bulan UNIQUE (tahun, bulan);
            END IF;
        END $$;
    """)

    op.execute("ALTER TABLE penutupan_tahun DROP CONSTRAINT IF EXISTS uq_penutupan_tahun_tahun_company")
    op.drop_constraint('fk_penutupan_tahun_company', 'penutupan_tahun', type_='foreignkey')
    op.drop_column('penutupan_tahun', 'company_id')
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_penutupan_tahun_tahun') THEN
                ALTER TABLE penutupan_tahun ADD CONSTRAINT uq_penutupan_tahun_tahun UNIQUE (tahun);
            END IF;
        END $$;
    """)

    # 7) Tabel fondasi organisasi — semua FK penggunanya sudah bersih.
    op.drop_table('document_organization')
    op.drop_table('reporting_audit')
    op.drop_table('cash_flow_classification')
    op.drop_table('organization_unit')


def downgrade() -> None:
    # 7) Tabel fondasi (pola q8r9s0t1u2v3) — kosong, tanpa backfill.
    op.create_table('organization_unit', *_base_columns(),
        sa.Column('kind', sa.String(16), nullable=False), sa.Column('code', sa.String(30), nullable=False),
        sa.Column('name', sa.String(150), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('parent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organization_unit.id')),
        sa.UniqueConstraint('kind', 'code', name='uq_organization_code'))
    op.create_table('document_organization', *_base_columns(),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column('document_type', sa.String(40), nullable=False),
        *[sa.Column(field, postgresql.UUID(as_uuid=True), sa.ForeignKey('organization_unit.id')) for field in FIELDS])
    op.create_table('reporting_audit', *_base_columns(),
        sa.Column('entity_type', sa.String(40), nullable=False),
        sa.Column('entity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action', sa.String(30), nullable=False), sa.Column('before', sa.JSON), sa.Column('after', sa.JSON),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=False))
    op.create_table('cash_flow_classification', *_base_columns(),
        sa.Column('target_type', sa.String(16), nullable=False),
        sa.Column('target_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category', sa.String(24), nullable=False),
        sa.UniqueConstraint('target_type', 'target_id', name='uq_cashflow_target'))

    # 6) penutupan_tahun & penutupan_periode — kolom nullable, tanpa backfill.
    op.execute("ALTER TABLE penutupan_tahun DROP CONSTRAINT IF EXISTS uq_penutupan_tahun_tahun")
    op.add_column('penutupan_tahun', sa.Column('company_id', postgresql.UUID(as_uuid=True),
        sa.ForeignKey('organization_unit.id', name='fk_penutupan_tahun_company'), nullable=True))
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_penutupan_tahun_tahun_company') THEN
                ALTER TABLE penutupan_tahun ADD CONSTRAINT uq_penutupan_tahun_tahun_company
                    UNIQUE NULLS NOT DISTINCT (tahun, company_id);
            END IF;
        END $$;
    """)
    op.execute("ALTER TABLE penutupan_periode DROP CONSTRAINT IF EXISTS uq_penutupan_periode_tahun_bulan")
    op.add_column('penutupan_periode', sa.Column('company_id', postgresql.UUID(as_uuid=True),
        sa.ForeignKey('organization_unit.id', name='fk_penutupan_periode_company'), nullable=True))
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_penutupan_periode_tahun_bulan_company') THEN
                ALTER TABLE penutupan_periode ADD CONSTRAINT uq_penutupan_periode_tahun_bulan_company
                    UNIQUE NULLS NOT DISTINCT (tahun, bulan, company_id);
            END IF;
        END $$;
    """)

    # 5) setting_akun — override per perusahaan kembali (semua baris global).
    op.execute("DROP INDEX IF EXISTS ix_setting_akun_key")
    op.add_column('setting_akun', sa.Column('company_id', postgresql.UUID(as_uuid=True),
        sa.ForeignKey('organization_unit.id', name='fk_setting_akun_company'), nullable=True))
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_setting_akun_key_company') THEN
                CREATE UNIQUE INDEX uq_setting_akun_key_company
                    ON setting_akun (key, company_id) NULLS NOT DISTINCT;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_setting_akun_company') THEN
                CREATE INDEX ix_setting_akun_company ON setting_akun (company_id);
            END IF;
        END $$;
    """)

    # 4) kas_bank_akun.
    op.add_column('kas_bank_akun', sa.Column('company_id', postgresql.UUID(as_uuid=True),
        sa.ForeignKey('organization_unit.id', name='fk_kas_bank_akun_company'), nullable=True))
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_kas_bank_akun_company') THEN
                CREATE INDEX ix_kas_bank_akun_company ON kas_bank_akun (company_id);
            END IF;
        END $$;
    """)

    # 3) gudang.
    op.add_column('gudang', sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('gudang', sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_gudang_company', 'gudang', 'organization_unit',
                          ['company_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_gudang_branch', 'gudang', 'organization_unit',
                          ['branch_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_gudang_company_id', 'gudang', ['company_id'])
    op.create_index('ix_gudang_branch_id', 'gudang', ['branch_id'])

    # 2) 10 tabel dokumen — nullable, tanpa server_default/backfill.
    for table in _DOC_TABLES:
        op.add_column(table, sa.Column('company_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('organization_unit.id'), nullable=True))
        op.create_index(f'ix_{table}_company_id', table, ['company_id'])

    # 1) jurnal_umum — 5 dimensi nullable (pola q8r9s0t1u2v3).
    for field in FIELDS:
        op.add_column('jurnal_umum', sa.Column(field, postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f'fk_jurnal_{field}', 'jurnal_umum', 'organization_unit', [field], ['id'])
        op.create_index(f'ix_jurnal_umum_{field}', 'jurnal_umum', [field])
