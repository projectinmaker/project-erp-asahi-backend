"""RBAC v2 — Resolver effective permission, seeder registry, dan audit.

Precedence (spesifikasi §7):
    effective = (UNION(role permissions) + user ALLOW − user DENY)
User tanpa user_roles → fallback template dari enum role lama (parity §11).
SUPER_ADMIN → short-circuit seluruh registry (+ endpoint belum terpetakan).

Permission di-resolve di server pada SETIAP request (bukan JWT claim) sehingga
perubahan akses langsung efektif tanpa revoke token (RBAC-09 terpenuhi by-design).
"""
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.master.access import (
    Role, Permission, RolePermission, UserRole, UserPermissionOverride, AccessAuditLog,
)
from app.models.master.pengguna import Pengguna
from app.services.access_registry import (
    REGISTRY, REGISTRY_CODES, ROLE_TEMPLATES, LEGACY_TEMPLATE, MODULES,
    RESOURCE_NAMES, ACTION_NAMES,
)


# ═══════════════════════════════════════════════════════════════════════════
# Seeder (idempotent, dipanggil saat startup & setelah migrasi)
# ═══════════════════════════════════════════════════════════════════════════

def seed_access(db: Session) -> dict:
    """Sinkronkan registry permission + template role + link user lama.
    Tidak pernah menghapus baris; hanya menambah/memperbarui (controlled seed)."""
    stats = {'permissions_added': 0, 'permissions_updated': 0,
             'roles_added': 0, 'role_links_added': 0}

    # 1) Registry permission
    by_code = {p.code: p for p in db.query(Permission).all()}
    for module, resource, action, name, is_sensitive in REGISTRY:
        code = f"{module}.{resource}.{action}"
        if code in by_code:
            p = by_code[code]
            if p.name != name or p.is_sensitive != is_sensitive or p.module != module:
                p.name, p.is_sensitive, p.module, p.resource, p.action = name, is_sensitive, module, resource, action
                stats['permissions_updated'] += 1
            continue
        p = Permission(code=code, module=module, resource=resource, action=action,
                       name=name, is_sensitive=is_sensitive)
        db.add(p)
        by_code[code] = p
        stats['permissions_added'] += 1
    db.flush()

    # 2) Template role + matrix permission-nya
    perm_all = {p.code: p for p in db.query(Permission).all()}
    roles_by_code = {r.code: r for r in db.query(Role).all()}
    for code, tpl in ROLE_TEMPLATES.items():
        role = roles_by_code.get(code)
        if role is None:
            role = Role(code=code, name=tpl['name'], description=tpl['description'],
                        is_system=tpl['is_system'], status='AKTIF')
            db.add(role)
            roles_by_code[code] = role
            stats['roles_added'] += 1
        elif role.name != tpl['name'] or role.description != tpl['description']:
            role.name, role.description = tpl['name'], tpl['description']
        db.flush()
        if not tpl['super']:  # super admin tidak butuh matrix
            want = {perm_all[c].id for c in tpl['permissions'] if c in perm_all}
            have = {rp.permission_id for rp in db.query(RolePermission).filter_by(role_id=role.id)}
            for pid in want - have:
                db.add(RolePermission(role_id=role.id, permission_id=pid))
            for pid in have - want:
                db.query(RolePermission).filter_by(role_id=role.id, permission_id=pid).delete()
    db.flush()

    # 3) Link user existing (enum lama → template) bila belum punya link
    for user in db.query(Pengguna).all():
        exists = db.query(UserRole).filter_by(user_id=user.id).first()
        if exists:
            continue
        legacy = getattr(user.role, 'value', user.role)
        tpl_code = LEGACY_TEMPLATE.get(legacy)
        if tpl_code and tpl_code in roles_by_code:
            db.add(UserRole(user_id=user.id, role_id=roles_by_code[tpl_code].id))
            stats['role_links_added'] += 1

    db.commit()
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# Resolver
# ═══════════════════════════════════════════════════════════════════════════

def _template_perms(tpl_code: str) -> set:
    tpl = ROLE_TEMPLATES.get(tpl_code)
    if not tpl:
        return set()
    return REGISTRY_CODES if tpl['super'] else set(tpl['permissions'])


