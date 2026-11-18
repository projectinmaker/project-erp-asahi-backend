"""RBAC v2 — Endpoint manajemen Role & Akses (spesifikasi §9).

Guard: mengelola akses memerl permission system.access.edit (super admin / IT_ADMIN).
Aturan anti-escalation (spesifikasi §7, UAT RBAC-11):
- Tidak boleh mengubah akses diri sendiri (SoD — gunakan admin lain).
- ALLOW hanya boleh diberikan untuk permission yang dimiliki aktor sendiri
  (atau aktor super admin).
- Setiap perubahan tercatat di access_audit_logs (before/after/actor/reason).
"""
# === Update #074 — filter rentang tanggal log audit (dateFrom/dateTo). ===
from datetime import datetime, time as dtime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.access import Role, Permission, RolePermission, UserRole, UserPermissionOverride
# === Update #074 — AccessAuditLog kini dipakai helper filter + endpoint
# aktor & ekspor (sebelumnya diimpor lokal di dalam get_audit_logs). ===
from app.models.master.access import AccessAuditLog
from app.models.master.pengguna import Pengguna
from app.schemas.access import (
    RoleOut, RoleDetailOut, RoleCreateIn, RolePermissionUpdateIn,
    UserAccessOut, UserAccessUpdateIn, AuditLogOut, PermissionModuleOut,
)
from app.schemas.base import PaginatedResponse
from app.services import access_service
# === Update #074 — lama simpan log aktivitas (salinan CSV + purge). ===
from app.services import log_retention_service
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

# === Update #074 — seluruh logika filter audit log dipusatkan di helper
# privat ini supaya dipakai bersama oleh get_audit_logs (daftar) dan
# export_audit_logs (ekspor). Filter #073 (target, aksi koma-OR, aktor,
# pencarian bebas) dipertahankan; ditambah filter rentang tanggal
# dateFrom/dateTo. ===
def _audit_log_query(db: Session, *, search: Optional[str] = None,
                     action: Optional[str] = None,
                     actor_user_id: Optional[UUID] = None,
                     target_user_id: Optional[UUID] = None,
                     date_from: Optional[str] = None,
                     date_to: Optional[str] = None):
    """Query dasar audit log dengan seluruh filter opsional (dipakai daftar & ekspor)."""
    from sqlalchemy import or_
    query = db.query(AccessAuditLog)
    if target_user_id:
        query = query.filter(AccessAuditLog.target_user_id == target_user_id)
    # === Update #073 — filter aksi (koma = OR) ===
    if action:
        actions = [a.strip().upper() for a in action.split(',') if a.strip()]
        if actions:
            query = query.filter(AccessAuditLog.action.in_(actions))
    # === Update #073 — filter aktor ===
    if actor_user_id:
        query = query.filter(AccessAuditLog.actor_id == actor_user_id)
    # === Update #073 — pencarian bebas: cocokkan nama/username aktor ATAU
    # target ATAU kode aksi ATAU teks keterangan (reason — memuat username
    # percobaan pada LOGIN_GAGAL, mis. cari "root" untuk melihat siapa
    # mencoba masuk sebagai root). ===
    if search:
        term = f"%{search}%"
        matched_ids = [
            u.id for u in db.query(Pengguna).filter(
                or_(Pengguna.nama_lengkap.ilike(term), Pengguna.username.ilike(term))
            ).all()
        ]
        query = query.filter(or_(
            AccessAuditLog.actor_id.in_(matched_ids),
            AccessAuditLog.target_user_id.in_(matched_ids),
            AccessAuditLog.action.ilike(term),
            AccessAuditLog.reason.ilike(term),
        ))
    # === Update #074 — filter rentang tanggal 'YYYY-MM-DD' (inklusif):
    # dateFrom → awal hari, dateTo → akhir hari. ===
    if date_from:
        try:
            d = datetime.strptime(date_from, '%Y-%m-%d').date()
        except ValueError:
            raise HTTPException(400, 'Format tanggal harus YYYY-MM-DD')
        query = query.filter(AccessAuditLog.created_at >= datetime.combine(d, dtime.min))
    if date_to:
        try:
            d = datetime.strptime(date_to, '%Y-%m-%d').date()
        except ValueError:
            raise HTTPException(400, 'Format tanggal harus YYYY-MM-DD')
        query = query.filter(AccessAuditLog.created_at <= datetime.combine(d, dtime.max))
    return query


# === Update #074 — parse parameter UUID opsional milik endpoint ekspor. ===
def _optional_uuid(value: Optional[str], field: str) -> Optional[UUID]:
    """Ubah parameter query string menjadi UUID; kosong → None; salah format → 400."""
    if value is None or value == '':
        return None
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(400, f'Format {field} harus UUID')


