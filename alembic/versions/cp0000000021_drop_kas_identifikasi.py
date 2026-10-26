"""drop kas_identifikasi + konversi BELUM_TERIDENTIFIKASI (Update #14)

Update #14 — fitur Belum Teridentifikasi (Identifikasi & Alokasi) dihapus
dari modul Kas & Bank:

- Dokumen penerimaan_kas/pembayaran_kas dengan jenis_transaksi
  'BELUM_TERIDENTIFIKASI' dikonversi menjadi 'TRANSAKSI_LAINNYA'.
- Kolom payment_allocation.kas_identifikasi_id dihapus.
- Tabel kas_identifikasi + kas_identifikasi_rincian dihapus (jurnal
  identifikasi lama TIDAK dihapus — tetap utuh di jurnal_umum sebagai
  audit trail; ref_id-nya kini hanya jejak historis).
- Baris setting_akun untuk key KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI /
  KAS_PENAMPUNG_PENERIMAAN / KAS_PENAMPUNG_PEMBAYARAN dihapus.
- Akun COA penampung (216000/216001/117004) TIDAK dihapus — masih bisa
  dipakai manual (mis. jurnal reklasifikasi untuk dokumen lama yang
  tadinya tercatat di akun penampung).

CATATAN DATA: dokumen BELUM_TERIDENTIFIKASI lama yang sudah posted
memiliki jurnal bertumpu di akun penampung. Setelah konversi jenis,
rincian dokumen tetap menunjuk akun penampung — bila saldo akun tersebut
perlu dinolkan, reklasifikasi manual lewat Jurnal Umum.
"""
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as postgresql
from alembic import op

# revision identifiers, used by Alembic.
revision = 'cp0000000021'
down_revision = 'cp0000000020'
branch_labels = None
depends_on = None

_STATUS_ENUM = postgresql.ENUM(
    'DRAFT', 'SELESAI', 'BATAL', name='statustransaksi', create_type=False,
)


def upgrade() -> None:
    # 1. Konversi jenis transaksi dokumen kas lama.
    op.execute(
        "UPDATE penerimaan_kas SET jenis_transaksi = 'TRANSAKSI_LAINNYA' "
        "WHERE jenis_transaksi = 'BELUM_TERIDENTIFIKASI'"
    )
    op.execute(
        "UPDATE pembayaran_kas SET jenis_transaksi = 'TRANSAKSI_LAINNYA' "
        "WHERE jenis_transaksi = 'BELUM_TERIDENTIFIKASI'"
    )

    # 2. Hapus tautan identifikasi pada alokasi pelunasan.
    with op.batch_alter_table('payment_allocation') as batch:
        batch.drop_column('kas_identifikasi_id')

    # 3. Hapus baris setting akun penampung.
    op.execute(
        "DELETE FROM setting_akun WHERE key IN ("
        "'KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI', "
        "'KAS_PENAMPUNG_PENERIMAAN', "
        "'KAS_PENAMPUNG_PEMBAYARAN')"
    )

    # 4. Drop tabel identifikasi (rincian dulu — FK ke header).
    op.drop_table('kas_identifikasi_rincian')
    op.drop_table('kas_identifikasi')


def downgrade() -> None:
    """Restore skema tabel identifikasi (data sesi tidak dapat dikembalikan)."""
    op.create_table(
        'kas_identifikasi',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('no_bukti', sa.String(30), nullable=False),
        sa.Column('tanggal', sa.DateTime(timezone=True), nullable=False),
        sa.Column('penerimaan_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('penerimaan_kas.id'), nullable=True),
        sa.Column('pembayaran_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('pembayaran_kas.id'), nullable=True),
        sa.Column('jenis', sa.String(20), nullable=False, server_default='LAINNYA'),
        sa.Column('pelanggan_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('supplier_id', postgresql.UUID(as_uuid=True), nullable=True),
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
    op.create_index(op.f('ix_kas_identifikasi_no_bukti'), 'kas_identifikasi', ['no_bukti'], unique=True)
    op.create_index(op.f('ix_kas_identifikasi_penerimaan_id'), 'kas_identifikasi', ['penerimaan_id'])
    op.create_index(op.f('ix_kas_identifikasi_pembayaran_id'), 'kas_identifikasi', ['pembayaran_id'])

    op.create_table(
        'kas_identifikasi_rincian',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('identifikasi_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('kas_identifikasi.id'), nullable=False),
        sa.Column('akun_perkiraan_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('akun_perkiraan.id'), nullable=False),
        sa.Column('nilai', sa.Numeric(18, 2), nullable=False),
        sa.Column('keterangan', sa.String(255), nullable=True),
        sa.Column('sales_invoice_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('purchase_invoice_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('diskon', sa.Numeric(18, 2), nullable=False, server_default='0'),
        sa.Column('pph23', sa.Numeric(18, 2), nullable=False, server_default='0'),
        sa.Column('no_bukti_potong', sa.String(50), nullable=True),
        sa.Column('masa_pajak', sa.String(7), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('nilai > 0', name='ck_identifikasi_rincian_positive'),
    )
    op.create_index(op.f('ix_kas_identifikasi_rincian_identifikasi_id'),
                    'kas_identifikasi_rincian', ['identifikasi_id'])

    with op.batch_alter_table('payment_allocation') as batch:
        batch.add_column(sa.Column('kas_identifikasi_id', postgresql.UUID(as_uuid=True),
                                   nullable=True))
