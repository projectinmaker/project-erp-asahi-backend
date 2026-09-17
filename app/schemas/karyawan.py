from datetime import datetime
from uuid import UUID
from typing import Optional

from app.schemas.base import BaseSchema
from app.schemas.master import COASimpleResponse
from app.models.master.karyawan import Departemen


# ==========================================
# KARYAWAN SCHEMAS
# ==========================================
class KaryawanBase(BaseSchema):
    nik: str
    nama: str
    jabatan: Optional[str] = None
    departemen: Optional[Departemen] = None
    email: Optional[str] = None
    no_hp: Optional[str] = None
    akun_piutang_id: UUID


class KaryawanCreate(KaryawanBase):
    pass


class KaryawanUpdate(BaseSchema):
    nama: Optional[str] = None
    jabatan: Optional[str] = None
    departemen: Optional[Departemen] = None
    email: Optional[str] = None
    no_hp: Optional[str] = None
    akun_piutang_id: Optional[UUID] = None
    status: Optional[str] = None


class KaryawanResponse(KaryawanBase):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    akun_piutang: COASimpleResponse