def _user_role_codes(db: Session, user: Pengguna) -> list:
    """Kode role template user (aktif saja)."""
    rows = (
        db.query(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user.id, Role.status == 'AKTIF')
        .all()
    )
    return [r.code for r in rows]


def effective_permissions(db: Session, user: Pengguna) -> tuple:
    """Return (set code, is_super)."""
    role_codes = _user_role_codes(db, user)
    if 'SUPER_ADMIN' in role_codes:
        return REGISTRY_CODES, True

    perms: set = set()
    for code in role_codes:
        perms |= _template_perms(code)

    # Fallback parity: user tanpa link sama sekali → turunkan dari enum lama
    if not role_codes:
        legacy = getattr(user.role, 'value', user.role)
        perms |= _template_perms(LEGACY_TEMPLATE.get(legacy, ''))

    # Override: ALLOW menambah, DENY selalu menang (RBAC-04)
    allow, deny = set(), set()
    rows = (
        db.query(Permission.code, UserPermissionOverride.effect)
        .join(UserPermissionOverride, UserPermissionOverride.permission_id == Permission.id)
        .filter(UserPermissionOverride.user_id == user.id)
        .all()
    )
    for code, effect in rows:
        (allow if effect == 'ALLOW' else deny).add(code)

    perms = (perms | allow) - deny
    return perms, False


def has_permission(db: Session, user: Pengguna, code: str) -> bool:
    perms, is_super = effective_permissions(db, user)
    return is_super or code in perms


def user_access_summary(db: Session, user: Pengguna) -> dict:
    """Snapshot akses user untuk GET /access/users/{id}/access & /auth/me/permissions."""
    role_codes = _user_role_codes(db, user)
    role_rows = (
        db.query(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user.id)
        .all()
    )
    template_perms: set = set()
    for code in role_codes:
        template_perms |= _template_perms(code)

    overrides = (
        db.query(Permission.code, UserPermissionOverride)
        .join(UserPermissionOverride, UserPermissionOverride.permission_id == Permission.id)
        .filter(UserPermissionOverride.user_id == user.id)
        .order_by(Permission.code)
        .all()
    )
    override_out = [
        {'permissionCode': code, 'effect': ov.effect, 'reason': ov.reason}
        for code, ov in overrides
    ]
    perms, is_super = effective_permissions(db, user)
    if is_super:
        perms = set(REGISTRY_CODES)
    return {
        'roles': [{'id': str(r.id), 'code': r.code, 'name': r.name} for r in role_rows],
        'templatePermissions': sorted(template_perms if not is_super else REGISTRY_CODES),
        'overrides': override_out,
        'effectivePermissions': sorted(perms),
        'isSuperAdmin': is_super,
        'legacyRole': getattr(user.role, 'value', user.role),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Audit
# ═══════════════════════════════════════════════════════════════════════════

def write_audit(db: Session, actor: Pengguna, action: str,
                target_user_id: Optional[UUID], before: Optional[dict],
                after: Optional[dict], reason: Optional[str]) -> None:
    db.add(AccessAuditLog(
        actor_id=actor.id if actor else None,
        target_user_id=target_user_id,
        action=action,
        before=before, after=after,
        reason=reason,
    ))


# ═══════════════════════════════════════════════════════════════════════════
# Registry untuk UI (GET /access/permissions)
# ═══════════════════════════════════════════════════════════════════════════

def registry_tree() -> list:
    """Tree modul → resource → aksi untuk UI Role & Akses."""
    by_module: dict = {}
    for module, resource, action, name, is_sensitive in REGISTRY:
        m = by_module.setdefault(module, {
            'module': module, 'moduleName': MODULES[module], 'resources': {},
        })
        r = m['resources'].setdefault(resource, {
            'resource': resource, 'resourceName': RESOURCE_NAMES.get(resource, resource), 'actions': [],
        })
        r['actions'].append({
            'action': action, 'actionName': ACTION_NAMES.get(action, action),
            'isSensitive': is_sensitive,
        })
    out = []
    for module in MODULES:  # urut konsisten
        if module in by_module:
            m = by_module[module]
            m['resources'] = list(m['resources'].values())
            out.append(m)
    return out
