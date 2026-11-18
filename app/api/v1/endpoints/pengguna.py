from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import bcrypt

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.pengguna import PenggunaCreate, PenggunaUpdate, PenggunaResponse

# === Update #073 — semua perubahan status/data pengguna kini dicatat ke
# access_audit_logs (halaman baru Pengaturan ▸ Sistem ▸ Log Aktivitas):
# CREATE_USER / UPDATE_USER / DEACTIVATE_USER / ACTIVATE_USER / DELETE_USER,
# ditambah event LOGIN / LOGIN_GAGAL dari endpoint auth. ===
from app.services.access_service import write_audit

router = APIRouter()


def _sync_role_template(db: Session, user: Pengguna) -> None:
    """Tautkan user ke role template sesuai enum role (RBAC v2 auto-link).

    Hanya mengganti link bila link yang ada persis template lama (belum
    dikustomisasi via UI Role & Akses) — akses kustom tidak pernah ditimpa.
    """
    from app.models.master.access import Role, UserRole
    from app.services.access_registry import LEGACY_TEMPLATE, ROLE_TEMPLATES
    legacy = getattr(user.role, 'value', user.role)
    want_code = LEGACY_TEMPLATE.get(legacy)
    if not want_code:
        return
    want = db.query(Role).filter(Role.code == want_code).first()
    if not want:
        return
    links = db.query(UserRole).filter_by(user_id=user.id).all()
    if len(links) == 1:
        current = db.get(Role, links[0].role_id)
        if current and current.code == want_code:
            return  # sudah benar
        # Link lama = template default enum lama → ganti mengikuti enum baru
        if current and current.code in LEGACY_TEMPLATE.values() and current.code != 'SUPER_ADMIN':
            db.delete(links[0])
    elif len(links) == 0:
        pass  # belum ada link → buat baru
    else:
        return  # multiple/kustom → jangan disentuh
    if ROLE_TEMPLATES.get(want_code, {}).get('super', False):
        db.add(UserRole(user_id=user.id, role_id=want.id))
    else:
        db.add(UserRole(user_id=user.id, role_id=want.id))


def _role_val(role) -> str:
    """Update #073 — nilai string enum role untuk payload audit."""
    return getattr(role, 'value', role) if role else None


def _guard_last_super_admin(db: Session, item: Pengguna) -> None:
    """Update #073 — proteksi 'jangan hapus/nonaktifkan satu-satunya super admin'
    (dipakai bersama oleh nonaktifkan & hapus permanen; dulu inline di delete)."""
    from app.services.access_service import effective_permissions
    from app.models.master.access import Role, UserRole
    _, is_super = effective_permissions(db, item)
    if is_super:
        other_supers = (
            db.query(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .filter(Role.code == 'SUPER_ADMIN', UserRole.user_id != item.id)
            .count()
        )
        legacy_admins = (
            db.query(Pengguna)
            .filter(Pengguna.status == 'AKTIF', Pengguna.id != item.id,
                    Pengguna.role == 'ADMINISTRATOR')
            .count()
        )
        if other_supers == 0 and legacy_admins == 0:
            raise HTTPException(status_code=400, detail="Tidak bisa menonaktifkan satu-satunya super admin")


@router.get("", response_model=PaginatedResponse[PenggunaResponse])
def get_pengguna_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="Cari berdasarkan nama, username, atau email"),
    # === Update #073 — filter status (AKTIF / NONAKTIF) untuk daftar Pengguna ===
    status: Optional[str] = Query(None, description="Filter status pengguna: AKTIF / NONAKTIF"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """List semua pengguna dengan pagination."""
    query = db.query(Pengguna)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            Pengguna.nama_lengkap.ilike(search_term)
            | Pengguna.username.ilike(search_term)
            | Pengguna.email.ilike(search_term)
        )

    # === Update #073 — filter status ===
    if status:
        query = query.filter(Pengguna.status == status.upper())

    total = query.count()
    data = query.order_by(Pengguna.created_at.desc()).offset(skip).limit(limit).all()

    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("", response_model=PenggunaResponse, status_code=status.HTTP_201_CREATED)
