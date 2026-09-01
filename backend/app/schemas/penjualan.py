"""
Schemas untuk modul Penjualan.
SalesOrder, SalesInvoice, SalesRetur, PengirimanBarang + Detail tabel.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import computed_field

from app.schemas.base import BaseSchema


# ==========================================
# HELPER: Nested response untuk relasi
# ==========================================
class PelangganSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class SyaratBayarSimpleResponse(BaseSchema):
    id: UUID
    nama: str
    hari: Optional[int] = None


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    nama: str


class BarangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    harga_pokok: Decimal


class SatuanSimpleResponse(BaseSchema):
    id: UUID
    nama: str


class SalesOrderSimpleResponse(BaseSchema):
    id: UUID
    no_pesanan: str


class JurnalSimpleResponse(BaseSchema):
    id: UUID
    no_jurnal: str


# ==========================================
# TRANSAKSI BIAYA (shared SO & SINV)
# ==========================================
class TransaksiBiayaBase(BaseSchema):
    nama: str
    jumlah: Decimal = Decimal("0")


class TransaksiBiayaCreate(TransaksiBiayaBase):
    pass


class TransaksiBiayaResponse(TransaksiBiayaBase):
    id: UUID


# ==========================================
# SALES ORDER DETAIL
# ==========================================
class SalesOrderDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    diskon: Optional[Decimal] = Decimal("0")
    sub_total: Decimal = Decimal("0")


class SalesOrderDetailCreate(SalesOrderDetailBase):
    pass


class SalesOrderDetailResponse(SalesOrderDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None


# ==========================================
# SALES ORDER
# ==========================================
class SalesOrderBase(BaseSchema):
    tanggal: datetime
    pelanggan_id: UUID
    syarat_bayar_id: Optional[UUID] = None
    fob: Optional[str] = None
    ekspedisi: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None
    penjual: Optional[str] = None
    alamat_pengiriman: Optional[str] = None
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = True


class SalesOrderCreate(SalesOrderBase):
    details: List[SalesOrderDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []


class SalesOrderUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    pelanggan_id: Optional[UUID] = None
    syarat_bayar_id: Optional[UUID] = None
    fob: Optional[str] = None
    ekspedisi: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None
    penjual: Optional[str] = None
    alamat_pengiriman: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class SalesOrderResponse(SalesOrderBase):
    id: UUID
    no_pesanan: str
    sub_total: Decimal
    total_diskon: Decimal
    total_ppn: Decimal
    total_biaya_tambahan: Decimal
    grand_total: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    pelanggan: Optional[PelangganSimpleResponse] = None
    syarat_bayar: Optional[SyaratBayarSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    details: List[SalesOrderDetailResponse] = []
    biaya_tambahan: List[TransaksiBiayaResponse] = []

    @computed_field  # type: ignore[misc]
    @property
    def dasar_pajak(self) -> Decimal:
        """DPP (Dasar Pengenaan Pajak) = sub_total - total_diskon."""
        return self.sub_total - self.total_diskon


# ==========================================
# SALES INVOICE DETAIL
# ==========================================
class SalesInvoiceDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    diskon: Optional[Decimal] = Decimal("0")
    sub_total: Decimal = Decimal("0")


class SalesInvoiceDetailCreate(SalesInvoiceDetailBase):
    pass


class SalesInvoiceDetailResponse(SalesInvoiceDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None


# ==========================================
# SALES INVOICE
# ==========================================
class SalesInvoiceBase(BaseSchema):
    tanggal: datetime
    pelanggan_id: UUID
    syarat_bayar_id: Optional[UUID] = None
    sales_order_id: Optional[UUID] = None
    fob: Optional[str] = None
    ekspedisi: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None
    alamat_pengiriman: Optional[str] = None
    mata_uang: str = "IDR"
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = True


class SalesInvoiceCreate(SalesInvoiceBase):
    details: List[SalesInvoiceDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []


class SalesInvoiceUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    pelanggan_id: Optional[UUID] = None
    syarat_bayar_id: Optional[UUID] = None
    sales_order_id: Optional[UUID] = None
    fob: Optional[str] = None
    ekspedisi: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None
    alamat_pengiriman: Optional[str] = None
    mata_uang: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class SalesInvoiceResponse(SalesInvoiceBase):
    id: UUID
    no_invoice: str
    sub_total: Decimal
    total_diskon: Decimal
    total_ppn: Decimal
    total_biaya_tambahan: Decimal
    grand_total: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    pelanggan: Optional[PelangganSimpleResponse] = None
    syarat_bayar: Optional[SyaratBayarSimpleResponse] = None
    sales_order: Optional[SalesOrderSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    details: List[SalesInvoiceDetailResponse] = []
    biaya_tambahan: List[TransaksiBiayaResponse] = []

    @computed_field  # type: ignore[misc]
    @property
    def dasar_pajak(self) -> Decimal:
        """DPP (Dasar Pengenaan Pajak) = sub_total - total_diskon."""
        return self.sub_total - self.total_diskon


# ==========================================
# SALES RETUR DETAIL
# ==========================================
class SalesReturDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    sub_total: Decimal = Decimal("0")


class SalesReturDetailCreate(SalesReturDetailBase):
    pass


class SalesReturDetailResponse(SalesReturDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None


# ==========================================
# SALES RETUR
# ==========================================
class SalesReturBase(BaseSchema):
    tanggal: datetime
    sales_invoice_id: UUID
    pelanggan_id: UUID
    alamat_pengembalian: Optional[str] = None
    no_pengembalian: Optional[str] = None
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = True


class SalesReturCreate(SalesReturBase):
    details: List[SalesReturDetailCreate]


class SalesReturUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    sales_invoice_id: Optional[UUID] = None
    pelanggan_id: Optional[UUID] = None
    alamat_pengembalian: Optional[str] = None
    no_pengembalian: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class SalesReturResponse(SalesReturBase):
    id: UUID
    no_retur: str
    sub_total: Decimal
    total_ppn: Decimal
    grand_total: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    sales_invoice: Optional[SalesOrderSimpleResponse] = None
    pelanggan: Optional[PelangganSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    details: List[SalesReturDetailResponse] = []

    @computed_field  # type: ignore[misc]
    @property
    def dasar_pajak(self) -> Decimal:
        """DPP (Dasar Pengenaan Pajak) = sub_total (retur tanpa diskon)."""
        return self.sub_total


# ==========================================
# PENGIRIMAN BARANG DETAIL
# ==========================================
class PengirimanBarangDetailBase(BaseSchema):
    barang_id: UUID
    qty: int = 0
    satuan_id: UUID


class PengirimanBarangDetailCreate(PengirimanBarangDetailBase):
    pass


class PengirimanBarangDetailResponse(PengirimanBarangDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None
    satuan: Optional[SatuanSimpleResponse] = None


# ==========================================
# PENGIRIMAN BARANG
# ==========================================
class PengirimanBarangBase(BaseSchema):
    tanggal: datetime
    sales_order_id: UUID
    pelanggan_id: UUID
    ekspedisi: Optional[str] = None
    alamat_pengiriman: Optional[str] = None
    keterangan: Optional[str] = None


class PengirimanBarangCreate(PengirimanBarangBase):
    details: List[PengirimanBarangDetailCreate]


class PengirimanBarangUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    sales_order_id: Optional[UUID] = None
    pelanggan_id: Optional[UUID] = None
    ekspedisi: Optional[str] = None
    alamat_pengiriman: Optional[str] = None
    keterangan: Optional[str] = None


class PengirimanBarangResponse(PengirimanBarangBase):
    id: UUID
    no_surat_jalan: str
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    sales_order: Optional[SalesOrderSimpleResponse] = None
    pelanggan: Optional[PelangganSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    details: List[PengirimanBarangDetailResponse] = []
