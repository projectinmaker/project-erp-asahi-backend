"""Tabel company_profile — identitas perusahaan untuk header cetak/PDF.

Update ASAHI (cetak & penomoran):
- Logo + nama perusahaan yang tampil di header semua dokumen cetak/PDF
  tidak lagi hardcoded "ASAHI Books" di frontend, melainkan dari tabel ini
  (menu Pengaturan → Profil Perusahaan).
- Seed default = nilai lama yang hardcoded, supaya tampilan cetak tidak
  berubah sampai user mengeditnya sendiri.
"""
import sqlalchemy as sa
from alembic import op

revision = 'cp0000000001'
down_revision = 'd7e8f9g0h1i2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'company_profile',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('nama_perusahaan', sa.String(200), nullable=False),
        sa.Column('alamat', sa.Text(), nullable=False),
        sa.Column('telepon', sa.String(50), nullable=True),
        sa.Column('email', sa.String(100), nullable=True),
        # Logo sebagai data URL (data:image/...;base64,...) — dipakai langsung
        # oleh <img> di template cetak frontend.
        sa.Column('logo', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    # Seed default (nilai identik dengan COMPANY_INFO lama di frontend).
    op.execute(
        "INSERT INTO company_profile (nama_perusahaan, alamat) VALUES ("
        "'ASAHI Books', "
        "'Jalan Simpangan No.18, RT.03/RW.06, Jatireja, Kec. Cikarang Tim., "
        "Kabupaten Bekasi, Jawa Barat 17530'"
        ")"
    )


def downgrade():
    op.drop_table('company_profile')
