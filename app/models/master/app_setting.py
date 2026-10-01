import enum

from sqlalchemy import Column, String

from app.database import BaseModel
from app.models.base import BaseMixin


class AppSettingKey(str, enum.Enum):
    """Kunci pengaturan aplikasi global (non-COA) yang dikenal sistem."""

    METODE_VALUASI = "METODE_VALUASI"


class AppSetting(BaseModel, BaseMixin):
    """Pengaturan aplikasi global key-value (non-COA).

    Menampung setting yang bukan mapping akun — contoh: METODE_VALUASI
    (metode valuasi persediaan global, dipindahkan dari per-barang ke
    halaman Setting Akun).
    """

    __tablename__ = "app_setting"

    key = Column(String(100), unique=True, index=True, nullable=False)
    value = Column(String(200), nullable=False)
