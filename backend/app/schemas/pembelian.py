"""
Schemas untuk modul Pembelian.
PurchaseOrder, PurchaseInvoice, PurchaseRetur, PenerimaanBarang + Detail tabel.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from app.schemas.base import BaseSchema


# ==========================================
# HELPER: Nested response untuk relasi
# ==========================================
class SupplierSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


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


class PurchaseOrderSimpleResponse(BaseSchema):
    id: UUID
    no_pesanan: str


class JurnalSimpleResponse(BaseSchema):
    id: UUID
    no_jurnal: str


# ==========================================
# TRANSAKSI BIAYA (shared PO & PINV — reuse dari penjualan jika sudah ada)
# ==========================================
class TransaksiBiayaBase(BaseSchema):
    nama: str
    jumlah: Decimal = Decimal("0")


class TransaksiBiayaCreate(TransaksiBiayaBase):
    pass


class TransaksiBiayaResponse(TransaksiBiayaBase):
    id: UUID


# ==========================================
# PURCHASE ORDER DETAIL
# ==========================================
class PurchaseOrderDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    diskon: Optional[Decimal] = Decimal("0")
    sub_total: Decimal = Decimal("0")


class PurchaseOrderDetailCreate(PurchaseOrderDetailBase):
    pass


class PurchaseOrderDetailResponse(PurchaseOrderDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None


# ==========================================
# PURCHASE ORDER
# ==========================================
class PurchaseOrderBase(BaseSchema):
    tanggal: datetime
    supplier_id: UUID
    tanggal_kirim: Optional[datetime] = None
    alamat: Optional[str] = None
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = True


class PurchaseOrderCreate(PurchaseOrderBase):
    details: List[PurchaseOrderDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []


class PurchaseOrderUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    supplier_id: Optional[UUID] = None
    tanggal_kirim: Optional[datetime] = None
    alamat: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class PurchaseOrderResponse(PurchaseOrderBase):
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
    supplier: Optional[SupplierSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    details: List[PurchaseOrderDetailResponse] = []
    biaya_tambahan: List[TransaksiBiayaResponse] = []


# ==========================================
# PURCHASE INVOICE DETAIL
# ==========================================
class PurchaseInvoiceDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    diskon: Optional[Decimal] = Decimal("0")
    sub_total: Decimal = Decimal("0")


class PurchaseInvoiceDetailCreate(PurchaseInvoiceDetailBase):
    pass


class PurchaseInvoiceDetailResponse(PurchaseInvoiceDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None


# ==========================================
# PURCHASE INVOICE
# ==========================================
class PurchaseInvoiceBase(BaseSchema):
    tanggal: datetime
    supplier_id: UUID
    no_faktur: str
    alamat: Optional[str] = None
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = True


class PurchaseInvoiceCreate(PurchaseInvoiceBase):
    details: List[PurchaseInvoiceDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []


class PurchaseInvoiceUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    supplier_id: Optional[UUID] = None
    no_faktur: Optional[str] = None
    alamat: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class PurchaseInvoiceResponse(PurchaseInvoiceBase):
    id: UUID
    no_form: str
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
    supplier: Optional[SupplierSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    details: List[PurchaseInvoiceDetailResponse] = []
    biaya_tambahan: List[TransaksiBiayaResponse] = []


# ==========================================
# PURCHASE RETUR DETAIL
# ==========================================
class PurchaseReturDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    sub_total: Decimal = Decimal("0")


class PurchaseReturDetailCreate(PurchaseReturDetailBase):
    pass


class PurchaseReturDetailResponse(PurchaseReturDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None


# ==========================================
# PURCHASE RETUR
# ==========================================
class PurchaseReturBase(BaseSchema):
    tanggal: datetime
    purchase_order_id: UUID
    supplier_id: UUID
    alamat: Optional[str] = None
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = True


class PurchaseReturCreate(PurchaseReturBase):
    details: List[PurchaseReturDetailCreate]


class PurchaseReturUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    purchase_order_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    alamat: Optional[str] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class PurchaseReturResponse(PurchaseReturBase):
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
    purchase_order: Optional[PurchaseOrderSimpleResponse] = None
    supplier: Optional[SupplierSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    details: List[PurchaseReturDetailResponse] = []


# ==========================================
# PENERIMAAN BARANG DETAIL
# ==========================================
class PenerimaanBarangDetailBase(BaseSchema):
    barang_id: UUID
    qty: int = 0
    satuan_id: UUID


class PenerimaanBarangDetailCreate(PenerimaanBarangDetailBase):
    pass


class PenerimaanBarangDetailResponse(PenerimaanBarangDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None
    satuan: Optional[SatuanSimpleResponse] = None


# ==========================================
# PENERIMAAN BARANG
# ==========================================
class PenerimaanBarangBase(BaseSchema):
    tanggal: datetime
    purchase_order_id: UUID
    supplier_id: UUID
    alamat: Optional[str] = None
    keterangan: Optional[str] = None


class PenerimaanBarangCreate(PenerimaanBarangBase):
    details: List[PenerimaanBarangDetailCreate]


class PenerimaanBarangUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    purchase_order_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    alamat: Optional[str] = None
    keterangan: Optional[str] = None


class PenerimaanBarangResponse(PenerimaanBarangBase):
    id: UUID
    no_form: str
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    purchase_order: Optional[PurchaseOrderSimpleResponse] = None
    supplier: Optional[SupplierSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    details: List[PenerimaanBarangDetailResponse] = []
