"""
Schemas untuk modul Persediaan.
PenyesuaianStok, PemindahanBarang, PermintaanBarang.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from app.schemas.base import BaseSchema


# ==========================================
# HELPER: Nested response untuk relasi
# ==========================================
class BarangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    harga_pokok: Decimal


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    nama: str


class GudangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class JurnalSimpleResponse(BaseSchema):
    id: UUID
    no_jurnal: str


# ==========================================
# PENYESUAIAN STOK
# ==========================================
class PenyesuaianStokBase(BaseSchema):
    tanggal: datetime
    barang_id: UUID
    tipe: str  # TAMBAH / KURANG
    qty: int = 0
    biaya_satuan: Decimal = Decimal("0")
    alasan: Optional[str] = None
    auto_post_jurnal: bool = True


class PenyesuaianStokCreate(PenyesuaianStokBase):
    pass


class PenyesuaianStokUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    barang_id: Optional[UUID] = None
    tipe: Optional[str] = None
    qty: Optional[int] = None
    biaya_satuan: Optional[Decimal] = None
    alasan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class PenyesuaianStokResponse(PenyesuaianStokBase):
    id: UUID
    no_adj: str
    total: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    barang: Optional[BarangSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None


# ==========================================
# PEMINDAHAN BARANG
# ==========================================
class PemindahanBarangBase(BaseSchema):
    tanggal: datetime
    proses: str  # KIRIM / TERIMA
    dari_gudang_id: UUID
    ke_gudang_id: UUID
    barang_id: UUID
    qty: int = 0
    auto_post_jurnal: bool = False
    keterangan: Optional[str] = None


class PemindahanBarangCreate(PemindahanBarangBase):
    pass


class PemindahanBarangUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    proses: Optional[str] = None
    dari_gudang_id: Optional[UUID] = None
    ke_gudang_id: Optional[UUID] = None
    barang_id: Optional[UUID] = None
    qty: Optional[int] = None
    auto_post_jurnal: Optional[bool] = None
    keterangan: Optional[str] = None


class PemindahanBarangResponse(PemindahanBarangBase):
    id: UUID
    no_pemindahan: str
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    dari_gudang: Optional[GudangSimpleResponse] = None
    ke_gudang: Optional[GudangSimpleResponse] = None
    barang: Optional[BarangSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None


# ==========================================
# PERMINTAAN BARANG
# ==========================================
class PermintaanBarangBase(BaseSchema):
    tanggal: datetime
    barang_id: UUID
    qty: int = 0
    diajukan_oleh: str
    keterangan: Optional[str] = None


class PermintaanBarangCreate(PermintaanBarangBase):
    pass


class PermintaanBarangUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    barang_id: Optional[UUID] = None
    qty: Optional[int] = None
    diajukan_oleh: Optional[str] = None
    keterangan: Optional[str] = None


class PermintaanBarangResponse(PermintaanBarangBase):
    id: UUID
    no_permintaan: str
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    barang: Optional[BarangSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
