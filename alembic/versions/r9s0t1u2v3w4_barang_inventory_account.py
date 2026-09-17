"""Optional inventory account mapping on item master; no historical backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'r9s0t1u2v3w4'
down_revision = 'q8r9s0t1u2v3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('barang', sa.Column('akun_persediaan_id', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_barang_akun_persediaan', 'barang', 'akun_perkiraan', ['akun_persediaan_id'], ['id'])
    op.create_index('ix_barang_akun_persediaan_id', 'barang', ['akun_persediaan_id'])


def downgrade():
    op.drop_index('ix_barang_akun_persediaan_id', table_name='barang')
    op.drop_constraint('fk_barang_akun_persediaan', 'barang', type_='foreignkey')
    op.drop_column('barang', 'akun_persediaan_id')
