from datetime import datetime
from uuid import UUID
from decimal import Decimal
from typing import Optional, List

from app.schemas.base import BaseSchema

# ==========================================
# HELPER SCHEMAS (Untuk Nested Response)
# ==========================================
class COASimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str

class KategoriSimpleResponse(BaseSchema):
    id: UUID
    nama: str

class SatuanSimpleResponse(BaseSchema):
    id: UUID
    nama: str

# ==========================================
# 1. PELANGGAN
# ==========================================
class PelangganBase(BaseSchema):
    kode: str
    nama: str
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    akun_piutang_id: UUID
    syarat_bayar_default: Optional[str] = "Tunai"

class PelangganCreate(PelangganBase): pass

class PelangganUpdate(BaseSchema):
    nama: Optional[str] = None
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    akun_piutang_id: Optional[UUID] = None
    syarat_bayar_default: Optional[str] = None
    status: Optional[str] = None

class PelangganResponse(PelangganBase):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    akun_piutang: COASimpleResponse  # <-- DITAMBAHKAN

# ==========================================
# 2. SUPPLIER
# ==========================================
class SupplierBase(BaseSchema):
    kode: str
    nama: str
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    akun_hutang_id: UUID
    syarat_bayar_default: Optional[str] = None

class SupplierCreate(SupplierBase): pass

class SupplierUpdate(BaseSchema):
    nama: Optional[str] = None
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    akun_hutang_id: Optional[UUID] = None
    syarat_bayar_default: Optional[str] = None
    status: Optional[str] = None

class SupplierResponse(SupplierBase):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    akun_hutang: COASimpleResponse  # <-- DITAMBAHKAN

# ==========================================
# 3. BARANG
# ==========================================
class BarangBase(BaseSchema):
    kode: str
    nama: str
    kategori_id: UUID
    satuan_id: UUID
    harga_pokok: Decimal = 0
    stok_minimum: int = 0

class BarangCreate(BarangBase): pass

class BarangUpdate(BaseSchema):
    nama: Optional[str] = None
    kategori_id: Optional[UUID] = None
    satuan_id: Optional[UUID] = None
    harga_pokok: Optional[Decimal] = None
    stok_minimum: Optional[int] = None
    status: Optional[str] = None

class BarangResponse(BarangBase):
    id: UUID
    stok: int
    status: str
    created_at: datetime
    updated_at: datetime
    kategori: KategoriSimpleResponse  # <-- DITAMBAHKAN
    satuan: SatuanSimpleResponse      # <-- DITAMBAHKAN

# ==========================================
# 4. KATEGORI & SATUAN (BARU)
# ==========================================
class KategoriBarangResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    status: str

class SatuanResponse(BaseSchema):
    id: UUID
    nama: str