# === Update #074 — daftar aktor untuk dropdown filter Log Aktivitas. ===
@router.get('/audit-logs/actors')
def get_audit_log_actors(db: Session = Depends(get_current_db),
                         user: Pengguna = Depends(get_current_user)):
    """Pengguna yang pernah tercatat sebagai aktor di log audit (maks 500, urut nama)."""
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.audit.view' not in perms:
        raise HTTPException(403, 'Tidak memiliki izin melihat audit akses')
    rows = (
        db.query(Pengguna)
        .join(AccessAuditLog, AccessAuditLog.actor_id == Pengguna.id)
        .distinct()
        .order_by(Pengguna.nama_lengkap)
        .limit(500)
        .all()
    )
    return {'data': [{'id': str(u.id), 'nama': u.nama_lengkap, 'username': u.username} for u in rows]}


# === Update #074 — response_model=PaginatedResponse[AuditLogOut] DIHAPUS dari
# dekorator supaya key baru `stats` tidak dibuang oleh schema (schema lama
# tidak mengenal field tersebut). Bentuk response LAMA tetap kompatibel:
# key data/total/skip/limit dan bentuk tiap baris TIDAK berubah — konsumen
# lama tidak perlu penyesuaian. ===
@router.get('/audit-logs')
def get_audit_logs(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                   target_user_id: Optional[UUID] = Query(default=None, alias='targetUserId'),
                   # === Update #073 — filter tambahan untuk halaman Log
                   # Aktivitas (Pengaturan ▸ Sistem): aksi (mendukung beberapa,
                   # dipisah koma), aktor, dan pencarian bebas (nama/username
                   # aktor atau target, atau kode aksi). Param lama tetap
                   # kompatibel — dialog Role & Akses tidak perlu berubah. ===
                   action: Optional[str] = Query(default=None, description='Filter aksi, mis. LOGIN,LOGIN_GAGAL'),
                   actor_user_id: Optional[UUID] = Query(default=None, alias='actorUserId'),
                   search: Optional[str] = Query(default=None, description='Cari nama/username aktor-target atau kode aksi'),
                   # === Update #074 — filter rentang tanggal (YYYY-MM-DD). ===
                   date_from: Optional[str] = Query(default=None, alias='dateFrom', description='Tanggal mulai (YYYY-MM-DD)'),
                   date_to: Optional[str] = Query(default=None, alias='dateTo', description='Tanggal akhir (YYYY-MM-DD)'),
                   db: Session = Depends(get_current_db),
                   user: Pengguna = Depends(get_current_user)):
    """Daftar log audit akses (Update #074: filter tanggal + ringkasan `stats`).

    Bentuk lama (data/total/skip/limit) tetap ada dan tidak berubah; key baru
    `stats` berisi total, loginGagal, erp, dan penggunaAkses — dihitung dari
    query filter yang sama TANPA offset/limit.
    """
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.audit.view' not in perms:
        raise HTTPException(403, 'Tidak memiliki izin melihat audit akses')
    query = _audit_log_query(db, search=search, action=action, actor_user_id=actor_user_id,
                             target_user_id=target_user_id, date_from=date_from, date_to=date_to)
    # === Update #074 — statistik dari query filter yang sama (tanpa paging). ===
    total = query.count()
    login_gagal = query.filter(AccessAuditLog.action == 'LOGIN_GAGAL').count()
    erp = query.filter(AccessAuditLog.action.like('ERP_%')).count()
    pengguna_akses = query.filter(AccessAuditLog.action.in_([
        'CREATE_USER', 'UPDATE_USER', 'DEACTIVATE_USER', 'ACTIVATE_USER',
        'DELETE_USER', 'CREATE_ROLE', 'UPDATE_ROLE', 'UPDATE_USER_ACCESS',
    ])).count()
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
    # === Update #074 — key `stats` baru; key lama tetap utuh (kompatibel). ===
    return {
        'data': data, 'total': total, 'skip': skip, 'limit': limit,
        'stats': {
            'total': total,
            'loginGagal': login_gagal,
            'erp': erp,
            'penggunaAkses': pengguna_akses,
        },
    }


# === Update #074 — unduh hasil filter log audit (tombol "Unduh CSV").
# Batas aman supaya response ekspor tidak membengkak; bila hasil filter
# lebih besar dari batas, data dipotong dan `truncated: true` dikirim. ===
EXPORT_MAX_ROWS = 10000


