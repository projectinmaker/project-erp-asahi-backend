import enum
from sqlalchemy import Column, String, DateTime, Enum as SQLEnum
from app.database import BaseModel
from app.models.base import BaseMixin


class RolePengguna(str, enum.Enum):
    ADMINISTRATOR = "ADMINISTRATOR"
    MANAJER_KEUANGAN = "MANAJER_KEUANGAN"
    STAFF_AKUNTANSI = "STAFF_AKUNTANSI"
    STAFF_GUDANG = "STAFF_GUDANG"
    STAFF_PENJUALAN = "STAFF_PENJUALAN"


class Pengguna(BaseModel, BaseMixin):
    __tablename__ = "pengguna"
    username = Column(String(50), unique=True, nullable=False, index=True)
    nama_lengkap = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(SQLEnum(RolePengguna), nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)
    terakhir_login = Column(DateTime(timezone=True), nullable=True)
