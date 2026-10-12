"""
Schemas untuk modul Kas & Bank.
PembayaranKas, PenerimaanKas, TransferBank + Detail tabel.
FIX v2: Tambah JurnalSimpleResponse + relasi jurnal di 3 Response class.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from app.schemas.base import BaseSchema
from app.schemas.settlement import AllocationResponse


# ==========================================
# HELPER: Nested response untuk relasi
# ==========================================
class AkunPerkiraanSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    username: str
    nama_lengkap: str


# FIX v2: Definisikan JurnalSimpleResponse
class JurnalSimpleResponse(BaseSchema):
    id: UUID
    no_jurnal: str


class KasBankSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    jenis: str
    akun_perkiraan: AkunPerkiraanSimpleResponse


# === Update cetak "Bayar Pemasok" — blok Pemasok (nama + alamat) di print ===
class SupplierSimpleResponse(BaseSchema):
    id: UUID
    nama: str
    alamat: Optional[str] = None


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
    # === Update cetak "Bayar Pemasok" — info cek & mata uang ===
    tanggal_cek: Optional[date] = None
    jumlah_cek: Optional[Decimal] = None
    mata_uang: str = "IDR"
    nilai_tukar: Decimal = Decimal("1")
    penerima: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: bool = False


class PembayaranKasCreate(PembayaranKasBase):
    rincian: List[PembayaranRincianCreate]


class PembayaranKasUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    kas_bank_id: Optional[UUID] = None
    no_nukti: Optional[str] = None
    no_cek: Optional[str] = None
    # === Update cetak "Bayar Pemasok" — info cek & mata uang (editable) ===
    tanggal_cek: Optional[date] = None
    jumlah_cek: Optional[Decimal] = None
    mata_uang: Optional[str] = None
    nilai_tukar: Optional[Decimal] = None
    penerima: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class PembayaranKasResponse(PembayaranKasBase):
    supplier_id: Optional[UUID] = None
    alokasi: List[AllocationResponse] = []
    # === Update cetak "Bayar Pemasok" — relasi supplier untuk blok Pemasok ===
    supplier: Optional[SupplierSimpleResponse] = None
    id: UUID
    no_bukti: str
    total_nilai: Decimal
    penalti: Decimal = Decimal("0")  # Update #5
    akun_penalti_id: UUID | None = None  # Update #5
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    kas_bank: Optional[KasBankSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    rincian: List[PembayaranRincianResponse] = []
    jurnal: Optional[JurnalSimpleResponse] = None


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
    auto_post_jurnal: bool = False


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
    pelanggan_id: Optional[UUID] = None
    alokasi: List[AllocationResponse] = []
    id: UUID
    no_bukti: str
    total_nilai: Decimal
    penalti: Decimal = Decimal("0")  # Update #5
    akun_penalti_id: UUID | None = None  # Update #5
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    kas_bank: Optional[KasBankSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    rincian: List[PenerimaanRincianResponse] = []
    jurnal: Optional[JurnalSimpleResponse] = None


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
    auto_post_jurnal: bool = False


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
    jurnal: Optional[JurnalSimpleResponse] = None