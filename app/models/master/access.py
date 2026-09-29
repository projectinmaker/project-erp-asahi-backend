"""RBAC v2 — Role & Permission models (Revisi role user, spesifikasi §4).

Konsep (lihat /home/z/revisi-asahi-books/Role user/SPESIFIKASI...md):
- Role      = template hak akses (bukan jabatan HR).
- Permission= kemampuan spesifik `module.resource.action`, registry dari kode.
- Override  = pengecualian per orang, ALLOW atau DENY; DENY menang.
- AuditLog  = catatan append-only setiap perubahan akses.

Kolom `pengguna.role` (enum lama) DIPERTAHANKAN untuk kompatibilitas migrasi
(spesifikasi §4 & §11: "Jangan drop kolom role lama sebelum aman").
"""
import uuid

from sqlalchemy import Column, String, Boolean, ForeignKey, Integer, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class Role(BaseModel, BaseMixin):
    """Template hak akses. `is_system` = bawaan sistem (tidak dapat dihapus/diedit matrix-nya)."""
    __tablename__ = "roles"

    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_system = Column(Boolean, default=False, nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)

    permissions = relationship(
        "Permission", secondary="role_permissions", lazy="selectin",
        primaryjoin="Role.id == RolePermission.role_id",
        secondaryjoin="Permission.id == RolePermission.permission_id",
        viewonly=True,
    )


class Permission(BaseModel, BaseMixin):
    """Registry permission `module.resource.action` — sumber tunggal dari kode (seeder)."""
    __tablename__ = "permissions"

    code = Column(String(120), unique=True, nullable=False, index=True)
    module = Column(String(40), nullable=False, index=True)
    resource = Column(String(60), nullable=False)
    action = Column(String(30), nullable=False)
    name = Column(String(150), nullable=False)
    is_sensitive = Column(Boolean, default=False, nullable=False)


class RolePermission(BaseModel):
    """Pasangan role ↔ permission (unique pair)."""
    __tablename__ = "role_permissions"

    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class UserRole(BaseModel):
    """Link pengguna ↔ role template. Mendukung multiple; tahap awal cukup satu."""
    __tablename__ = "user_roles"

    user_id = Column(UUID(as_uuid=True), ForeignKey("pengguna.id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    linked_at = Column(DateTime(timezone=True), default=None, nullable=True)


class UserPermissionOverride(BaseModel):
    """Pengecualian per orang. DENY selalu menang atas ALLOW/role (spesifikasi §7)."""
    __tablename__ = "user_permission_overrides"

    user_id = Column(UUID(as_uuid=True), ForeignKey("pengguna.id", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)
    effect = Column(String(10), nullable=False)  # ALLOW | DENY
    reason = Column(Text, nullable=True)
    granted_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id", ondelete="SET NULL"), nullable=True)


class AccessAuditLog(BaseModel, BaseMixin):
    """Append-only; tidak menyimpan password/token (spesifikasi §4 & §8.8)."""
    __tablename__ = "access_audit_logs"

    actor_id = Column(UUID(as_uuid=True), ForeignKey("pengguna.id", ondelete="SET NULL"), nullable=True, index=True)
    target_user_id = Column(UUID(as_uuid=True), ForeignKey("pengguna.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(40), nullable=False, index=True)
    before = Column(JSONB, nullable=True)
    after = Column(JSONB, nullable=True)
    reason = Column(Text, nullable=True)
