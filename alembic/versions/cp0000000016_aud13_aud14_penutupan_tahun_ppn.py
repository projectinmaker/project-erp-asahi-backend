"""AUD-13 + AUD-14 — closing tahunan & perbaikan data PPN Keluaran legacy.

AUD-14 (temuan audit 7-c): hanya closing BULANAN yang didukung — transfer
saldo LABA_RUGI_BERJALAN (322000) → LABA_DITAHAN (321000) di akhir tahun
tidak ada. Migrasi ini membuat tabel ``penutupan_tahun`` (status tahun buku,
jurnal transfer, laba/rugi tahun, jejak tutup/buka) — service menambahkan
tutup_tahun/buka_tahun/get_tahun_list.

AUD-13 (temuan audit 7-b #7): setting PPN_KELUARAN live menunjuk akun legacy
230100001 (seeder phase3, duplikat nama dengan 213005 COA v2). Migrasi data
idempotent (hanya bila akun 213005 ada):
1. Re-point setting ``PPN_KELUARAN`` ke 213005.
2. Pindahkan baris jurnal_detail dari 230100001 → 213005 (histori PPN tetap
   terbaca di rekap pajak & neraca; total balance tidak berubah).
3. Nonaktifkan + rename akun 230100001 → "PPN Keluaran (Legacy — AUD-13)"
   supaya tidak terpilih lagi di UI.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'cp0000000016'
down_revision = 'cp0000000015'
branch_labels = None
depends_on = None


def _uuid_col():
    return UUID(as_uuid=True)


def upgrade():
    bind = op.get_bind()

    # === AUD-14: tabel penutupan_tahun ===
    exists = sa.inspect(bind).has_table('penutupan_tahun')
    if not exists:
        op.create_table(
            'penutupan_tahun',
            sa.Column('id', _uuid_col(), primary_key=True),
            sa.Column('tahun', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(20), nullable=False, server_default='DITUTUP'),
            sa.Column('keterangan', sa.Text(), nullable=True),
            sa.Column('jurnal_penutupan_id', _uuid_col(),
                      sa.ForeignKey('jurnal_umum.id', name='fk_penutupan_tahun_jurnal'), nullable=True),
            sa.Column('laba_rugi', sa.Numeric(18, 2), nullable=True),
            sa.Column('closed_by', _uuid_col(), sa.ForeignKey('pengguna.id'), nullable=True),
            sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('reopened_by', _uuid_col(), sa.ForeignKey('pengguna.id'), nullable=True),
            sa.Column('reopened_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('reopen_alasan', sa.Text(), nullable=True),
            sa.Column('created_by', _uuid_col(), sa.ForeignKey('pengguna.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.UniqueConstraint('tahun', name='uq_penutupan_tahun_tahun'),
        )

    # === AUD-13: data fix PPN_KELUARAN legacy 230100001 → 213005 ===
    op.execute(sa.text("""
    DO $$
    DECLARE
        v_legacy uuid;
        v_v2 uuid;
        v_setting uuid;
    BEGIN
        SELECT id INTO v_v2 FROM akun_perkiraan WHERE kode = '213005' LIMIT 1;
        IF v_v2 IS NULL THEN
            RETURN;  -- COA v2 tidak ada (instalasi legacy) — tidak ada yang boleh diubah
        END IF;
        SELECT id INTO v_legacy FROM akun_perkiraan WHERE kode = '230100001' LIMIT 1;
        SELECT akun_perkiraan_id INTO v_setting FROM setting_akun WHERE key = 'PPN_KELUARAN';

        -- 1) Setting menunjuk legacy (atau kosong) → re-point ke 213005
        IF v_setting IS NULL OR v_setting = v_legacy THEN
            UPDATE setting_akun SET akun_perkiraan_id = v_v2 WHERE key = 'PPN_KELUARAN';
        END IF;

        -- 2) Histori jurnal PPN keluaran dipindah ke akun kanonik
        IF v_legacy IS NOT NULL THEN
            UPDATE jurnal_detail SET akun_perkiraan_id = v_v2 WHERE akun_perkiraan_id = v_legacy;
            -- 3) Legacy dinonaktifkan + rename (jejak sebab migrasi)
            UPDATE akun_perkiraan
               SET active = false,
                   nama = 'PPN Keluaran (Legacy — AUD-13)'
             WHERE id = v_legacy AND active = true;
        END IF;
    END $$;
    """))


def downgrade():
    op.execute(sa.text("""
    DO $$
    DECLARE
        v_legacy uuid;
        v_v2 uuid;
    BEGIN
        SELECT id INTO v_legacy FROM akun_perkiraan WHERE kode = '230100001' LIMIT 1;
        SELECT id INTO v_v2 FROM akun_perkiraan WHERE kode = '213005' LIMIT 1;
        IF v_legacy IS NOT NULL AND v_v2 IS NOT NULL THEN
            UPDATE jurnal_detail SET akun_perkiraan_id = v_legacy WHERE akun_perkiraan_id = v_v2;
            UPDATE akun_perkiraan SET active = true, nama = 'PPN Keluaran' WHERE id = v_legacy;
            UPDATE setting_akun SET akun_perkiraan_id = v_legacy WHERE key = 'PPN_KELUARAN';
        END IF;
    END $$;
    """))
    if sa.inspect(op.get_bind()).has_table('penutupan_tahun'):
        op.drop_table('penutupan_tahun')


def _uuid_col():
    from sqlalchemy.dialects.postgresql import UUID
    return UUID(as_uuid=True)
