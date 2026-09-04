from datetime import datetime
from uuid import UUID
from decimal import Decimal
from typing import Optional, List

from app.schemas.base import BaseSchema
from app.models.akun_perkiraan import HeaderCOA, TingkatAkun

# ==========================================
# HELPER SCHEMAS (Untuk Nested Response)
# ==========================================
class COASimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    header: Optional[HeaderCOA] = None
    tingkat: Optional[TingkatAkun] = None
    status: Optional[str] = None

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
    npwp: Optional[str] = None
    syarat_bayar_default: Optional[str] = "Tunai"

class PelangganCreate(PelangganBase): pass

class PelangganUpdate(BaseSchema):
    nama: Optional[str] = None
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    npwp: Optional[str] = None
    syarat_bayar_default: Optional[str] = None
    status: Optional[str] = None

class PelangganResponse(PelangganBase):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime

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
    npwp: Optional[str] = None
    syarat_bayar_default: Optional[str] = None

class SupplierCreate(SupplierBase): pass

class SupplierUpdate(BaseSchema):
    nama: Optional[str] = None
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    npwp: Optional[str] = None
    syarat_bayar_default: Optional[str] = None
    status: Optional[str] = None

class SupplierResponse(SupplierBase):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime

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
    metode_valuasi: str = "AVERAGE"

class BarangCreate(BarangBase): pass

class BarangUpdate(BaseSchema):
    nama: Optional[str] = None
    kategori_id: Optional[UUID] = None
    satuan_id: Optional[UUID] = None
    harga_pokok: Optional[Decimal] = None
    stok_minimum: Optional[int] = None
    metode_valuasi: Optional[str] = None
    status: Optional[str] = None

class BarangResponse(BarangBase):
    id: UUID
    stok: int
    status: str
    created_at: datetime
    updated_at: datetime
    kategori: KategoriSimpleResponse
    satuan: SatuanSimpleResponse
    # Multi-satuan: daftar satuan tambahan (selain satuan utama)
    daftar_satuan: List['BarangSatuanResponse'] = []


# ==========================================
# 3a. BARANG SATUAN (Multi-satuan)
# ==========================================
class BarangSatuanBase(BaseSchema):
    barang_id: UUID
    satuan_id: UUID
    is_utama: bool = False
    isi_satuan: Optional[int] = 1

class BarangSatuanCreate(BarangSatuanBase):
    pass

class BarangSatuanUpdate(BaseSchema):
    satuan_id: Optional[UUID] = None
    is_utama: Optional[bool] = None
    isi_satuan: Optional[int] = None

class BarangSatuanResponse(BarangSatuanBase):
    id: UUID
    satuan: SatuanSimpleResponse

# ==========================================
# 4. KATEGORI & SATUAN
# ==========================================
class KategoriBarangCreate(BaseSchema):
    kode: str
    nama: str

class KategoriBarangUpdate(BaseSchema):
    kode: Optional[str] = None
    nama: Optional[str] = None
    status: Optional[str] = None

class KategoriBarangResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    status: str

class SatuanCreate(BaseSchema):
    nama: str

class SatuanUpdate(BaseSchema):
    nama: Optional[str] = None
    status: Optional[str] = None

class SatuanResponse(BaseSchema):
    id: UUID
    nama: str
    status: str

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

class KasBankAkunCreate(KasBankAkunBase): pass

class KasBankAkunUpdate(BaseSchema):
    nama: Optional[str] = None
    jenis: Optional[str] = None
    akun_perkiraan_id: Optional[UUID] = None
    status: Optional[str] = None

class KasBankAkunResponse(KasBankAkunBase):
    id: UUID
    saldo: Decimal
    status: str
    created_at: datetime
    updated_at: datetime
    akun_perkiraan: COASimpleResponse
