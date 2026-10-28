"""preferensi & pengaturan: kolom baru company_profile (Update ASAHI #019)

Update ASAHI #019 — halaman Pengaturan (Umum/Periode Akuntansi/Pajak) &
menu baru Preferensi. Profil perusahaan diperluas dengan kolom identitas,
periode akuntansi, dan data pajak (semua NULLABLE, tanpa default — baris
existing tidak berubah):

1. Identitas umum (Pengaturan → Umum):
   - fax (String 50), negara (String 100), mata_uang (String 8)
2. Periode akuntansi (Pengaturan → Periode Akuntansi):
   - tanggal_mulai (Date), tahun_fiskal (String 20),
     periode_default (String 30 — BULAN_INI/BULAN_LALU/dll.)
3. Data pajak (Pengaturan → Pajak):
   - no_seri_faktur_pajak (String 50), npwp (String 50),
     no_pengukuhan_pkp (String 50), branch_code (String 10),
     jenis_usaha (String 200), klu_spt (String 200).

Preferensi non-akun (font, format tanggal, prefix penomoran, dll.) TIDAK
di-seed ke app_setting di sini — memakai lazy default yang didefinisikan
registry app/services/preferensi_service.py (GET selalu punya jawaban).
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'cp0000000023'
down_revision = 'rbac00000002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Identitas umum (Pengaturan → Umum — diperluas dari Profil Perusahaan).
    op.add_column('company_profile', sa.Column('fax', sa.String(length=50), nullable=True))
    op.add_column('company_profile', sa.Column('negara', sa.String(length=100), nullable=True))
    op.add_column('company_profile', sa.Column('mata_uang', sa.String(length=8), nullable=True))
    # 2. Periode akuntansi (Pengaturan → Periode Akuntansi).
    op.add_column('company_profile', sa.Column('tanggal_mulai', sa.Date(), nullable=True))
    op.add_column('company_profile', sa.Column('tahun_fiskal', sa.String(length=20), nullable=True))
    op.add_column('company_profile', sa.Column('periode_default', sa.String(length=30), nullable=True))
    # 3. Data pajak (Pengaturan → Pajak).
    op.add_column('company_profile', sa.Column('no_seri_faktur_pajak', sa.String(length=50), nullable=True))
    op.add_column('company_profile', sa.Column('npwp', sa.String(length=50), nullable=True))
    op.add_column('company_profile', sa.Column('no_pengukuhan_pkp', sa.String(length=50), nullable=True))
    op.add_column('company_profile', sa.Column('branch_code', sa.String(length=10), nullable=True))
    op.add_column('company_profile', sa.Column('jenis_usaha', sa.String(length=200), nullable=True))
    op.add_column('company_profile', sa.Column('klu_spt', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('company_profile', 'klu_spt')
    op.drop_column('company_profile', 'jenis_usaha')
    op.drop_column('company_profile', 'branch_code')
    op.drop_column('company_profile', 'no_pengukuhan_pkp')
    op.drop_column('company_profile', 'npwp')
    op.drop_column('company_profile', 'no_seri_faktur_pajak')
    op.drop_column('company_profile', 'periode_default')
    op.drop_column('company_profile', 'tahun_fiskal')
    op.drop_column('company_profile', 'tanggal_mulai')
    op.drop_column('company_profile', 'mata_uang')
    op.drop_column('company_profile', 'negara')
    op.drop_column('company_profile', 'fax')
