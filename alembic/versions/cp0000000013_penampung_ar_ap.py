"""Update #03 — Piutang/Hutang Kontrol Sementara (akun penampung AR & AP).

catatan-update-03: pisahkan akun penampung suspense dari akun piutang/hutang
operasional supaya penyajian neraca benar, dan dukung identifikasi langsung
dari dokumen penerimaan/pembayaran dengan memilih customer/supplier + invoice
(reklasifikasi jurnal penampung, tanpa jurnal bank ganda).

Perubahan database:

1. **Dua akun penampung terpisah** (sebelumnya satu akun 216000 untuk dua
   arah — penerimaan pakai saldo kredit, pembayaran "kredit negatif"):
   - ``216001`` "Penerimaan Kas Belum Teridentifikasi (Penampung)" —
     liabilitas (kredit) di bawah 210000: kas masuk belum diketahui asalnya.
   - ``117004`` "Pembayaran Kas Belum Teridentifikasi (Penampung)" —
     aset (debit) di bawah 117000: kas keluar belum diketahui tujuannya.
   Keduanya + setting ``KAS_PENAMPUNG_PENERIMAAN`` /
   ``KAS_PENAMPUNG_PEMBAYARAN`` dibuat idempotent (ON CONFLICT DO NOTHING).
   Setting lama ``KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI`` (216000) tetap jalan
   sebagai kompatibilitas — dokumen lama tetap membaca akun penampung dari
   rincian-nya masing-masing (dibekukan saat create).

2. **Kolom identifikasi piutang/hutang** (audit trail + alokasi invoice):
   - ``kas_identifikasi.jenis`` ('LAINNYA' | 'PIUTANG' | 'HUTANG'),
     ``pelanggan_id``, ``supplier_id``.
   - ``kas_identifikasi_rincian.sales_invoice_id`` / ``purchase_invoice_id``
     + ``diskon`` / ``pph23`` per baris (snapshot audit alokasi invoice).
   - ``payment_allocation.kas_identifikasi_id`` — menghubungkan baris alokasi
     invoice hasil identifikasi dengan sesinya (dipakai saat pembatalan untuk
     memulihkan outstanding invoice tanpa menyentuh jurnal bank).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'cp0000000013'
down_revision = 'cp0000000012'
branch_labels = None
depends_on = None

_IDENTIFIKASI = 'kas_identifikasi'
_IDENTIFIKASI_RINCIAN = 'kas_identifikasi_rincian'
_ALLOCATION = 'payment_allocation'

# Akun penampung penerimaan (AR — kas masuk belum diketahui asalnya).
# Liabilitas di bawah 210000 KEWAJIBAN JANGKA PENDEK, kode 216001 kosong.
_AKUN_PENERIMAAN_KODE = '216001'
_AKUN_PENERIMAAN_NAMA = 'Penerimaan Kas Belum Teridentifikasi (Penampung)'
_SETTING_PENERIMAAN_KEY = 'KAS_PENAMPUNG_PENERIMAAN'
_SETTING_PENERIMAAN_LABEL = 'Penerimaan Kas Belum Teridentifikasi (Penampung)'

# Akun penampung pembayaran (AP — kas keluar belum diketahui tujuannya).
# Aset lancar di bawah 117000 (117001-117003 terpakai; 117004 kosong).
_AKUN_PEMBAYARAN_KODE = '117004'
_AKUN_PEMBAYARAN_NAMA = 'Pembayaran Kas Belum Teridentifikasi (Penampung)'
_SETTING_PEMBAYARAN_KEY = 'KAS_PENAMPUNG_PEMBAYARAN'
_SETTING_PEMBAYARAN_LABEL = 'Pembayaran Kas Belum Teridentifikasi (Penampung)'


def _insert_akun(kode: str, nama: str, induk_kode: str, header: str,
                 saldo_normal: str, account_class: str, report_group: str) -> None:
    op.execute(
        f"""
        INSERT INTO akun_perkiraan
            (id, kode, nama, header, tingkat, induk_id, induk_kode, saldo_normal,
             saldo, status, is_subledger, account_class, account_subclass,
             financial_statement, report_group, system_account_type,
             allow_system_posting, allow_manual_posting, is_control_account,
             reconciliation_required, active, created_at, updated_at)
        SELECT gen_random_uuid(), '{kode}', '{nama}',
               '{header}'::headercoa, 'DETAIL'::tingkatakun, a.id, '{induk_kode}',
               '{saldo_normal}'::saldonormal, 0, 'AKTIF', false,
               '{account_class}', NULL, 'NERACA', '{report_group}', NULL,
               true, true, false, false, true, now(), now()
        FROM akun_perkiraan a
        WHERE a.kode = '{induk_kode}'
        ON CONFLICT (kode) DO NOTHING
        """
    )


def _insert_setting(key: str, label: str, kode_akun: str) -> None:
    op.execute(
        f"""
        INSERT INTO setting_akun (id, "key", label, akun_perkiraan_id)
        SELECT gen_random_uuid(), '{key}', '{label}', a.id
        FROM akun_perkiraan a
        WHERE a.kode = '{kode_akun}'
        ON CONFLICT ("key") DO NOTHING
        """
    )


def _drop_setting_dan_akun(key: str, kode: str, nama: str) -> None:
    op.execute(
        f"DELETE FROM setting_akun WHERE \"key\" = '{key}' "
        f"AND akun_perkiraan_id IN (SELECT id FROM akun_perkiraan WHERE kode = '{kode}')"
    )
    op.execute(
        f"DELETE FROM akun_perkiraan WHERE kode = '{kode}' "
        f"AND nama = '{nama}' "
        f"AND NOT EXISTS (SELECT 1 FROM jurnal_detail jd WHERE jd.akun_perkiraan_id = akun_perkiraan.id)"
    )


def upgrade() -> None:
    # 1) Kolom identifikasi piutang/hutang (audit trail + tautan invoice).
    op.add_column(
        _IDENTIFIKASI,
        sa.Column('jenis', sa.String(20), nullable=False, server_default='LAINNYA'),
    )
    op.add_column(
        _IDENTIFIKASI,
        sa.Column('pelanggan_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        _IDENTIFIKASI,
        sa.Column('supplier_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        _IDENTIFIKASI_RINCIAN,
        sa.Column('sales_invoice_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        _IDENTIFIKASI_RINCIAN,
        sa.Column('purchase_invoice_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        _IDENTIFIKASI_RINCIAN,
        sa.Column('diskon', sa.Numeric(18, 2), nullable=False, server_default='0'),
    )
    op.add_column(
        _IDENTIFIKASI_RINCIAN,
        sa.Column('pph23', sa.Numeric(18, 2), nullable=False, server_default='0'),
    )
    # Tautan alokasi invoice hasil identifikasi -> sesi identifikasi-nya.
    op.add_column(
        _ALLOCATION,
        sa.Column('kas_identifikasi_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f('ix_payment_allocation_kas_identifikasi_id'),
        _ALLOCATION, ['kas_identifikasi_id'],
    )

    # 2) Dua akun penampung terpisah + setting (idempotent).
    _insert_akun(
        _AKUN_PENERIMAAN_KODE, _AKUN_PENERIMAAN_NAMA,
        induk_kode='210000', header='KEWAJIBAN', saldo_normal='KREDIT',
        account_class='LIABILITY', report_group='CURRENT_LIABILITY',
    )
    _insert_akun(
        _AKUN_PEMBAYARAN_KODE, _AKUN_PEMBAYARAN_NAMA,
        induk_kode='117000', header='AKTIVA', saldo_normal='DEBIT',
        account_class='ASSET', report_group='CURRENT_ASSET',
    )
    _insert_setting(_SETTING_PENERIMAAN_KEY, _SETTING_PENERIMAAN_LABEL, _AKUN_PENERIMAAN_KODE)
    _insert_setting(_SETTING_PEMBAYARAN_KEY, _SETTING_PEMBAYARAN_LABEL, _AKUN_PEMBAYARAN_KODE)


def downgrade() -> None:
    op.drop_index(
        op.f('ix_payment_allocation_kas_identifikasi_id'), table_name=_ALLOCATION)
    op.drop_column(_ALLOCATION, 'kas_identifikasi_id')

    op.drop_column(_IDENTIFIKASI_RINCIAN, 'pph23')
    op.drop_column(_IDENTIFIKASI_RINCIAN, 'diskon')
    op.drop_column(_IDENTIFIKASI_RINCIAN, 'purchase_invoice_id')
    op.drop_column(_IDENTIFIKASI_RINCIAN, 'sales_invoice_id')

    op.drop_column(_IDENTIFIKASI, 'supplier_id')
    op.drop_column(_IDENTIFIKASI, 'pelanggan_id')
    op.drop_column(_IDENTIFIKASI, 'jenis')

    _drop_setting_dan_akun(
        _SETTING_PEMBAYARAN_KEY, _AKUN_PEMBAYARAN_KODE, _AKUN_PEMBAYARAN_NAMA)
    _drop_setting_dan_akun(
        _SETTING_PENERIMAAN_KEY, _AKUN_PENERIMAAN_KODE, _AKUN_PENERIMAAN_NAMA)
