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
    akun_piutang: COASimpleResponse

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
    akun_hutang: COASimpleResponse

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
    kategori: KategoriSimpleResponse
    satuan: SatuanSimpleResponse

# ==========================================
# 4. KATEGORI & SATUAN
# ==========================================
class KategoriBarangResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    status: str

class SatuanResponse(BaseSchema):
    id: UUID
    nama: str

# ==========================================
# 5. GUDANG
# ==========================================
class GudangBase(BaseSchema):
    kode: str
    nama: str
    alamat: Optional[str] = None

class GudangCreate(GudangBase): pass

class GudangUpdate(BaseSchema):
    nama: Optional[str] = None
    alamat: Optional[str] = None
    status: Optional[str] = None

class GudangResponse(GudangBase):
    id: UUID
    total_barang: int
    status: str
    created_at: datetime
    updated_at: datetime

# ==========================================
# 6. SYARAT BAYAR
# ==========================================
class SyaratBayarBase(BaseSchema):
    nama: str
    hari: Optional[int] = None

class SyaratBayarCreate(SyaratBayarBase): pass

class SyaratBayarUpdate(BaseSchema):
    nama: Optional[str] = None
    hari: Optional[int] = None

class SyaratBayarResponse(SyaratBayarBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

# ==========================================
# 7. KATEGORI ASET
# ==========================================
class KategoriAsetBase(BaseSchema):
    kode: str
    nama: str

class KategoriAsetCreate(KategoriAsetBase): pass

class KategoriAsetUpdate(BaseSchema):
    nama: Optional[str] = None
    status: Optional[str] = None

class KategoriAsetResponse(KategoriAsetBase):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime

# ==========================================
# 8. KAS BANK AKUN
# ==========================================
class KasBankAkunBase(BaseSchema):
    kode: str
    nama: str
    jenis: str  # KAS / BANK
    akun_perkiraan_id: UUID
    saldo: Decimal = Decimal("0")

class KasBankAkunCreate(KasBankAkunBase): pass

class KasBankAkunUpdate(BaseSchema):
    nama: Optional[str] = None
    jenis: Optional[str] = None
    akun_perkiraan_id: Optional[UUID] = None
    status: Optional[str] = None

class KasBankAkunResponse(KasBankAkunBase):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    akun_perkiraan: COASimpleResponse
