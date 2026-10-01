"""Tabel app_setting untuk pengaturan aplikasi global (non-COA).

Menampung METODE_VALUASI — metode valuasi persediaan global yang dipindahkan
dari form per-barang ke halaman Setting Akun. Seed default AVERAGE.
"""
import sqlalchemy as sa
from alembic import op

revision = 'v4w5x6y7z8a9'
down_revision = 'u3v4w5x6y7z8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'app_setting',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('key', sa.String(100), nullable=False),
        sa.Column('value', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('key', name='uq_app_setting_key'),
    )
    op.create_index('ix_app_setting_key', 'app_setting', ['key'])
    # Seed default metode valuasi global (idempotent)
    op.execute(
        "INSERT INTO app_setting (key, value) VALUES ('METODE_VALUASI', 'AVERAGE') "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade():
    op.drop_index('ix_app_setting_key', table_name='app_setting')
    op.drop_table('app_setting')
