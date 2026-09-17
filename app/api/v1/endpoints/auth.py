from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import bcrypt

from app.api.deps import get_current_db
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
