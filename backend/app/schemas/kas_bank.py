"""
Schemas untuk modul Kas & Bank.
PembayaranKas, PenerimaanKas, TransferBank + Detail tabel.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from app.schemas.base import BaseSchema


# ==========================================
# HELPER: Nested response untuk relasi
# ==========================================
class KasBankSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    jenis: str


class AkunPerkiraanSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    nama: str


# ==========================================
# PEMBAYARAN KAS
# ==========================================
class PembayaranRincianBase(BaseSchema):
    akun_perkiraan_id: UUID
    nilai: Decimal = Decimal("0")


class PembayaranRincianCreate(PembayaranRincianBase):
    pass


class PembayaranRincianResponse(PembayaranRincianBase):
    id: UUID
    akun_perkiraan: AkunPerkiraanSimpleResponse


class PembayaranKasBase(BaseSchema):
    tanggal: datetime
    kas_bank_id: UUID
    no_nukti: str
    no_cek: Optional[str] = None
    penerima: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: bool = True


class PembayaranKasCreate(PembayaranKasBase):
    rincian: List[PembayaranRincianCreate]


class PembayaranKasUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    kas_bank_id: Optional[UUID] = None
    no_nukti: Optional[str] = None
    no_cek: Optional[str] = None
    penerima: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class PembayaranKasResponse(PembayaranKasBase):
    id: UUID
    no_bukti: str
    total_nilai: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    kas_bank: Optional[KasBankSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    rincian: List[PembayaranRincianResponse] = []


# ==========================================
# PENERIMAAN KAS
# ==========================================
class PenerimaanRincianBase(BaseSchema):
    akun_perkiraan_id: UUID
    nilai: Decimal = Decimal("0")


class PenerimaanRincianCreate(PenerimaanRincianBase):
    pass


class PenerimaanRincianResponse(PenerimaanRincianBase):
    id: UUID
    akun_perkiraan: AkunPerkiraanSimpleResponse


class PenerimaanKasBase(BaseSchema):
    tanggal: datetime
    kas_bank_id: UUID
    no_nukti: str
    no_cek: Optional[str] = None
    pemberi: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: bool = True


class PenerimaanKasCreate(PenerimaanKasBase):
    rincian: List[PenerimaanRincianCreate]


class PenerimaanKasUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    kas_bank_id: Optional[UUID] = None
    no_nukti: Optional[str] = None
    no_cek: Optional[str] = None
    pemberi: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class PenerimaanKasResponse(PenerimaanKasBase):
    id: UUID
    no_bukti: str
    total_nilai: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    kas_bank: Optional[KasBankSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    rincian: List[PenerimaanRincianResponse] = []


# ==========================================
# TRANSFER BANK
# ==========================================
class TransferBankBase(BaseSchema):
    tanggal: datetime
    dari_kas_bank_id: UUID
    ke_kas_bank_id: UUID
    nilai_transfer: Decimal = Decimal("0")
    biaya_transfer: Decimal = Decimal("0")
    informasi: Optional[str] = None
    auto_post_jurnal: bool = True


class TransferBankCreate(TransferBankBase):
    pass


class TransferBankUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    dari_kas_bank_id: Optional[UUID] = None
    ke_kas_bank_id: Optional[UUID] = None
    nilai_transfer: Optional[Decimal] = None
    biaya_transfer: Optional[Decimal] = None
    informasi: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class TransferBankResponse(TransferBankBase):
    id: UUID
    no_transfer: str
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    dari_kas_bank: Optional[KasBankSimpleResponse] = None
    ke_kas_bank: Optional[KasBankSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
