"""RBAC v2 — Endpoint manajemen Role & Akses (spesifikasi §9).

Guard: mengelola akses memerl permission system.access.edit (super admin / IT_ADMIN).
Aturan anti-escalation (spesifikasi §7, UAT RBAC-11):
- Tidak boleh mengubah akses diri sendiri (SoD — gunakan admin lain).
- ALLOW hanya boleh diberikan untuk permission yang dimiliki aktor sendiri
  (atau aktor super admin).
- Setiap perubahan tercatat di access_audit_logs (before/after/actor/reason).
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.access import Role, Permission, RolePermission, UserRole, UserPermissionOverride
from app.models.master.pengguna import Pengguna
from app.schemas.access import (
    RoleOut, RoleDetailOut, RoleCreateIn, RolePermissionUpdateIn,
    UserAccessOut, UserAccessUpdateIn, AuditLogOut, PermissionModuleOut,
)
from app.schemas.base import PaginatedResponse
from app.services import access_service
from app.services.access_registry import REGISTRY_CODES, ROLE_TEMPLATES

router = APIRouter()


# ═════════════════════════ Guard ══════════════════════════════════════════

def _require_access_view(db: Session, user: Pengguna):
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and not ({'system.access.view', 'system.access.edit'} & perms):
        raise HTTPException(403, 'Tidak memiliki izin melihat konfigurasi akses')


def _require_access_edit(db: Session, user: Pengguna):
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.access.edit' not in perms:
        raise HTTPException(403, 'Tidak memiliki izin mengubah konfigurasi akses')


def _require_roles_view(db: Session, user: Pengguna):
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and not ({'system.roles.view', 'system.roles.edit'} & perms):
        raise HTTPException(403, 'Tidak memiliki izin melihat role template')


def _require_roles_edit(db: Session, user: Pengguna):
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.roles.edit' not in perms:
        raise HTTPException(403, 'Tidak memiliki izin mengubah role template')


# ═════════════════════════ Registry ═══════════════════════════════════════

@router.get('/permissions', response_model=list[PermissionModuleOut])
def get_permission_registry(db: Session = Depends(get_current_db),
                            user: Pengguna = Depends(get_current_user)):
    """Registry modul → resource → aksi (sumber tunggal untuk UI Role & Akses)."""
    _require_access_view(db, user)
    return access_service.registry_tree()


# ═════════════════════════ Role template ═══════════════════════════════════

def _role_out(db: Session, role: Role) -> dict:
    count = db.query(RolePermission).filter_by(role_id=role.id).count()
    is_super = ROLE_TEMPLATES.get(role.code, {}).get('super', False)
    return {
        'id': role.id, 'code': role.code, 'name': role.name,
        'description': role.description, 'isSystem': role.is_system,
        'status': role.status,
        'permissionCount': len(REGISTRY_CODES) if is_super else count,
    }


@router.get('/roles', response_model=PaginatedResponse[RoleOut])
def get_roles(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
              db: Session = Depends(get_current_db), user: Pengguna = Depends(get_current_user)):
    _require_roles_view(db, user)
    query = db.query(Role).order_by(Role.is_system.desc(), Role.code)
    total = query.count()
    roles = query.offset(skip).limit(limit).all()
    return {'data': [_role_out(db, r) for r in roles], 'total': total, 'skip': skip, 'limit': limit}


@router.get('/roles/{role_id}', response_model=RoleDetailOut)
def get_role_detail(role_id: UUID, db: Session = Depends(get_current_db),
                    user: Pengguna = Depends(get_current_user)):
    _require_roles_view(db, user)
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, 'Role template tidak ditemukan')
    out = _role_out(db, role)
    if ROLE_TEMPLATES.get(role.code, {}).get('super', False):
        out['permissions'] = sorted(REGISTRY_CODES)
    else:
        codes = (
            db.query(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .filter(RolePermission.role_id == role.id)
            .order_by(Permission.code)
            .all()
        )
        out['permissions'] = [c for (c,) in codes]
    return out


@router.post('/roles', response_model=RoleDetailOut, status_code=status.HTTP_201_CREATED)
def create_role(data_in: RoleCreateIn, db: Session = Depends(get_current_db),
                user: Pengguna = Depends(get_current_user)):
    _require_roles_edit(db, user)
    code = data_in.code.strip().upper()
    if ' ' in code:
        raise HTTPException(400, 'Kode role tidak boleh mengandung spasi')
    if db.query(Role).filter(Role.code == code).first():
        raise HTTPException(400, 'Kode role sudah digunakan')

    invalid = [c for c in data_in.permissions if c not in REGISTRY_CODES]
    if invalid:
        raise HTTPException(400, f"Permission code tidak dikenal: {', '.join(invalid[:5])}")

    # Anti-escalation: role baru tidak boleh berisi permission di luar kepemilikan aktor
    actor_perms, is_super = access_service.effective_permissions(db, user)
    if not is_super:
        beyond = [c for c in data_in.permissions if c not in actor_perms]
        if beyond:
            raise HTTPException(403, f'Tidak dapat membuat role berisi permission yang Anda tidak miliki: {", ".join(beyond[:5])}')

    role = Role(code=code, name=data_in.name, description=data_in.description,
                is_system=False, status='AKTIF')
    db.add(role)
    db.flush()
    perm_rows = db.query(Permission).filter(Permission.code.in_(data_in.permissions)).all() if data_in.permissions else []
    for p in perm_rows:
        db.add(RolePermission(role_id=role.id, permission_id=p.id))
    access_service.write_audit(db, user, 'CREATE_ROLE', None,
                               before=None,
                               after={'code': code, 'name': data_in.name, 'permissions': sorted(data_in.permissions)},
                               reason=f'Buat role template baru: {data_in.name}')
    db.commit()
    db.refresh(role)
    return get_role_detail(role.id, db, user)


@router.put('/roles/{role_id}/permissions', response_model=RoleDetailOut)
def update_role_permissions(role_id: UUID, data_in: RolePermissionUpdateIn,
                            db: Session = Depends(get_current_db),
                            user: Pengguna = Depends(get_current_user)):
    _require_roles_edit(db, user)
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, 'Role template tidak ditemukan')
    if role.is_system:
        raise HTTPException(400, 'Role sistem tidak dapat diubah matriksnya. Kloning menjadi role baru terlebih dahulu')
    if ROLE_TEMPLATES.get(role.code, {}).get('super', False):
        raise HTTPException(400, 'SUPER_ADMIN tidak dapat diubah')

    invalid = [c for c in data_in.permissions if c not in REGISTRY_CODES]
    if invalid:
        raise HTTPException(400, f"Permission code tidak dikenal: {', '.join(invalid[:5])}")

    before_codes = {c for (c,) in db.query(Permission.code)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .filter(RolePermission.role_id == role.id).all()}

    actor_perms, is_super = access_service.effective_permissions(db, user)
    if not is_super:
        beyond = [c for c in data_in.permissions if c not in actor_perms]
        if beyond:
            raise HTTPException(403, f'Tidak dapat memberikan permission yang Anda tidak miliki: {", ".join(beyond[:5])}')

    want = set(data_in.permissions)
    perm_rows = db.query(Permission).filter(Permission.code.in_(want)).all() if want else []
    by_code = {p.code: p for p in perm_rows}
    current = {rp.permission_id for rp in db.query(RolePermission).filter_by(role_id=role.id)}
    want_ids = {by_code[c].id for c in want}
    for pid in want_ids - current:
        db.add(RolePermission(role_id=role.id, permission_id=pid))
    for pid in current - want_ids:
        db.query(RolePermission).filter_by(role_id=role.id, permission_id=pid).delete()

    access_service.write_audit(db, user, 'UPDATE_ROLE', None,
                               before={'code': role.code, 'permissions': sorted(before_codes)},
                               after={'code': role.code, 'permissions': sorted(want)},
                               reason=data_in.reason)
    db.commit()
    return get_role_detail(role.id, db, user)


# ═════════════════════════ User access ════════════════════════════════════

def _user_access(db: Session, target: Pengguna) -> dict:
    summary = access_service.user_access_summary(db, target)
    return {
        'userId': target.id, 'username': target.username,
        'namaLengkap': target.nama_lengkap,
        **summary,
    }


@router.get('/users/{user_id}/access', response_model=UserAccessOut)
def get_user_access(user_id: UUID, db: Session = Depends(get_current_db),
                    user: Pengguna = Depends(get_current_user)):
    _require_access_view(db, user)
    target = db.get(Pengguna, user_id)
    if not target:
        raise HTTPException(404, 'Pengguna tidak ditemukan')
    return _user_access(db, target)


@router.get('/users/{user_id}/effective-permissions')
def get_user_effective(user_id: UUID, db: Session = Depends(get_current_db),
                       user: Pengguna = Depends(get_current_user)):
    _require_access_view(db, user)
    target = db.get(Pengguna, user_id)
    if not target:
        raise HTTPException(404, 'Pengguna tidak ditemukan')
    perms, is_super = access_service.effective_permissions(db, target)
    return {'permissions': sorted(REGISTRY_CODES if is_super else perms), 'isSuperAdmin': is_super}


@router.put('/users/{user_id}/access', response_model=UserAccessOut)
def update_user_access(user_id: UUID, data_in: UserAccessUpdateIn,
                       db: Session = Depends(get_current_db),
                       user: Pengguna = Depends(get_current_user)):
    """Simpan Role & Akses seorang pengguna (template + override ALLOW/DENY).

    - Tidak boleh mengubah akses sendiri (SoD / anti self-escalation).
    - ALLOW hanya untuk permission yang dimiliki aktor (atau aktor super admin).
    - Menautkan role berarti seluruh permission template role harus ⊆ aktor
      (kecuali aktor super admin).
    """
    _require_access_edit(db, user)
    target = db.get(Pengguna, user_id)
    if not target:
        raise HTTPException(404, 'Pengguna tidak ditemukan')
    if target.id == user.id:
        raise HTTPException(400, 'Tidak dapat mengubah akses akun sendiri. Minta admin lain (separation of duties)')

    # Validasi kode & efek override
    seen = set()
    for ov in data_in.overrides:
        if ov.permissionCode not in REGISTRY_CODES:
            raise HTTPException(400, f"Permission code tidak dikenal: {ov.permissionCode}")
        if ov.permissionCode in seen:
            raise HTTPException(400, f'Override duplikat untuk {ov.permissionCode}')
        seen.add(ov.permissionCode)

    actor_perms, is_super = access_service.effective_permissions(db, user)

    # Validasi role template
    roles = [db.get(Role, rid) for rid in data_in.roleIds]
    if any(r is None for r in roles):
        raise HTTPException(400, 'Salah satu role template tidak ditemukan')
    if any(ROLE_TEMPLATES.get(r.code, {}).get('super', False) for r in roles):
        raise HTTPException(400, 'SUPER_ADMIN tidak dapat ditautkan manual; ubah melalui skrip terkontrol')
    if not is_super:
        for r in roles:
            tpl = ROLE_TEMPLATES.get(r.code)
            r_perms = set(tpl['permissions']) if tpl else {
                c for (c,) in db.query(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .filter(RolePermission.role_id == r.id).all()
            }
            beyond = r_perms - actor_perms
            if beyond:
                raise HTTPException(403, f'Role {r.code} berisi permission yang Anda tidak miliki: {", ".join(sorted(beyond)[:5])}')

    # Anti-escalation untuk ALLOW
    if not is_super:
        beyond = [ov.permissionCode for ov in data_in.overrides
                  if ov.effect == 'ALLOW' and ov.permissionCode not in actor_perms]
        if beyond:
            raise HTTPException(403, f'Tidak dapat memberikan ALLOW untuk permission yang Anda tidak miliki: {", ".join(beyond[:5])}')

    # Snapshot before untuk audit
    before = _user_access(db, target)

    # Terapkan user_roles
    db.query(UserRole).filter_by(user_id=target.id).delete()
    for r in roles:
        db.add(UserRole(user_id=target.id, role_id=r.id))

    # Terapkan overrides
    db.query(UserPermissionOverride).filter_by(user_id=target.id).delete()
    perm_rows = db.query(Permission).filter(
        Permission.code.in_([ov.permissionCode for ov in data_in.overrides] or [None])
    ).all() if data_in.overrides else []
    perm_by_code = {p.code: p for p in perm_rows}
    for ov in data_in.overrides:
        p = perm_by_code.get(ov.permissionCode)
        db.add(UserPermissionOverride(
            user_id=target.id, permission_id=p.id, effect=ov.effect,
            reason=ov.reason, granted_by=user.id,
        ))

    db.flush()
    after = _user_access(db, target)
    access_service.write_audit(
        db, user, 'UPDATE_USER_ACCESS', target.id,
        before={'roles': before['roles'], 'overrides': before['overrides']},
        after={'roles': after['roles'], 'overrides': after['overrides']},
        reason=data_in.reason,
    )
    db.commit()
    return after


# ═════════════════════════ Audit log ═══════════════════════════════════════

@router.get('/audit-logs', response_model=PaginatedResponse[AuditLogOut])
def get_audit_logs(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                   target_user_id: Optional[UUID] = Query(default=None, alias='targetUserId'),
                   db: Session = Depends(get_current_db),
                   user: Pengguna = Depends(get_current_user)):
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.audit.view' not in perms:
        raise HTTPException(403, 'Tidak memiliki izin melihat audit akses')
    from app.models.master.access import AccessAuditLog
    query = db.query(AccessAuditLog)
    if target_user_id:
        query = query.filter(AccessAuditLog.target_user_id == target_user_id)
    total = query.count()
    rows = query.order_by(AccessAuditLog.created_at.desc()).offset(skip).limit(limit).all()
    actor_ids = {r.actor_id for r in rows if r.actor_id}
    target_ids = {r.target_user_id for r in rows if r.target_user_id}
    users = {u.id: u for u in db.query(Pengguna).filter(Pengguna.id.in_(actor_ids | target_ids)).all()}
    data = [{
        'id': r.id,
        'actorId': r.actor_id,
        'actorNama': users[r.actor_id].nama_lengkap if r.actor_id in users else None,
        'targetUserId': r.target_user_id,
        'targetNama': users[r.target_user_id].nama_lengkap if r.target_user_id in users else None,
        'action': r.action, 'before': r.before, 'after': r.after,
        'reason': r.reason, 'createdAt': r.created_at,
    } for r in rows]
    return {'data': data, 'total': total, 'skip': skip, 'limit': limit}
