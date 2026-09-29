"""rbac_v2_access_control

Revisi role user (repo revisi-asahi-books) — struktur RBAC v2:

- roles, permissions (registry), role_permissions, user_roles,
  user_permission_overrides, access_audit_logs (spesifikasi §4).
- Kolom `pengguna.role` (enum lama) DIPERTAHANKAN — kompatibilitas migrasi.
- Data (registry permission, template role, link user lama) di-seed oleh
  app.services.access_service.seed_access() saat startup — idempotent,
  sumber tunggal dari kode (spesifikasi §14).

Revision ID: a1b2c3d4e5f6
Revises: h9i0j1k2l3m4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "rbac00000001"
down_revision: Union[str, Sequence[str], None] = "h9i0j1k2l3m4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="AKTIF"),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_roles_code", "roles", ["code"], unique=True)

    op.create_table(
        "permissions",
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("resource", sa.String(length=60), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_permissions_code", "permissions", ["code"], unique=True)
    op.create_index("ix_permissions_module", "permissions", ["module"])

    op.create_table(
        "role_permissions",
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_role_permissions_role", "role_permissions", ["role_id"])

    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pengguna.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_roles_user", "user_roles", ["user_id"])

    op.create_table(
        "user_permission_overrides",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pengguna.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("effect", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pengguna.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_user_permission_overrides_user", "user_permission_overrides", ["user_id"])

    op.create_table(
        "access_audit_logs",
        sa.Column("actor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pengguna.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pengguna.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_access_audit_logs_actor", "access_audit_logs", ["actor_id"])
    op.create_index("ix_access_audit_logs_target", "access_audit_logs", ["target_user_id"])
    op.create_index("ix_access_audit_logs_action", "access_audit_logs", ["action"])


def downgrade() -> None:
    op.drop_table("access_audit_logs")
    op.drop_table("user_permission_overrides")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
