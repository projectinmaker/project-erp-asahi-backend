"""Slogan perusahaan & rekening bank (cetak Invoice Penjualan).

Update ASAHI #6:
1. Kolom company_profile.slogan — tagline yang tampil KHUSUS di header
   cetak Invoice Penjualan (di bawah nama perusahaan). Seed teks slogan
   Asahi untuk baris yang sudah ada.
2. Tabel rekening_bank — daftar rekening bank perusahaan untuk dicetak di
   bawah Keterangan pada Invoice Penjualan (nama bank + "Acc Nbr <nomor>
   (<mata uang>)"). Seed contoh sesuai permintaan user:
   Bank BNI KCP Jababeka — 12345678910 — IDR.
"""
import sqlalchemy as sa
from alembic import op

revision = 'cp0000000003'
down_revision = 'cp0000000002'
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. Kolom slogan di company_profile + seed ──────────────────────────
    op.add_column('company_profile', sa.Column('slogan', sa.Text(), nullable=True))
    # Seed slogan untuk baris yang sudah ada (literal aman — teks slogan
    # tidak memuat tanda kutip).
    op.execute(
        "UPDATE company_profile SET slogan = "
        "'Machining, precision, part Jig & fixture Fabrication "
        "Mechanical & electrical Industrial supplies' "
        "WHERE slogan IS NULL"
    )

    # ── 2. Tabel rekening_bank + seed contoh ───────────────────────────────
    op.create_table(
        'rekening_bank',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('nama_bank', sa.String(200), nullable=False),
        sa.Column('no_rekening', sa.String(100), nullable=False),
        sa.Column('mata_uang', sa.String(8), nullable=False, server_default='IDR'),
        sa.Column('is_aktif', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_rekening_bank_nama_bank', 'rekening_bank', ['nama_bank'])
    # Seed contoh (bisa diedit/dihapus di Pengaturan → Profil Perusahaan).
    op.execute(
        "INSERT INTO rekening_bank (nama_bank, no_rekening, mata_uang, is_aktif) VALUES "
        "('Bank BNI KCP Jababeka', '12345678910', 'IDR', true)"
    )


def downgrade():
    op.drop_index('ix_rekening_bank_nama_bank', table_name='rekening_bank')
    op.drop_table('rekening_bank')
    op.drop_column('company_profile', 'slogan')
