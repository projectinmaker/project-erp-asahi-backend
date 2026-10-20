"""AUD-17 (update #05) — company-scoped setting akun, kas/bank & penutupan periode.

Temuan audit 7-c: multi-company hanya DIMENSI REPORTING (company/branch/dst di
jurnal_umum + document_organization) sedangkan setting_akun (unique key GLOBAL),
kas_bank_akun, penutupan_periode (unique tahun+bulan, kuncinya global semua
dimensi) SEMUA single-book. Migrasi ini menambah dimensi perusahaan pada master
dimaksud dengan FALLBACK GLOBAL (company_id NULL):

- ``setting_akun``   : kolom ``company_id`` + UNIQUE (key, company_id)
  NULLS NOT DISTINCT → override per perusahaan; lookup service: (key, company)
  → fallback (key, NULL). Baris existing otomatis menjadi baris GLOBAL.
- ``kas_bank_akun``  : kolom ``company_id`` (kode tetap global-unique) → akun
  kas/bank milik satu perusahaan; list/dropdown bisa difilter per company
  (akun GLOBAL tetap terlihat oleh semua perusahaan).
- ``penutupan_periode`` + ``penutupan_tahun``: kolom ``company_id`` + UNIQUE
  NULLS NOT DISTINCT (tahun, bulan, company_id) / (tahun, company_id) →
  periode bisa ditutup global (company NULL = mengunci semua) atau khusus
  satu perusahaan. Guard periode menjadi company-aware: jurnal perusahaan C
  terkunci bila ada baris DITUTUP utk (C) ATAU (NULL); jurnal tanpa perusahaan
  hanya terkunci oleh baris global.

COA (akun_perkiraan) tetap SHARED/GLOBAL — keputusan desain terdokumentasi
(satu bagan akun, dimensi perusahaan di jurnal; scoping COA per entitas
adalah perubahan arsitektur yang lebih besar dan tidak diminta audit).

Idempotent & reversible. PG >= 15 (NULLS NOT DISTINCT) — terverifikasi 16.2.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'cp0000000018'
down_revision = 'cp0000000017'
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set:
    return {c['name'] for c in sa.inspect(bind).get_columns(table)}


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # === setting_akun: company override dengan fallback global ===
    cols = _columns(bind, 'setting_akun')
    if 'company_id' not in cols:
        op.add_column('setting_akun', sa.Column(
            'company_id', UUID(as_uuid=True),
            sa.ForeignKey('organization_unit.id', name='fk_setting_akun_company'),
            nullable=True))
    # Unik lama: ix_setting_akun_key (unique index di DB live) dan/atau
    # uq_setting_akun_key (constraint model lama) — drop keduanya bila ada.
    op.execute("DROP INDEX IF EXISTS ix_setting_akun_key")
    op.execute("ALTER TABLE setting_akun DROP CONSTRAINT IF EXISTS uq_setting_akun_key")
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

    # === kas_bank_akun: akun milik satu perusahaan (kode tetap global-unique) ===
    cols = _columns(bind, 'kas_bank_akun')
    if 'company_id' not in cols:
        op.add_column('kas_bank_akun', sa.Column(
            'company_id', UUID(as_uuid=True),
            sa.ForeignKey('organization_unit.id', name='fk_kas_bank_akun_company'),
            nullable=True))
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_kas_bank_akun_company') THEN
                CREATE INDEX ix_kas_bank_akun_company ON kas_bank_akun (company_id);
            END IF;
        END $$;
    """)

    # === penutupan_periode: lock global (NULL) atau per perusahaan ===
    cols = _columns(bind, 'penutupan_periode')
    if 'company_id' not in cols:
        op.add_column('penutupan_periode', sa.Column(
            'company_id', UUID(as_uuid=True),
            sa.ForeignKey('organization_unit.id', name='fk_penutupan_periode_company'),
            nullable=True))
    op.execute("ALTER TABLE penutupan_periode DROP CONSTRAINT IF EXISTS uq_penutupan_periode_tahun_bulan")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_penutupan_periode_tahun_bulan_company') THEN
                ALTER TABLE penutupan_periode ADD CONSTRAINT uq_penutupan_periode_tahun_bulan_company
                    UNIQUE NULLS NOT DISTINCT (tahun, bulan, company_id);
            END IF;
        END $$;
    """)

    # === penutupan_tahun: tutup tahun buku per perusahaan ===
    if insp.has_table('penutupan_tahun'):
        cols = _columns(bind, 'penutupan_tahun')
        if 'company_id' not in cols:
            op.add_column('penutupan_tahun', sa.Column(
                'company_id', UUID(as_uuid=True),
                sa.ForeignKey('organization_unit.id', name='fk_penutupan_tahun_company'),
                nullable=True))
        op.execute("ALTER TABLE penutupan_tahun DROP CONSTRAINT IF EXISTS uq_penutupan_tahun_tahun")
        op.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_penutupan_tahun_tahun_company') THEN
                    ALTER TABLE penutupan_tahun ADD CONSTRAINT uq_penutupan_tahun_tahun_company
                        UNIQUE NULLS NOT DISTINCT (tahun, company_id);
                END IF;
            END $$;
        """)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Guard keamanan: baris ber-perusahaan di master operasional tidak boleh
    # hilang diam-diam — downgrade harus dijelaskan manual bila ada datanya.
    for table in ('kas_bank_akun', 'penutupan_periode', 'penutupan_tahun'):
        if insp.has_table(table) and 'company_id' in _columns(bind, table):
            n = bind.execute(sa.text(
                f"SELECT count(*) FROM {table} WHERE company_id IS NOT NULL")).scalar()
            if n:
                raise RuntimeError(
                    f"Tabel {table} masih punya {n} baris ber-perusahaan — "
                    f"bersihkan/pindahkan dulu sebelum downgrade cp18."
                )

    if insp.has_table('penutupan_tahun'):
        op.execute("ALTER TABLE penutupan_tahun DROP CONSTRAINT IF EXISTS uq_penutupan_tahun_tahun_company")
        op.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_penutupan_tahun_tahun') THEN
                    ALTER TABLE penutupan_tahun ADD CONSTRAINT uq_penutupan_tahun_tahun UNIQUE (tahun);
                END IF;
            END $$;
        """)
        if 'company_id' in _columns(bind, 'penutupan_tahun'):
            op.drop_column('penutupan_tahun', 'company_id')

    op.execute("ALTER TABLE penutupan_periode DROP CONSTRAINT IF EXISTS uq_penutupan_periode_tahun_bulan_company")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_penutupan_periode_tahun_bulan') THEN
                ALTER TABLE penutupan_periode ADD CONSTRAINT uq_penutupan_periode_tahun_bulan UNIQUE (tahun, bulan);
            END IF;
        END $$;
    """)
    if 'company_id' in _columns(bind, 'penutupan_periode'):
        op.drop_column('penutupan_periode', 'company_id')

    op.execute("DROP INDEX IF EXISTS ix_kas_bank_akun_company")
    if 'company_id' in _columns(bind, 'kas_bank_akun'):
        op.drop_column('kas_bank_akun', 'company_id')

    op.execute("DROP INDEX IF EXISTS uq_setting_akun_key_company")
    op.execute("DROP INDEX IF EXISTS ix_setting_akun_company")
    # Override per-perusahaan setting akun aman dihapus (fallback global tetap).
    op.execute("DELETE FROM setting_akun WHERE company_id IS NOT NULL")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_setting_akun_key') THEN
                CREATE UNIQUE INDEX ix_setting_akun_key ON setting_akun (key);
            END IF;
        END $$;
    """)
    if 'company_id' in _columns(bind, 'setting_akun'):
        op.drop_column('setting_akun', 'company_id')
