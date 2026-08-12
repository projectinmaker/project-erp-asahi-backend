"""
Asahi ERP - API Dependencies
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials  # <-- GANTI INI
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.auth import verify_token
from app.models.master.pengguna import Pengguna

# Re-export get_db sebagai get_current_db
get_current_db = get_db

# Ganti OAuth2 dengan HTTPBearer
security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),  # <-- GANTI INI
    db: Session = Depends(get_db),
) -> Pengguna:
    """
    Dependency Gate: Mengekstrak user dari JWT Token.
    """
    # Ambil string token dari format "Bearer <token>"
    token = credentials.credentials

    payload = verify_token(token)
    user_id = payload.get("id")

    user = db.query(Pengguna).filter(Pengguna.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User pemilik token tidak ditemukan di database",
        )
    if user.status != "AKTIF":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Akun pengguna sudah dinonaktifkan"
        )
    return user
