from datetime import datetime
from uuid import UUID
from decimal import Decimal
from typing import Optional, List

from pydantic import Field

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
    # Induk untuk auto-create COA subledger piutang. Default: akun root dari
    # setting_akun PIUTANG_USAHA (mis. 112000) — cocok untuk COA v2 di mana
    # root ber-level DETAIL. Dipakai form "Pilih Akun Perkiraan".
    # exclude=True: field instruksi (bukan kolom tabel) — tidak ikut model_dump
    # sehingga create_master tidak mencoba set kolom yang tidak ada.
    akun_piutang_parent_id: Optional[UUID] = Field(default=None, exclude=True)

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
    # coa_id/kode/nama bisa None untuk baris "pelanggan tanpa akun piutang"
    # (belum ter-link ke COA mana pun) — tetap ditampilkan di master pelanggan.
    coa_id: Optional[UUID] = None
    kode: Optional[str] = None
    nama: Optional[str] = None
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
    # Induk untuk auto-create COA subledger hutang (mirror pelanggan).
    # exclude=True: field instruksi (bukan kolom tabel).
    akun_hutang_parent_id: Optional[UUID] = Field(default=None, exclude=True)

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
    # coa_id/kode/nama bisa None untuk baris "supplier tanpa akun hutang"
    # (belum ter-link ke COA mana pun) — tetap ditampilkan di master supplier.
    coa_id: Optional[UUID] = None
    kode: Optional[str] = None
    nama: Optional[str] = None
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
    harga_jual: Decimal = 0  # harga jual default (bukan HPP; HPP mengikuti valuasi stok)
    stok_minimum: int = 0
    metode_valuasi: MetodeValuasi = MetodeValuasi.AVERAGE  # inventory_method
    # NEW Phase 2 — P0 fields
    item_type: Optional[ItemTypeBarang] = None  # item_type canonical enum
    akun_hpp_id: Optional[UUID] = None  # COGS_account
    akun_penjualan_id: Optional[UUID] = None  # sales_account (revenue)
    akun_retur_penjualan_id: Optional[UUID] = None  # sales_return_account (contra-revenue)
    akun_diskon_penjualan_id: Optional[UUID] = None  # sales_discount_account (contra-revenue)
    stock_item: bool = True  # stock_item flag (True=stock-tracked, False=non-stock/jasa)
    # LEGACY
    jenis_barang: Optional[str] = None

class BarangCreate(BarangBase):
    # Task 27-c — dynamic form barang: status opsional saat create
    # (default kolom tetap 'AKTIF' di server). Nilai divalidasi di
    # master_service.apply_barang_item_type_policy (hanya AKTIF/NONAKTIF).
    status: Optional[str] = None

class BarangUpdate(BaseSchema):
    akun_persediaan_id: Optional[UUID] = None
    nama: Optional[str] = None
    kategori_id: Optional[UUID] = None
    satuan_id: Optional[UUID] = None
    harga_pokok: Optional[Decimal] = None
    harga_jual: Optional[Decimal] = None
    stok_minimum: Optional[int] = None
    metode_valuasi: Optional[MetodeValuasi] = None
    status: Optional[str] = None
    # NEW Phase 2
    item_type: Optional[ItemTypeBarang] = None
    akun_hpp_id: Optional[UUID] = None
    akun_penjualan_id: Optional[UUID] = None
    akun_retur_penjualan_id: Optional[UUID] = None
    akun_diskon_penjualan_id: Optional[UUID] = None
    stock_item: Optional[bool] = None
    # LEGACY
    jenis_barang: Optional[str] = None

class BarangResponse(BarangBase):
    akun_persediaan: Optional[COASimpleResponse] = None
    akun_hpp: Optional[COASimpleResponse] = None  # NEW Phase 2
    akun_penjualan: Optional[COASimpleResponse] = None  # NEW Phase 2
    akun_retur_penjualan: Optional[COASimpleResponse] = None
    akun_diskon_penjualan: Optional[COASimpleResponse] = None
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


# ==========================================
# SETTING APLIKASI GLOBAL (non-COA) — app_setting
# ==========================================
class AppSettingUpdate(BaseSchema):
    value: str

class AppSettingResponse(BaseSchema):
    key: str
    value: str
    updated_at: Optional[datetime] = None