def create_pengguna(
    data_in: PenggunaCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat pengguna baru. Password akan di-hash otomatis."""
    # Cek duplikat username
    existing = db.query(Pengguna).filter(Pengguna.username == data_in.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")

    # Cek duplikat email
    existing_email = db.query(Pengguna).filter(Pengguna.email == data_in.email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Email sudah digunakan")

    # Hash password
    hashed_password = bcrypt.hashpw(data_in.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    db_obj = Pengguna(
        username=data_in.username,
        nama_lengkap=data_in.nama_lengkap,
        email=data_in.email,
        password_hash=hashed_password,
        role=data_in.role,
    )
    db.add(db_obj)
    db.flush()
    _sync_role_template(db, db_obj)
    # === Update #073 — audit trail pembuatan pengguna ===
    write_audit(
        db, current_user, 'CREATE_USER', db_obj.id, None,
        {'username': db_obj.username, 'namaLengkap': db_obj.nama_lengkap,
         'email': db_obj.email, 'role': _role_val(db_obj.role)},
        'Pengguna baru dibuat',
    )
    db.commit()
    db.refresh(db_obj)
    return db_obj


@router.get("/{pengguna_id}", response_model=PenggunaResponse)
def get_pengguna_detail(
    pengguna_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Detail satu pengguna."""
    item = db.query(Pengguna).filter(Pengguna.id == pengguna_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")
    return item


@router.put("/{pengguna_id}", response_model=PenggunaResponse)
def update_pengguna(
    pengguna_id: UUID,
    data_in: PenggunaUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update pengguna. Jika password dikirim, akan di-hash ulang."""
    item = db.query(Pengguna).filter(Pengguna.id == pengguna_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)

    # Hash password jika di-update
    if "password" in update_data and update_data["password"]:
        update_data["password_hash"] = bcrypt.hashpw(
            update_data.pop("password").encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
    else:
        update_data.pop("password", None)

    # === Update #073 — snapshot sebelum perubahan untuk audit trail ===
    # (password TIDAK PERNAH dicatat — hanya identitas & role yang berubah)
    audit_fields = {'namaLengkap': 'nama_lengkap', 'email': 'email', 'role': 'role'}
    tracked = [k for k in update_data if k in audit_fields]
    before = {k: getattr(item, audit_fields[k]) for k in tracked}
    before = {k: (_role_val(v) if k == 'role' else v) for k, v in before.items()}

    for field, value in update_data.items():
        setattr(item, field, value)

    db.add(item)
    db.flush()
    if "role" in update_data:
        _sync_role_template(db, item)
    # === Update #073 — audit trail perubahan data pengguna ===
    if tracked:
        after = {k: (_role_val(getattr(item, audit_fields[k])) if k == 'role' else getattr(item, audit_fields[k])) for k in tracked}
        write_audit(
            db, current_user, 'UPDATE_USER', item.id, before, after,
            'Perubahan data pengguna',
        )
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{pengguna_id}", status_code=status.HTTP_200_OK)
def delete_pengguna(
    pengguna_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Nonaktifkan pengguna (soft delete).

    Pengguna tidak bisa login lagi namun barisnya tetap ada karena data
    historis transaksi masih merujuk kepadanya. Untuk benar-benar menghapus
    baris pengguna, pakai DELETE /{pengguna_id}/permanent (Update #073).
    """
    if current_user.id == pengguna_id:
        raise HTTPException(status_code=400, detail="Tidak bisa menonaktifkan akun sendiri")

    item = db.query(Pengguna).filter(Pengguna.id == pengguna_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Pengguna sudah tidak aktif")

    # RBAC-15: jangan nonaktifkan satu-satunya super admin
    _guard_last_super_admin(db, item)

    item.status = "NONAKTIF"
    db.add(item)
    # === Update #073 — audit trail nonaktifkan pengguna ===
    write_audit(
        db, current_user, 'DEACTIVATE_USER', item.id,
        {'status': 'AKTIF'}, {'status': 'NONAKTIF'},
        'Dinonaktifkan via menu Pengguna',
    )
    db.commit()
    return {"message": "Pengguna berhasil dinonaktifkan"}


# ═══════════════════════════════════════════════════════════════════════════
# === Update #073 — aktifkan ulang & hapus permanen ===
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/{pengguna_id}/activate", response_model=PenggunaResponse)
def activate_pengguna(
    pengguna_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #073 — aktifkan ulang pengguna yang dinonaktifkan.

    Izin mengikuti pemetaan modul pengguna: POST + fungsi bukan create_* →
    system.users.edit (sama dengan mengubah data pengguna).
    """
    item = db.query(Pengguna).filter(Pengguna.id == pengguna_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")
    if item.status == "AKTIF":
        raise HTTPException(status_code=400, detail="Pengguna sudah aktif")

    item.status = "AKTIF"
    db.add(item)
    write_audit(
        db, current_user, 'ACTIVATE_USER', item.id,
        {'status': 'NONAKTIF'}, {'status': 'AKTIF'},
        'Diaktifkan ulang via menu Pengguna',
    )
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{pengguna_id}/permanent", status_code=status.HTTP_200_OK)
def delete_pengguna_permanent(
    pengguna_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #073 — hapus permanen baris pengguna (hard delete).

    Aman secara historis: bila pengguna masih dirujuk dokumen/jurnal
    (FK created_by dsb.), database menolak → 409 dengan saran menonaktifkan
    sebagai gantinya. Baris yang hanya dirujuk log audit/link role aman
    dihapus (audit → SET NULL; role link → CASCADE). Identitas pengguna
    tetap tercatat di log aktivitas (payload before pada aksi DELETE_USER).
    """
    if current_user.id == pengguna_id:
        raise HTTPException(status_code=400, detail="Tidak bisa menghapus akun sendiri")

    item = db.query(Pengguna).filter(Pengguna.id == pengguna_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")

    # Proteksi yang sama dengan nonaktifkan: jangan hapus satu-satunya
    # super admin (menghapus = menonaktifkan + menghilangkan barisnya).
    _guard_last_super_admin(db, item)

    # Catat audit SEBELUM delete — target_user_id otomatis SET NULL oleh DB,
    # identitas lengkap disimpan di payload `before`.
    write_audit(
        db, current_user, 'DELETE_USER', item.id,
        {'username': item.username, 'namaLengkap': item.nama_lengkap,
         'email': item.email, 'role': _role_val(item.role), 'status': item.status},
        None,
        'Hapus permanen via menu Pengguna',
    )
    try:
        db.delete(item)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=("Pengguna masih terhubung dengan data historis transaksi "
                    "(dokumen/jurnal yang pernah dibuat/diprosesnya). "
                    "Nonaktifkan pengguna sebagai gantinya — data historis "
                    "tetap harus merujuk ke pengguna ini."),
        )
    return {"message": "Pengguna berhasil dihapus permanen"}
