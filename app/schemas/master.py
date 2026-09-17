from datetime import datetime
from uuid import UUID
from decimal import Decimal
from typing import Optional, List

from app.schemas.base import BaseSchema
from app.models.akun_perkiraan import HeaderCOA, TingkatAkun
from app.models.master.barang import ItemTypeBarang, MetodeValuasi
from app.models.transaksi.aset_tetap.aset_tetap import MetodePenyusutan

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

class SyaratBayarSimpleResponse(BaseSchema):
    id: UUID
    nama: str
    hari: Optional[int] = None

class OrganizationSimpleResponse(BaseSchema):
    id: UUID
    code: str
    name: str
    kind: str

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
    # NEW Phase 2 fields
    nitku: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    tax_status: Optional[str] = None  # PKP / NON_PKP / null
    # FK canonical (prefer pakai ini); legacy syarat_bayar_default string tetap dipertahankan
    syarat_bayar_id: Optional[UUID] = None
    # LEGACY (akan di-drop di Phase berikutnya)
    syarat_bayar_default: Optional[str] = "Tunai"

class PelangganCreate(PelangganBase):
    akun_piutang_id: Optional[UUID] = None  # kalau diisi, skip auto-create COA (link ke COA existing)

class PelangganUpdate(BaseSchema):
    kode: Optional[str] = None  # immutable setelah dipakai transaksi (di-enforce di service)
    nama: Optional[str] = None
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    npwp: Optional[str] = None
    # NEW Phase 2 fields
    nitku: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    tax_status: Optional[str] = None
    syarat_bayar_id: Optional[UUID] = None
    # LEGACY
    syarat_bayar_default: Optional[str] = None
    status: Optional[str] = None

class PelangganResponse(PelangganBase):
    id: UUID
    status: str
    akun_piutang: Optional[COASimpleResponse] = None
    syarat_bayar: Optional[SyaratBayarSimpleResponse] = None  # NEW Phase 2
    created_at: datetime
    updated_at: datetime

# Skenario B2: link COA Piutang existing (sudah di-import manual) ke data Pelanggan
class PelangganFromCoaCreate(BaseSchema):
    coa_id: UUID
    kode: str
    nama: str
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    npwp: Optional[str] = None
    # NEW Phase 2
    nitku: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    tax_status: Optional[str] = None
    syarat_bayar_id: Optional[UUID] = None
    syarat_bayar_default: Optional[str] = "Tunai"

class PelangganCoaResponse(BaseSchema):
    coa_id: UUID
    kode: str
    nama: str
    pelanggan_id: Optional[UUID] = None
    kode_pelanggan: Optional[str] = None
    nama_pelanggan: Optional[str] = None
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    npwp: Optional[str] = None
    nitku: Optional[str] = None  # NEW Phase 2
    syarat_bayar_default: Optional[str] = None
    status: str
    is_linked: bool

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
    # Phase 2 fields
    nitku: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    tax_status: Optional[str] = None  # PKP / NON_PKP / null
    syarat_bayar_id: Optional[UUID] = None
    # LEGACY
    syarat_bayar_default: Optional[str] = None
    # Phase A fields — Catatan Update Supplier Master
    supplier_type: Optional[str] = None  # COMPANY / INDIVIDUAL
    city: Optional[str] = None
    province: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    currency: Optional[str] = 'IDR'
    bank_name: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_account_name: Optional[str] = None

class SupplierCreate(SupplierBase):
    akun_hutang_id: Optional[UUID] = None  # kalau diisi, skip auto-create COA (link ke COA existing)

class SupplierUpdate(BaseSchema):
    kode: Optional[str] = None  # immutable setelah dipakai transaksi
    nama: Optional[str] = None
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    npwp: Optional[str] = None
    # Phase 2
    nitku: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    tax_status: Optional[str] = None
    syarat_bayar_id: Optional[UUID] = None
    # LEGACY
    syarat_bayar_default: Optional[str] = None
    status: Optional[str] = None
    # Phase A fields
    supplier_type: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    currency: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_account_name: Optional[str] = None

class SupplierResponse(SupplierBase):
    id: UUID
    status: str
    akun_hutang: Optional[COASimpleResponse] = None
    syarat_bayar: Optional[SyaratBayarSimpleResponse] = None  # NEW Phase 2
    created_at: datetime
    updated_at: datetime

# Skenario B2: link COA Hutang existing (sudah di-import manual) ke data Supplier
class SupplierFromCoaCreate(BaseSchema):
    coa_id: UUID
    kode: str
    nama: str
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    npwp: Optional[str] = None
    # NEW Phase 2
    nitku: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    tax_status: Optional[str] = None
    syarat_bayar_id: Optional[UUID] = None
    syarat_bayar_default: Optional[str] = "Tunai"

class SupplierCoaResponse(BaseSchema):
    coa_id: UUID
    kode: str
    nama: str
    supplier_id: Optional[UUID] = None
    kode_supplier: Optional[str] = None
    nama_supplier: Optional[str] = None
    alamat: Optional[str] = None
    telepon: Optional[str] = None
    email: Optional[str] = None
    kontak_person: Optional[str] = None
    npwp: Optional[str] = None
    nitku: Optional[str] = None  # NEW Phase 2
    syarat_bayar_default: Optional[str] = None
    status: str
    is_linked: bool

