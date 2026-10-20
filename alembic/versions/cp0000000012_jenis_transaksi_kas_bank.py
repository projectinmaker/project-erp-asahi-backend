"""jenis_transaksi_kas_bank + identifikasi_alokasi

Update #02 (koreksi miskom catatan-update-01 + tambahan) — Pelunasan
Piutang/Pelunasan Hutang bukan menu terpisah, melainkan JENIS TRANSAKSI
di dalam form Penerimaan/Pembayaran modul Kas & Bank. Tiga jenis:

1. PELUNASAN_INVOICE      — pilih customer/supplier → invoice outstanding
                            → pilih invoice → nominal → posting otomatis ke
                            akun piutang/hutang subledger pihak + Kas/Bank.
2. TRANSAKSI_LAINNYA      — penerimaan modal, pendapatan lain, biaya
                            operasional, transaksi non-invoice; akun lawan
                            dipilih user dari COA (rincian). Guard service:
                            akun piutang/hutang DILARANG tanpa alokasi invoice.
3. BELUM_TERIDENTIFIKASI  — mutasi bank yang asalnya belum diketahui;
                            posting ke akun penampung sementara, lalu
                            dipindahkan ke akun benar via Identifikasi &
                            Alokasi (tanpa jurnal bank ganda).

Perubahan skema:
- penerimaan_kas.jenis_transaksi VARCHAR(30) NOT NULL DEFAULT 'TRANSAKSI_LAINNYA'
- pembayaran_kas.jenis_transaksi VARCHAR(30) NOT NULL DEFAULT 'TRANSAKSI_LAINNYA'
- Backfill: dokumen lama yang punya payment_allocation (settlement Update #01
  dan lebih lama) ditandai PELUNASAN_INVOICE; sisanya TRANSAKSI_LAINNYA.
- Tabel baru kas_identifikasi + kas_identifikasi_rincian (Identifikasi &
  Alokasi dokumen kas Belum Teridentifikasi; jurnal reklasifikasi lewat
  posting engine yang sama, reversal via reverse_journal).
- Akun penampung baru 216000 "Hutang Belum Teridentifikasi (Penampung)"
  di bawah 210000 (KEWAJIBAN JANGKA PENDEK) + baris setting akun
  KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI. Keduanya idempotent (ON CONFLICT DO
  NOTHING) dan bisa diganti per perusahaan lewat Pengaturan → Setting Akun.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'cp0000000012'
down_revision = 'cp0000000011'
branch_labels = None
depends_on = None

_PENERIMAAN = 'penerimaan_kas'
_PEMBAYARAN = 'pembayaran_kas'
_IDENTIFIKASI = 'kas_identifikasi'
_IDENTIFIKASI_RINCIAN = 'kas_identifikasi_rincian'

_STATUS_ENUM = postgresql.ENUM(
    'DRAFT', 'SELESAI', 'BATAL', name='statustransaksi', create_type=False,
)

# Akun penampung default (lihat docstring). Kode 216000 dipilih karena
# 211000-215000 di bawah 210000 sudah terpakai; 216000 kosong di COA v2.
_AKUN_PENAMPUNG_KODE = '216000'
_AKUN_PENAMPUNG_NAMA = 'Hutang Belum Teridentifikasi (Penampung)'
_SETTING_KEY = 'KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI'
_SETTING_LABEL = 'Kas Belum Teridentifikasi (Penampung)'


def upgrade() -> None:
    # 1) Kolom jenis_transaksi pada kedua tabel kas.
    op.add_column(
        _PENERIMAAN,
        sa.Column('jenis_transaksi', sa.String(30), nullable=False,
                  server_default='TRANSAKSI_LAINNYA'),
    )
    op.add_column(
        _PEMBAYARAN,
        sa.Column('jenis_transaksi', sa.String(30), nullable=False,
                  server_default='TRANSAKSI_LAINNYA'),
    )

    # 2) Backfill: dokumen settlement lama (punya alokasi invoice) →
    #    PELUNASAN_INVOICE. Dokumen umum tetap TRANSAKSI_LAINNYA (default).
    op.execute(
        "UPDATE penerimaan_kas SET jenis_transaksi = 'PELUNASAN_INVOICE' "
        "WHERE id IN (SELECT penerimaan_id FROM payment_allocation "
        "             WHERE penerimaan_id IS NOT NULL)"
    )
    op.execute(
        "UPDATE pembayaran_kas SET jenis_transaksi = 'PELUNASAN_INVOICE' "
        "WHERE id IN (SELECT pembayaran_id FROM payment_allocation "
        "             WHERE pembayaran_id IS NOT NULL)"
    )

    # 3) Tabel Identifikasi & Alokasi.
    op.create_table(
        _IDENTIFIKASI,
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('no_bukti', sa.String(30), nullable=False),
        sa.Column('tanggal', sa.DateTime(timezone=True), nullable=False),
        sa.Column('penerimaan_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('penerimaan_kas.id'), nullable=True),
        sa.Column('pembayaran_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('pembayaran_kas.id'), nullable=True),
        sa.Column('catatan', sa.Text(), nullable=True),
        sa.Column('total_nilai', sa.Numeric(18, 2), nullable=False),
        sa.Column('jurnal_umum_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('jurnal_umum.id'), nullable=True),
        sa.Column('status', _STATUS_ENUM, nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('pengguna.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(penerimaan_id IS NOT NULL AND pembayaran_id IS NULL) "
            "OR (penerimaan_id IS NULL AND pembayaran_id IS NOT NULL)",
            name='ck_identifikasi_doc_pair',
        ),
        sa.CheckConstraint('total_nilai > 0', name='ck_identifikasi_positive'),
    )
    op.create_index(op.f('ix_kas_identifikasi_no_bukti'), _IDENTIFIKASI, ['no_bukti'], unique=True)
    op.create_index(op.f('ix_kas_identifikasi_penerimaan_id'), _IDENTIFIKASI, ['penerimaan_id'])
    op.create_index(op.f('ix_kas_identifikasi_pembayaran_id'), _IDENTIFIKASI, ['pembayaran_id'])

    op.create_table(
        _IDENTIFIKASI_RINCIAN,
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('identifikasi_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('kas_identifikasi.id'), nullable=False),
        sa.Column('akun_perkiraan_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('akun_perkiraan.id'), nullable=False),
        sa.Column('nilai', sa.Numeric(18, 2), nullable=False),
        sa.Column('keterangan', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('nilai > 0', name='ck_identifikasi_rincian_positive'),
    )
    op.create_index(op.f('ix_kas_identifikasi_rincian_identifikasi_id'),
                    _IDENTIFIKASI_RINCIAN, ['identifikasi_id'])

    # 4) Akun penampung default + setting (idempotent; induk 210000 harus ada).
    op.execute(
        f"""
        INSERT INTO akun_perkiraan
            (id, kode, nama, header, tingkat, induk_id, induk_kode, saldo_normal,
             saldo, status, is_subledger, account_class, account_subclass,
             financial_statement, report_group, system_account_type,
             allow_system_posting, allow_manual_posting, is_control_account,
             reconciliation_required, active, created_at, updated_at)
        SELECT gen_random_uuid(), '{_AKUN_PENAMPUNG_KODE}',
               '{_AKUN_PENAMPUNG_NAMA}',
               'KEWAJIBAN'::headercoa, 'DETAIL'::tingkatakun, a.id, '210000',
               'KREDIT'::saldonormal, 0, 'AKTIF', false, 'LIABILITY', NULL,
               'NERACA', 'CURRENT_LIABILITY', NULL, true, true, false, false,
               true, now(), now()
        FROM akun_perkiraan a
        WHERE a.kode = '210000'
        ON CONFLICT (kode) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO setting_akun (id, "key", label, akun_perkiraan_id)
        SELECT gen_random_uuid(), '{_SETTING_KEY}', '{_SETTING_LABEL}', a.id
        FROM akun_perkiraan a
        WHERE a.kode = '{_AKUN_PENAMPUNG_KODE}'
        ON CONFLICT ("key") DO NOTHING
        """
    )


def downgrade() -> None:
    # Hapus setting + akun penampung yang dibuat migration ini (hanya kalau
    # masih memakai kode/nama default — akun yang di-rename/dipakai transaksi
    # dibiarkan agar jurnal histori tetap valid).
    op.execute(
        f"DELETE FROM setting_akun WHERE \"key\" = '{_SETTING_KEY}' "
        f"AND akun_perkiraan_id IN (SELECT id FROM akun_perkiraan WHERE kode = '{_AKUN_PENAMPUNG_KODE}')"
    )
    op.execute(
        f"DELETE FROM akun_perkiraan WHERE kode = '{_AKUN_PENAMPUNG_KODE}' "
        f"AND nama = '{_AKUN_PENAMPUNG_NAMA}' "
        f"AND NOT EXISTS (SELECT 1 FROM jurnal_detail jd WHERE jd.akun_perkiraan_id = akun_perkiraan.id)"
    )

    op.drop_index(op.f('ix_kas_identifikasi_rincian_identifikasi_id'), table_name=_IDENTIFIKASI_RINCIAN)
    op.drop_table(_IDENTIFIKASI_RINCIAN)
    op.drop_index(op.f('ix_kas_identifikasi_pembayaran_id'), table_name=_IDENTIFIKASI)
    op.drop_index(op.f('ix_kas_identifikasi_penerimaan_id'), table_name=_IDENTIFIKASI)
    op.drop_index(op.f('ix_kas_identifikasi_no_bukti'), table_name=_IDENTIFIKASI)
    op.drop_table(_IDENTIFIKASI)
    op.drop_column(_PEMBAYARAN, 'jenis_transaksi')
    op.drop_column(_PENERIMAAN, 'jenis_transaksi')