# ==========================================
# IMPORT EXCEL (Update #5) — hasil ringkasan import master
# ==========================================
class ImportRowError(BaseSchema):
    baris: int  # nomor baris di sheet Excel (baris 1 = header)
    pesan: str

class ImportResult(BaseSchema):
    """Hasil import Excel master (barang/pelanggan/supplier).

    Error per baris tidak menghentikan baris lain — seluruh baris valid
    tetap diproses; ringkasan dikembalikan dengan 200.
    """
    total_baris: int
    sukses: int
    gagal: int
    errors: list[ImportRowError] = []


# ==========================================
# REKENING BANK (cetak Invoice Penjualan) — update ASAHI #6
# Didefinisikan sebelum CompanyProfileResponse karena di-embed ke sana.
# ==========================================
class RekeningBankBase(BaseSchema):
    nama_bank: str = Field(min_length=1, max_length=200)
    no_rekening: str = Field(min_length=1, max_length=100)
    # Kode mata uang (IDR/USD/...), divalidasi ke master mata uang.
    mata_uang: str = Field(default="IDR", min_length=2, max_length=8)
    is_aktif: bool = True


class RekeningBankCreate(RekeningBankBase):
    pass


class RekeningBankUpdate(BaseSchema):
    nama_bank: Optional[str] = Field(default=None, min_length=1, max_length=200)
    no_rekening: Optional[str] = Field(default=None, min_length=1, max_length=100)
    mata_uang: Optional[str] = Field(default=None, min_length=2, max_length=8)
    is_aktif: Optional[bool] = None


class RekeningBankResponse(RekeningBankBase):
    id: UUID
    updated_at: Optional[datetime] = None


# ==========================================
# PROFIL PERUSAHAAN (header cetak/PDF) — update ASAHI
# ==========================================
class CompanyProfileResponse(BaseSchema):
    """Profil perusahaan untuk header cetak/PDF (tabel satu baris)."""
    id: UUID
    nama_perusahaan: str
    alamat: str
    telepon: Optional[str] = None
    email: Optional[str] = None
    # Logo sebagai data URL (data:image/...;base64,...) — langsung dipakai
    # <img> di template cetak.
    logo: Optional[str] = None
    # Update ASAHI #6: slogan tampil khusus di header cetak Invoice Penjualan.
    slogan: Optional[str] = None
    # Update ASAHI #6: rekening bank AKTIF — tampil di bawah Keterangan pada
    # cetak Invoice Penjualan (di-embed agar frontend cukup satu fetch).
    rekening_bank: List[RekeningBankResponse] = []
    updated_at: Optional[datetime] = None


class CompanyProfileUpdate(BaseSchema):
    nama_perusahaan: str = Field(min_length=1, max_length=200)
    alamat: str = Field(min_length=1)
    telepon: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=100)
    logo: Optional[str] = None
    # Update ASAHI #6: slogan opsional (kosong/null = tidak tampil di invoice).
    slogan: Optional[str] = None


# ==========================================
# MATA UANG (dropdown Currency SO/PO) — update ASAHI #3
# ==========================================
class MataUangBase(BaseSchema):
    kode: str = Field(min_length=2, max_length=8)
    nama: str = Field(min_length=1, max_length=100)
    is_aktif: bool = True


class MataUangCreate(MataUangBase):
    pass


class MataUangUpdate(BaseSchema):
    kode: Optional[str] = Field(default=None, min_length=2, max_length=8)
    nama: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_aktif: Optional[bool] = None


class MataUangResponse(MataUangBase):
    id: UUID
    updated_at: Optional[datetime] = None


# ==========================================
# ALAMAT PENGIRIMAN (gudang tujuan PO) — update ASAHI #3
# ==========================================
class AlamatPengirimanBase(BaseSchema):
    prefix: str = Field(min_length=1, max_length=200)
    nama: str = Field(min_length=1, max_length=200)
    is_aktif: bool = True


class AlamatPengirimanCreate(AlamatPengirimanBase):
    pass


class AlamatPengirimanUpdate(BaseSchema):
    prefix: Optional[str] = Field(default=None, min_length=1, max_length=200)
    nama: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_aktif: Optional[bool] = None


class AlamatPengirimanResponse(AlamatPengirimanBase):
    id: UUID
    updated_at: Optional[datetime] = None
