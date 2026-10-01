"""
Schemas untuk modul Penjualan.
SalesOrder, SalesInvoice, SalesRetur, PengirimanBarang + Detail tabel.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import computed_field, Field

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
    # Update #4 — ikut mengalir ke semua response memakai nested ini
    # (SalesInvoiceResponse.salesOrder, PengirimanBarangResponse.salesOrder, dll).
    customer_po_number: str | None = None


class SalesInvoiceSimpleResponse(BaseSchema):
    id: UUID
    no_invoice: str


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
    # Phase B — UOM on detail
    satuan_id: Optional[UUID] = None


class SalesOrderDetailCreate(SalesOrderDetailBase):
    pass


class SalesOrderDetailResponse(SalesOrderDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None
    satuan: Optional[SatuanSimpleResponse] = None  # Phase B


# ==========================================
# SALES ORDER
# ==========================================
class SalesOrderBase(BaseSchema):
    tanggal: datetime
    pelanggan_id: UUID
    syarat_bayar_id: Optional[UUID] = None
    ekspedisi: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None
    penjual: Optional[str] = None
    alamat_pengiriman: Optional[str] = None
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    # Phase B — deprecated, SO tidak boleh posting jurnal
    auto_post_jurnal: bool = False
    # Phase B — new fields
    customer_po_number: Optional[str] = None
    customer_po_date: Optional[date] = None
    currency: str = 'IDR'


class SalesOrderCreate(SalesOrderBase):
    details: List[SalesOrderDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []


class SalesOrderUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    pelanggan_id: Optional[UUID] = None
    syarat_bayar_id: Optional[UUID] = None
    ekspedisi: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None
    penjual: Optional[str] = None
    alamat_pengiriman: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    # Phase B — new fields
    customer_po_number: Optional[str] = None
    customer_po_date: Optional[date] = None
    currency: Optional[str] = None
    # Phase B — support detail update
    details: Optional[List[SalesOrderDetailCreate]] = None


class SalesOrderResponse(SalesOrderBase):
    id: UUID
    no_pesanan: str
    sub_total: Decimal
    total_diskon: Decimal
    total_ppn: Decimal
    total_biaya_tambahan: Decimal
    grand_total: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None  # LEGACY — should always be null
    fulfillment_status: Optional[str] = None  # Phase B
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    pelanggan: Optional[PelangganSimpleResponse] = None
    syarat_bayar: Optional[SyaratBayarSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None  # LEGACY
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
    # === Phase 4 — Source-line trace (Roadmap §14) ===
    # Link baris invoice ke baris SO dan/atau baris pengiriman sumber
    # (dipakai fitur tarik-data + hitung sisa faktur per SO line).
    sales_order_detail_id: UUID | None = None
    delivery_detail_id: UUID | None = None


class SalesInvoiceDetailCreate(SalesInvoiceDetailBase):
    pass


class SalesInvoiceDetailResponse(SalesInvoiceDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None
    # Update #5 — nama satuan dasar barang (kolom Satuan di cetakan FE)
    satuan: str | None = None


# ==========================================
# SALES INVOICE
# ==========================================
class SalesInvoiceBase(BaseSchema):
    tanggal_jatuh_tempo: Optional[date] = None
    tanggal: datetime
    pelanggan_id: UUID
    syarat_bayar_id: Optional[UUID] = None
    sales_order_id: Optional[UUID] = None
    ekspedisi: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None
    alamat_pengiriman: Optional[str] = None
    mata_uang: str = "IDR"
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = False


class SalesInvoiceCreate(SalesInvoiceBase):
    details: List[SalesInvoiceDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []


class SalesInvoiceUpdate(BaseSchema):
    tanggal_jatuh_tempo: Optional[date] = None
    tanggal: Optional[datetime] = None
    pelanggan_id: Optional[UUID] = None
    syarat_bayar_id: Optional[UUID] = None
    sales_order_id: Optional[UUID] = None
    ekspedisi: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None
    alamat_pengiriman: Optional[str] = None
    mata_uang: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None
    # Update #3 — penggantian baris detail (termasuk link source-line
    # sales_order_detail_id / delivery_detail_id) saat invoice masih draft.
    details: list[SalesInvoiceDetailCreate] | None = None


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
    # Update #4 — distinct no_surat_jalan pengiriman ter-link via detail
    # (dibaca dari @property SalesInvoice.no_surat_jalan; None bila invoice
    # dibuat langsung dari SO tanpa pengiriman).
    no_surat_jalan: str | None = None

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
    # === Phase 4 — Source-line trace (Roadmap §16): link ke detail invoice sumber ===
    sales_invoice_detail_id: Optional[UUID] = None


class SalesReturDetailCreate(SalesReturDetailBase):
    pass


class SalesReturDetailResponse(SalesReturDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None
    # Update #5 — nama satuan dasar barang (kolom Satuan di cetakan FE)
    satuan: str | None = None


# ==========================================
# SALES RETUR
# ==========================================
class SalesReturBase(BaseSchema):
    pengiriman_id: Optional[UUID] = None
    gudang_id: Optional[UUID] = None
    tanggal: datetime
    sales_invoice_id: UUID
    pelanggan_id: UUID
    alamat_pengembalian: Optional[str] = None
    no_pengembalian: Optional[str] = None
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = False


class SalesReturCreate(SalesReturBase):
    details: List[SalesReturDetailCreate]


class SalesReturUpdate(BaseSchema):
    pengiriman_id: Optional[UUID] = None
    gudang_id: Optional[UUID] = None
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
    # Relasi sales_retur.sales_invoice → SalesInvoice (bukan SalesOrder) —
    # salah ketik SalesOrderSimpleResponse membuat POST/GET retur 500 (noPesanan missing)
    sales_invoice: Optional[SalesInvoiceSimpleResponse] = None
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
    # Phase C fix (Catatan Audit Delivery §10): qty wajib > 0
    qty: int = Field(gt=0, description="Qty pengiriman harus bilangan bulat positif (> 0)")
    satuan_id: UUID
    # Phase C fix (Catatan Audit Delivery §7): link ke SO detail untuk over-delivery check
    sales_order_detail_id: Optional[UUID] = None


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
    gudang_id: Optional[UUID] = None
    tanggal: datetime
    sales_order_id: UUID
    pelanggan_id: UUID
    ekspedisi: Optional[str] = None
    alamat_pengiriman: Optional[str] = None
    keterangan: Optional[str] = None


class PengirimanBarangCreate(PengirimanBarangBase):
    details: List[PengirimanBarangDetailCreate]


class PengirimanBarangUpdate(BaseSchema):
    gudang_id: Optional[UUID] = None
    tanggal: Optional[datetime] = None
    sales_order_id: Optional[UUID] = None
    pelanggan_id: Optional[UUID] = None
    ekspedisi: Optional[str] = None
    alamat_pengiriman: Optional[str] = None
    keterangan: Optional[str] = None
    # Phase C fix (Catatan Audit Delivery P1): support detail replacement on update
    details: Optional[List[PengirimanBarangDetailCreate]] = None


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



# ==========================================
# SISA SALES ORDER (tarik data — Update #3)
# ==========================================
class SalesOrderSisaDetailResponse(BaseSchema):
    """Satu baris SO + qty yang sudah dipakai dokumen lanjutan.

    qtyTerkirim = SUM pengiriman (status DIPROSES/SELESAI) untuk baris SO ini
    (mirror sales_validation.get_qty_delivered_so_far).
    qtyTerfaktur = SUM invoice (status DIPROSES/SELESAI) via dua jalur link
    (sales_order_detail_id langsung ATAU delivery_detail_id dari pengiriman
    baris SO ini) — mirror sales_validation.get_qty_invoiced_for_so_detail.
    """
    sales_order_detail_id: UUID
    barang_id: UUID
    kode_barang: str | None = None
    nama_barang: str | None = None
    satuan_id: UUID | None = None
    satuan_nama: str | None = None
    qty_pesanan: int
    qty_terkirim: int
    qty_terfaktur: int
    sisa_kirim: int
    sisa_faktur: int
    harga: float
    diskon: float


class SalesOrderSisaResponse(BaseSchema):
    sales_order_id: UUID
    pelanggan_id: UUID
    syarat_bayar_id: UUID | None = None
    no_pesanan: str
    alamat_pengiriman: str | None = None
    details: list[SalesOrderSisaDetailResponse] = []


# ==========================================
# PENAWARAN (quotation — Update #3)
# ==========================================
class PenawaranDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    diskon: Decimal | None = Decimal("0")
    sub_total: Decimal = Decimal("0")
    satuan_id: UUID | None = None
    keterangan: str | None = None


class PenawaranDetailCreate(PenawaranDetailBase):
    pass


class PenawaranDetailResponse(PenawaranDetailBase):
    id: UUID
    barang: BarangSimpleResponse | None = None
    satuan: SatuanSimpleResponse | None = None


class PenawaranBase(BaseSchema):
    tanggal: datetime
    berlaku_hingga: date | None = None
    pelanggan_id: UUID
    syarat_bayar_id: UUID | None = None
    alamat_pengiriman: str | None = None
    keterangan: str | None = None
    mata_uang: str = "IDR"
    diskon_global: Decimal | None = Decimal("0")
    ppn: Decimal = Decimal("11")


class PenawaranCreate(PenawaranBase):
    details: list[PenawaranDetailCreate]
    biaya_tambahan: list[TransaksiBiayaCreate] = []


class PenawaranUpdate(BaseSchema):
    tanggal: datetime | None = None
    berlaku_hingga: date | None = None
    pelanggan_id: UUID | None = None
    syarat_bayar_id: UUID | None = None
    alamat_pengiriman: str | None = None
    keterangan: str | None = None
    mata_uang: str | None = None
    diskon_global: Decimal | None = None
    ppn: Decimal | None = None
    details: list[PenawaranDetailCreate] | None = None
    biaya_tambahan: list[TransaksiBiayaCreate] | None = None


class PenawaranResponse(PenawaranBase):
    id: UUID
    no_penawaran: str
    sub_total: Decimal
    total_diskon: Decimal
    total_ppn: Decimal
    total_biaya_tambahan: Decimal
    grand_total: Decimal
    status: str
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
    pelanggan: PelangganSimpleResponse | None = None
    syarat_bayar: SyaratBayarSimpleResponse | None = None
    creator: PenggunaSimpleResponse | None = None
    details: list[PenawaranDetailResponse] = []
    biaya_tambahan: list[TransaksiBiayaResponse] = []
