"""
Schemas untuk modul Aset Tetap.
AsetTetap — single entity (no detail table).
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.schemas.base import BaseSchema


class KategoriAsetSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class AkunPerkiraanSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    nama: str


class AsetTetapBase(BaseSchema):
    kode: str
    nama: str
    kategori_aset_id: UUID
    akun_aset_id: UUID
    akun_akumulasi_id: UUID
    akun_beban_id: UUID
    kuantitas: int = 1
    nilai_perolehan: Decimal = Decimal("0")
    tanggal_mulai: datetime
    catatan: Optional[str] = None
    auto_post_jurnal: bool = True


class AsetTetapCreate(AsetTetapBase):
    pass


class AsetTetapUpdate(BaseSchema):
    kode: Optional[str] = None
    nama: Optional[str] = None
    kategori_aset_id: Optional[UUID] = None
    akun_aset_id: Optional[UUID] = None
    akun_akumulasi_id: Optional[UUID] = None
    akun_beban_id: Optional[UUID] = None
    kuantitas: Optional[int] = None
    nilai_perolehan: Optional[Decimal] = None
    tanggal_mulai: Optional[datetime] = None
    catatan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class AsetTetapResponse(AsetTetapBase):
    id: UUID
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    kategori_aset: Optional[KategoriAsetSimpleResponse] = None
    akun_aset: Optional[AkunPerkiraanSimpleResponse] = None
    akun_akumulasi: Optional[AkunPerkiraanSimpleResponse] = None
    akun_beban: Optional[AkunPerkiraanSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
