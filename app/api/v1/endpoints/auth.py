from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import bcrypt

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.pengguna import PenggunaCreate, PenggunaResponse, LoginRequest, TokenResponse
from app.utils.auth import create_access_token

router = APIRouter()


@router.post("/init-admin", response_model=PenggunaResponse)
def create_initial_admin(db: Session = Depends(get_current_db)):
    raise HTTPException(410, "Bootstrap admin publik dinonaktifkan. Gunakan scripts/bootstrap_admin.py di server.")


@router.post("/login", response_model=TokenResponse)
def login(login_data: LoginRequest, db: Session = Depends(get_current_db)):
    """
    Login untuk mendapatkan JWT Token.
    Token ini wajib disertakan di header Authorization (Bearer Token)
    untuk mengakses endpoint lainnya.
    """
    # Cari user berdasarkan username
    user = db.query(Pengguna).filter(Pengguna.username == login_data.username).first()

    # Jika user tidak ditemukan ATAU password salah
    if not user or not bcrypt.checkpw(
        login_data.password.encode("utf-8"), user.password_hash.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Cek apakah user statusnya aktif
    if user.status != "AKTIF":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun dinonaktifkan, hubungi administrator",
        )

    # Buat token berisi ID user dan Role-nya
    access_token = create_access_token(data={"sub": str(user.id), "role": user.role.value})

    return {"access_token": access_token, "token_type": "bearer", "user": user}


@router.post("/refresh", response_model=TokenResponse)
def refresh_session(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #051 — sesi geser (idle-timeout); disempurnakan #055, durasi #061.

    Tukar token lama (yang MASIH sah) dengan token baru berumur penuh
    (ACCESS_TOKEN_EXPIRE_MINUTES = 3 jam [#061], di app/config.py — sengaja
    lebih panjang dari window idle 1 jam [#061]). Frontend memanggil
    endpoint ini secara periodik HANYA saat user aktif — bukan saat idle.
    Efeknya umur sesi "bergeser" mengikuti aktivitas:

    - User terus aktif → token terus diperbarui → sesi tidak pernah berakhir.
    - User diam ≥ 1 jam (#061) → FRONTEND (useSessionWatch) yang mengakhiri
      sesi dan menampilkan "Sesi Berakhir" — umur token (3 jam, #061)
      selalu melampaui window idle, jadi token bukan lagi penyebab sesi
      berakhir lebih awal (penyebab lama: token "tua" 2 jam keburu mati
      sebelum idle 2 jam tercapai → 401 prematur).

    Jadi sesi benar-benar berakhir HANYA karena tidak ada aktivitas.
    """
    # Cek ulang status akun: admin bisa menonaktifkan user kapan pun.
    if current_user.status != "AKTIF":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun dinonaktifkan, hubungi administrator",
        )

    # Data user dikirim segar (role/nama bisa saja diubah admin sejak login).
    access_token = create_access_token(data={"sub": str(current_user.id), "role": current_user.role.value})
    return {"access_token": access_token, "token_type": "bearer", "user": current_user}


@router.get("/me/permissions")
def get_my_permissions(db: Session = Depends(get_current_db),
                       user: Pengguna = Depends(get_current_user)):
    """Effective permissions user saat ini — dipakai frontend untuk gating menu.

    Di-resolve fresh dari server per request: perubahan akses oleh admin
    langsung efektif tanpa perlu login ulang (spesifikasi §7, UAT RBAC-09).
    """
    from app.services import access_service
    summary = access_service.user_access_summary(db, user)
    return {
        'permissions': summary['effectivePermissions'],
        'isSuperAdmin': summary['isSuperAdmin'],
        'roles': summary['roles'],
        'legacyRole': summary['legacyRole'],
    }
