"""
Schemas untuk modul Rekonsiliasi Bank.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from app.schemas.base import BaseSchema


class KasBankSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    jenis: str


class AkunPerkiraanSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class JurnalSimpleResponse(BaseSchema):
    id: UUID
    no_jurnal: str


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    nama: str


# ==========================================
# DETAIL
# ==========================================

class RekonsiliasiDetailResponse(BaseSchema):
    id: UUID
    tipe: str
    keterangan: str
    jumlah: Decimal
    sisi: str
    akun_perkiraan_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    akun_perkiraan: Optional[AkunPerkiraanSimpleResponse] = None


class RekonsiliasiDetailCreate(BaseSchema):
    tipe: str  # MEMO or PENYESUAIAN
    keterangan: str
    jumlah: Decimal
    sisi: str  # DEBIT or KREDIT
    akun_perkiraan_id: Optional[UUID] = None


class RekonsiliasiDetailUpdate(BaseSchema):
    keterangan: Optional[str] = None
    jumlah: Optional[Decimal] = None
    sisi: Optional[str] = None
    akun_perkiraan_id: Optional[UUID] = None


# ==========================================
# HEADER
# ==========================================

class RekonsiliasiBankResponse(BaseSchema):
    id: UUID
    kas_bank_akun_id: UUID
    tanggal_akhir: datetime
    saldo_bank: Decimal
    saldo_buku: Decimal
    selisih: Decimal
    status: str
    keterangan: Optional[str] = None
    jurnal_penyesuaian_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    kas_bank: Optional[KasBankSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    details: List[RekonsiliasiDetailResponse] = []


class RekonsiliasiBankCreate(BaseSchema):
    kas_bank_akun_id: UUID
    tanggal_akhir: datetime
    saldo_bank: Decimal
    keterangan: Optional[str] = None


class RekonsiliasiBankUpdate(BaseSchema):
    saldo_bank: Optional[Decimal] = None
    keterangan: Optional[str] = None


class SaldoBukuPreviewResponse(BaseSchema):
    kas_bank_akun_id: UUID
    kas_bank_nama: str
    tanggal_akhir: datetime
    saldo_buku: Decimal
