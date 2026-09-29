"""RBAC v2 — Pydantic schemas untuk endpoint /access/* dan /auth/me/permissions."""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class PermissionActionOut(BaseSchema):
    action: str
    actionName: str
    isSensitive: bool = False


class PermissionResourceOut(BaseSchema):
    resource: str
    resourceName: str
    actions: List[PermissionActionOut]


class PermissionModuleOut(BaseSchema):
    module: str
    moduleName: str
    resources: List[PermissionResourceOut]


class RoleBriefOut(BaseSchema):
    id: UUID
    code: str
    name: str


class RoleOut(BaseSchema):
    id: UUID
    code: str
    name: str
    description: Optional[str] = None
    isSystem: bool = False
    status: str = 'AKTIF'
    permissionCount: int = 0


class RoleDetailOut(RoleOut):
    permissions: List[str] = []


class RoleCreateIn(BaseSchema):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    permissions: List[str] = []


class RolePermissionUpdateIn(BaseSchema):
    permissions: List[str]
    reason: str = Field(min_length=3, max_length=500)


class OverrideIn(BaseSchema):
    permissionCode: str
    effect: str = Field(pattern='^(ALLOW|DENY)$')
    reason: Optional[str] = Field(default=None, max_length=500)


class UserAccessOut(BaseSchema):
    userId: UUID
    username: str
    namaLengkap: str
    roles: List[RoleBriefOut]
    templatePermissions: List[str]
    overrides: List[dict]
    effectivePermissions: List[str]
    isSuperAdmin: bool = False
    legacyRole: str


class UserAccessUpdateIn(BaseSchema):
    roleIds: List[UUID] = []
    overrides: List[OverrideIn] = []
    reason: str = Field(min_length=3, max_length=500)


class MePermissionsOut(BaseSchema):
    permissions: List[str]
    isSuperAdmin: bool = False
    roles: List[RoleBriefOut]
    legacyRole: str


class AuditLogOut(BaseSchema):
    id: UUID
    actorId: Optional[UUID] = None
    actorNama: Optional[str] = None
    targetUserId: Optional[UUID] = None
    targetNama: Optional[str] = None
    action: str
    before: Optional[dict] = None
    after: Optional[dict] = None
    reason: Optional[str] = None
    createdAt: datetime
