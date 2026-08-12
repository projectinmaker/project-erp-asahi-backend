from datetime import datetime
from uuid import UUID
from decimal import Decimal
from typing import Optional

from app.models.akun_perkiraan import HeaderCOA, SaldoNormal, TingkatAkun
from app.schemas.base import BaseSchema


class COABase(BaseSchema):
    kode: str
    nama: str
    header: HeaderCOA
    tingkat: TingkatAkun
    induk_id: Optional[UUID] = None
    induk_kode: Optional[str] = None
    saldo_normal: SaldoNormal
    status: str = "AKTIF"


class COACreate(COABase):
    """Schema untuk membuat COA baru (tidak butuh ID dan Timestamp)"""

    pass


class COAUpdate(BaseSchema):
    """Schema untuk update COA (semua field opsional)"""

    nama: Optional[str] = None
    induk_id: Optional[UUID] = None
    induk_kode: Optional[str] = None
    saldo: Optional[Decimal] = None
    status: Optional[str] = None


class COAResponse(COABase):
    """Schema untuk response API ke Frontend"""

    id: UUID
    saldo: Decimal
    created_at: datetime
    updated_at: datetime

    class Config:
        # Alias spesifik jika ada penamaan yang tidak standar
        json_schema_extra = {
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "kode": "100000001",
                "nama": "Kas Kecil",
                "header": "AKTIVA",
                "tingkat": "DETAIL",
                "indukId": "ec35cd2a-0bc9-4dd7-bded-c2930e895c41",
                "indukKode": "100000000",
                "saldoNormal": "DEBIT",
                "saldo": 5000000,
                "status": "AKTIF",
                "createdAt": "2026-08-11T16:00:00.000000",
                "updatedAt": "2026-08-11T16:00:00.000000",
            }
        }
