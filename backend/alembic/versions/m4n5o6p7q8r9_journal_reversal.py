"""Link a reversal to its original journal without deleting history."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "m4n5o6p7q8r9"
down_revision = "l3m4n5o6p7q8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jurnal_umum", sa.Column("reversal_of_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_jurnal_reversal", "jurnal_umum", "jurnal_umum", ["reversal_of_id"], ["id"])
    op.create_unique_constraint("uq_jurnal_reversal", "jurnal_umum", ["reversal_of_id"])


def downgrade():
    op.drop_constraint("uq_jurnal_reversal", "jurnal_umum", type_="unique")
    op.drop_constraint("fk_jurnal_reversal", "jurnal_umum", type_="foreignkey")
    op.drop_column("jurnal_umum", "reversal_of_id")