# ==========================================
# 3. BARANG
# ==========================================
class BarangBase(BaseSchema):
    akun_persediaan_id: Optional[UUID] = None
    kode: str
    nama: str
    kategori_id: UUID
    satuan_id: UUID  # base_uom
    harga_pokok: Decimal = 0
    stok_minimum: int = 0
    metode_valuasi: MetodeValuasi = MetodeValuasi.AVERAGE  # inventory_method
    # NEW Phase 2 — P0 fields
    item_type: Optional[ItemTypeBarang] = None  # item_type canonical enum
    akun_hpp_id: Optional[UUID] = None  # COGS_account
    akun_penjualan_id: Optional[UUID] = None  # sales_account (revenue)
    stock_item: bool = True  # stock_item flag (True=stock-tracked, False=non-stock/jasa)
    # LEGACY
    jenis_barang: Optional[str] = None

class BarangCreate(BarangBase): pass

class BarangUpdate(BaseSchema):
    akun_persediaan_id: Optional[UUID] = None
    nama: Optional[str] = None
    kategori_id: Optional[UUID] = None
    satuan_id: Optional[UUID] = None
    harga_pokok: Optional[Decimal] = None
    stok_minimum: Optional[int] = None
    metode_valuasi: Optional[MetodeValuasi] = None
    status: Optional[str] = None
    # NEW Phase 2
    item_type: Optional[ItemTypeBarang] = None
    akun_hpp_id: Optional[UUID] = None
    akun_penjualan_id: Optional[UUID] = None
    stock_item: Optional[bool] = None
    # LEGACY
    jenis_barang: Optional[str] = None

class BarangResponse(BarangBase):
    akun_persediaan: Optional[COASimpleResponse] = None
    akun_hpp: Optional[COASimpleResponse] = None  # NEW Phase 2
    akun_penjualan: Optional[COASimpleResponse] = None  # NEW Phase 2
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
    # NEW Phase 2 — organization scope
    company_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None

class GudangCreate(GudangBase): pass

class GudangUpdate(BaseSchema):
    nama: Optional[str] = None
    alamat: Optional[str] = None
    # NEW Phase 2
    company_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None
    status: Optional[str] = None

class GudangResponse(GudangBase):
    id: UUID
    total_barang: int
    status: str
    company: Optional[OrganizationSimpleResponse] = None  # NEW Phase 2
    branch: Optional[OrganizationSimpleResponse] = None  # NEW Phase 2
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
    # NEW Phase 2 — P0 accounting mapping (semua nullable, diisi saat setup)
    akun_aset_id: Optional[UUID] = None  # asset_cost_account
    akun_akumulasi_id: Optional[UUID] = None  # accumulated_depreciation_account
    akun_beban_id: Optional[UUID] = None  # depreciation_expense_account
    # NEW Phase 2 — default untuk create aset baru
    default_useful_life: Optional[int] = None  # dalam bulan
    default_method: Optional[MetodePenyusutan] = None  # GARIS_LURUS / SALDO_MENURUN

class KategoriAsetCreate(KategoriAsetBase): pass

class KategoriAsetUpdate(BaseSchema):
    nama: Optional[str] = None
    # NEW Phase 2
    akun_aset_id: Optional[UUID] = None
    akun_akumulasi_id: Optional[UUID] = None
    akun_beban_id: Optional[UUID] = None
    default_useful_life: Optional[int] = None
    default_method: Optional[MetodePenyusutan] = None
    status: Optional[str] = None

class KategoriAsetResponse(KategoriAsetBase):
    id: UUID
    status: str
    akun_aset: Optional[COASimpleResponse] = None  # NEW Phase 2
    akun_akumulasi: Optional[COASimpleResponse] = None  # NEW Phase 2
    akun_beban: Optional[COASimpleResponse] = None  # NEW Phase 2
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
    # NEW Phase 2 — currency (P0)
    currency: str = "IDR"  # ISO 4217, default IDR (Phase-1 IDR-only per Roadmap §7)

class KasBankAkunCreate(KasBankAkunBase): pass

class KasBankAkunUpdate(BaseSchema):
    nama: Optional[str] = None
    jenis: Optional[str] = None
    akun_perkiraan_id: Optional[UUID] = None
    currency: Optional[str] = None  # NEW Phase 2
    status: Optional[str] = None

class KasBankAkunResponse(KasBankAkunBase):
    id: UUID
    saldo: Decimal
    status: str
    created_at: datetime
    updated_at: datetime
    akun_perkiraan: COASimpleResponse

# ==========================================
# 9. SETTING AKUN (mapping akun default untuk auto-posting jurnal)
# ==========================================
class SettingAkunUpdate(BaseSchema):
    akun_perkiraan_id: UUID

class SettingAkunResponse(BaseSchema):
    id: UUID
    key: str
    label: str
    akun_perkiraan_id: UUID
    akun_perkiraan: Optional[COASimpleResponse] = None
    created_at: datetime
    updated_at: datetime
