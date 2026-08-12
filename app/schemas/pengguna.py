from datetime import datetime
from uuid import UUID
from typing import Optional

from app.models.master.pengguna import RolePengguna
from app.schemas.base import BaseSchema


class PenggunaCreate(BaseSchema):
    username: str
    nama_lengkap: str
    email: str
    password: str
    role: RolePengguna = RolePengguna.ADMINISTRATOR


class PenggunaResponse(BaseSchema):
    id: UUID
    username: str
    nama_lengkap: str
    email: str
    role: RolePengguna
    status: str
    created_at: datetime


# ... (biarkan kode yang lama tetap ada di atas) ...

from pydantic import Field


class LoginRequest(BaseSchema):
    """Schema untuk menerima input login dari Frontend"""

    username: str = Field(..., min_length=3, description="Username pengguna")
    password: str = Field(..., min_length=3, description="Password pengguna")


class TokenResponse(BaseSchema):
    """Schema untuk mengembalikan Token ke Frontend"""

    access_token: str
    token_type: str = "bearer"