@router.get('/audit-logs/export')
def export_audit_logs(search: Optional[str] = Query(default=None, description='Cari nama/username aktor-target atau kode aksi'),
                      action: Optional[str] = Query(default=None, description='Filter aksi, mis. LOGIN,LOGIN_GAGAL'),
                      actor_user_id: Optional[str] = Query(default=None, alias='actorUserId', description='ID aktor (UUID)'),
                      target_user_id: Optional[str] = Query(default=None, alias='targetUserId', description='ID target (UUID)'),
                      date_from: Optional[str] = Query(default=None, alias='dateFrom', description='Tanggal mulai (YYYY-MM-DD)'),
                      date_to: Optional[str] = Query(default=None, alias='dateTo', description='Tanggal akhir (YYYY-MM-DD)'),
                      db: Session = Depends(get_current_db),
                      user: Pengguna = Depends(get_current_user)):
    """Ekspor hasil filter log audit sebagai JSON (maksimum EXPORT_MAX_ROWS baris).

    Untuk tombol "Unduh CSV" di frontend — frontend menyusun CSV di sisi
    klien dari JSON ini supaya memakai klien `api` yang sama (token
    otentikasi terikut otomatis). Bentuk tiap baris SAMA persis dengan
    get_audit_logs; `total` = jumlah seluruh baris yang cocok filter
    (sebelum dipotong), `truncated` = true bila hasil melebihi batas.
    """
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.audit.view' not in perms:
        raise HTTPException(403, 'Tidak memiliki izin melihat audit akses')
    query = _audit_log_query(db, search=search, action=action,
                             actor_user_id=_optional_uuid(actor_user_id, 'actorUserId'),
                             target_user_id=_optional_uuid(target_user_id, 'targetUserId'),
                             date_from=date_from, date_to=date_to)
    total = query.count()
    rows = query.order_by(AccessAuditLog.created_at.desc()).limit(EXPORT_MAX_ROWS + 1).all()
    truncated = len(rows) > EXPORT_MAX_ROWS
    if truncated:
        rows = rows[:EXPORT_MAX_ROWS]
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
    return {'data': data, 'total': total, 'truncated': truncated, 'maxRows': EXPORT_MAX_ROWS}


# === Update #074 — lama simpan log aktivitas: GET/PUT /audit-logs/
# retention + POST /audit-logs/purge (arsip dulu, hapus kemudian). ===
class RetentionPolicyIn(BaseModel):
    """Payload ubah lama simpan log aktivitas."""
    retentionDays: int
    archiveEnabled: bool


@router.get('/audit-logs/retention')
def get_log_retention(db: Session = Depends(get_current_db),
                      user: Pengguna = Depends(get_current_user)):
    """Info lama simpan log: label, batas tanggal, catatan lama, hapus terakhir & berikutnya."""
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.audit.view' not in perms:
        raise HTTPException(403, 'Tidak memiliki izin melihat audit akses')
    return log_retention_service.retention_info(db, user)


@router.put('/audit-logs/retention')
def update_log_retention(body: RetentionPolicyIn, db: Session = Depends(get_current_db),
                         user: Pengguna = Depends(get_current_user)):
    """Ubah lama simpan log (tercatat sebagai aksi audit RETENSI_UBAH)."""
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.access.edit' not in perms:
        raise HTTPException(403, 'Hanya Super Admin (atau pemegang izin system.access.edit) yang boleh mengubah lama simpan log')
    if body.retentionDays not in log_retention_service.RETENTION_CHOICES:
        raise HTTPException(422, 'Lama simpan harus salah satu dari: Selamanya, 30, 60, 90, 180, atau 365 hari')
    log_retention_service.set_policy(db, body.retentionDays, body.archiveEnabled, user)
    return log_retention_service.retention_info(db, user)


@router.post('/audit-logs/purge')
def purge_audit_logs(db: Session = Depends(get_current_db),
                     user: Pengguna = Depends(get_current_user)):
    """Jalankan penghapusan log lama SEKARANG (tercatat sebagai aksi audit LOG_PURGE)."""
    perms, is_super = access_service.effective_permissions(db, user)
    if not is_super and 'system.access.edit' not in perms:
        raise HTTPException(403, 'Hanya Super Admin (atau pemegang izin system.access.edit) yang boleh menghapus log lama')
    try:
        return log_retention_service.purge_now(db, user)
    except RuntimeError as exc:
        # Gagal menulis salinan CSV → penghapusan dibatalkan, TIDAK ADA baris
        # yang terhapus (prinsip: salinan dulu, hapus kemudian).
        raise HTTPException(500, str(exc))
