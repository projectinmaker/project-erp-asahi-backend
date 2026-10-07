"""
Schemas untuk modul Pembelian.
PurchaseOrder, PurchaseInvoice, PurchaseRetur, PenerimaanBarang + Detail tabel.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import computed_field

from app.schemas.base import BaseSchema


# ==========================================
# HELPER: Nested response untuk relasi
# ==========================================
class SupplierSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    # Update ASAHI (cetak PO): Kontak Person & Telepon supplier ditampilkan
    # di bawah "Nama Pemasok" pada print/PDF Purchase Order.
    kontak_person: Optional[str] = None
    telepon: Optional[str] = None


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    username: str
    nama_lengkap: str


class BarangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    harga_pokok: Decimal


class SatuanSimpleResponse(BaseSchema):
    id: UUID
    nama: str


class SyaratBayarSimpleResponse(BaseSchema):
    id: UUID
    nama: str
    hari: Optional[int] = None


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
    # Phase B — UOM on detail
    satuan_id: Optional[UUID] = None


class PurchaseOrderDetailCreate(PurchaseOrderDetailBase):
    pass


class PurchaseOrderDetailResponse(PurchaseOrderDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None
    satuan: Optional[SatuanSimpleResponse] = None  # Phase B


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
    # Phase B — deprecated, PO tidak boleh posting jurnal
    auto_post_jurnal: bool = False
    # Phase B — new fields
    syarat_bayar_id: Optional[UUID] = None
    currency: str = 'IDR'
    # === Update ASAHI #3 — alamat pengiriman (gudang tujuan) + PPIC ===
    # Optional di Base agar PO lama (sebelum update) tetap bisa diserialisasi;
    # PurchaseOrderCreate meng-override jadi WAJIB (pilih satu saat input PO).
    alamat_pengiriman_id: Optional[UUID] = None
    ppic: bool = False


class PurchaseOrderCreate(PurchaseOrderBase):
    details: List[PurchaseOrderDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []
    # Wajib pilih SATU alamat pengiriman (checkbox di form PO).
    alamat_pengiriman_id: UUID


class PurchaseOrderUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    supplier_id: Optional[UUID] = None
    tanggal_kirim: Optional[datetime] = None
    alamat: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    # Phase B — new fields
    syarat_bayar_id: Optional[UUID] = None
    currency: Optional[str] = None
    # === Update ASAHI #3 — alamat pengiriman (gudang tujuan) + PPIC ===
    alamat_pengiriman_id: Optional[UUID] = None
    ppic: Optional[bool] = None
    # Phase B — support detail update
    details: Optional[List[PurchaseOrderDetailCreate]] = None


class PurchaseOrderResponse(PurchaseOrderBase):
    id: UUID
    no_pesanan: str
    sub_total: Decimal
    total_diskon: Decimal
    total_ppn: Decimal
    total_biaya_tambahan: Decimal
    grand_total: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None  # LEGACY — should always be null
    supplier_name_snapshot: Optional[str] = None  # Phase B
    # === Update ASAHI #3 — snapshot teks alamat pengiriman untuk cetak PO ===
    alamat_pengiriman: Optional[str] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    supplier: Optional[SupplierSimpleResponse] = None
    syarat_bayar: Optional[SyaratBayarSimpleResponse] = None  # Phase B
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None  # LEGACY
    details: List[PurchaseOrderDetailResponse] = []
    biaya_tambahan: List[TransaksiBiayaResponse] = []

    @computed_field  # type: ignore[misc]
    @property
    def dasar_pajak(self) -> Decimal:
        """DPP (Dasar Pengenaan Pajak) = sub_total - total_diskon."""
        return self.sub_total - self.total_diskon


# ==========================================
# PURCHASE INVOICE DETAIL
# ==========================================
class PurchaseInvoiceDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    diskon: Optional[Decimal] = Decimal("0")
    sub_total: Decimal = Decimal("0")
    # Phase D: UOM on detail
    satuan_id: Optional[UUID] = None
    # === Phase 5 — Source-line trace (Roadmap §20) ===
    # Link baris invoice ke baris PO dan/atau baris penerimaan sumber
    # (dipakai fitur tarik-data + hitung sisa faktur per PO line).
    purchase_order_detail_id: UUID | None = None
    penerimaan_barang_detail_id: UUID | None = None


class PurchaseInvoiceDetailCreate(PurchaseInvoiceDetailBase):
    pass


class PurchaseInvoiceDetailResponse(PurchaseInvoiceDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None
    satuan: Optional[SatuanSimpleResponse] = None  # Phase D
    # Update #5 — nama satuan baris sebagai string (kolom Satuan di cetakan FE);
    # fallback ke satuan dasar barang bila satuan baris tidak diisi.
    satuan_nama: str | None = None


# ==========================================
# PURCHASE INVOICE
# ==========================================
class PurchaseInvoiceBase(BaseSchema):
    tanggal_jatuh_tempo: Optional[date] = None
    syarat_bayar_id: Optional[UUID] = None
    tanggal: datetime
    supplier_id: UUID
    no_faktur: str
    alamat: Optional[str] = None
    diskon_global: Optional[Decimal] = Decimal("0")
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    # Phase D: deprecated
    auto_post_jurnal: bool = False
    # Phase D: new fields
    purchase_order_id: Optional[UUID] = None
    invoice_type: Optional[str] = None  # INVENTORY / NON_INVENTORY / FIXED_ASSET


class PurchaseInvoiceCreate(PurchaseInvoiceBase):
    details: List[PurchaseInvoiceDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []


class PurchaseInvoiceUpdate(BaseSchema):
    tanggal_jatuh_tempo: Optional[date] = None
    syarat_bayar_id: Optional[UUID] = None
    tanggal: Optional[datetime] = None
    supplier_id: Optional[UUID] = None
    no_faktur: Optional[str] = None
    alamat: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    ppn: Optional[Decimal] = None
    keterangan: Optional[str] = None
    # Phase D: new fields
    purchase_order_id: Optional[UUID] = None
    invoice_type: Optional[str] = None
    # Update #3 — penggantian baris detail (termasuk link source-line
    # purchase_order_detail_id / penerimaan_barang_detail_id) saat invoice masih draft.
    details: list[PurchaseInvoiceDetailCreate] | None = None


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
    purchase_order: Optional[PurchaseOrderSimpleResponse] = None  # Phase D
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    details: List[PurchaseInvoiceDetailResponse] = []
    biaya_tambahan: List[TransaksiBiayaResponse] = []

    @computed_field  # type: ignore[misc]
    @property
    def dasar_pajak(self) -> Decimal:
        """DPP (Dasar Pengenaan Pajak) = sub_total - total_diskon."""
        return self.sub_total - self.total_diskon


# ==========================================
# PURCHASE RETUR DETAIL
# ==========================================
class PurchaseReturDetailBase(BaseSchema):
    barang_id: UUID
    harga: Decimal = Decimal("0")
    qty: int = 0
    sub_total: Decimal = Decimal("0")


class PurchaseReturDetailCreate(PurchaseReturDetailBase):
    # Source-line trace (tarik data dari invoice/PO)
    purchase_invoice_detail_id: Optional[UUID] = None
    purchase_order_detail_id: Optional[UUID] = None


class PurchaseReturDetailResponse(PurchaseReturDetailBase):
    id: UUID
    purchase_invoice_detail_id: Optional[UUID] = None
    purchase_order_detail_id: Optional[UUID] = None
    barang: Optional[BarangSimpleResponse] = None
    # Update #5 — nama satuan dasar barang (kolom Satuan di cetakan FE)
    satuan: str | None = None


# ==========================================
# PURCHASE RETUR
# ==========================================
class PurchaseReturBase(BaseSchema):
    gudang_id: Optional[UUID] = None
    purchase_invoice_id: Optional[UUID] = None
    tanggal: datetime
    purchase_order_id: UUID
    supplier_id: UUID
    alamat: Optional[str] = None
    ppn: Decimal = Decimal("11")
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = False


class PurchaseReturCreate(PurchaseReturBase):
    details: List[PurchaseReturDetailCreate]


class PurchaseReturUpdate(BaseSchema):
    gudang_id: Optional[UUID] = None
    purchase_invoice_id: Optional[UUID] = None
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

    @computed_field  # type: ignore[misc]
    @property
    def dasar_pajak(self) -> Decimal:
        """DPP (Dasar Pengenaan Pajak) = sub_total (retur tanpa diskon)."""
        return self.sub_total


# ==========================================
# PENERIMAAN BARANG DETAIL
# ==========================================
class PenerimaanBarangDetailBase(BaseSchema):
    harga_perolehan: Optional[Decimal] = None
    tanggal_kedaluwarsa: Optional[date] = None
    barang_id: UUID
    qty: int = 0
    satuan_id: UUID
    # === Phase 5 — source-line trace (Roadmap §19): link ke baris PO sumber ===
    # (dipakai fitur tarik-data + hitung sisa terima per PO line)
    purchase_order_detail_id: UUID | None = None


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
    gudang_id: Optional[UUID] = None
    tanggal: datetime
    purchase_order_id: UUID
    supplier_id: UUID
    alamat: Optional[str] = None
    keterangan: Optional[str] = None
    # Tahap 2: Optional link ke purchase_invoice untuk anti double-record
    # Persediaan. Bila diisi DAN akun PENERIMAAN_DALAM_PROSES sudah di-configure,
    # saat invoice dipost, sistem akan D: PENERIMAAN_DALAM_PROSES (clearing)
    # alih-alih D: Pembelian.
    purchase_invoice_id: Optional[UUID] = None


class PenerimaanBarangCreate(PenerimaanBarangBase):
    details: List[PenerimaanBarangDetailCreate]


class PenerimaanBarangUpdate(BaseSchema):
    gudang_id: Optional[UUID] = None
    tanggal: Optional[datetime] = None
    purchase_order_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    alamat: Optional[str] = None
    keterangan: Optional[str] = None
    purchase_invoice_id: Optional[UUID] = None


class PenerimaanBarangResponse(PenerimaanBarangBase):
    id: UUID
    no_form: str
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    purchase_order: Optional[PurchaseOrderSimpleResponse] = None
    supplier: Optional[SupplierSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    details: List[PenerimaanBarangDetailResponse] = []



# ==========================================
# SISA PURCHASE ORDER (tarik data — Update #3)
# ==========================================
class PurchaseOrderSisaDetailResponse(BaseSchema):
    """Satu baris PO + qty yang sudah dipakai dokumen lanjutan.

    qtyDiterima = SUM penerimaan (status DIPROSES/SELESAI) untuk baris PO ini
    (mirror purchase_validation.get_qty_received_so_far).
    qtyTerfaktur = SUM invoice (status DIPROSES/SELESAI) via dua jalur link
    (purchase_order_detail_id langsung ATAU penerimaan_barang_detail_id dari
    penerimaan baris PO ini) — mirror purchase_validation.get_qty_invoiced_for_po_detail.
    """
    purchase_order_detail_id: UUID
    barang_id: UUID
    kode_barang: str | None = None
    nama_barang: str | None = None
    satuan_id: UUID | None = None
    satuan_nama: str | None = None
    qty_pesanan: int
    qty_diterima: int
    qty_terfaktur: int
    sisa_terima: int
    sisa_faktur: int
    harga: float
    diskon: float


class PurchaseOrderSisaResponse(BaseSchema):
    purchase_order_id: UUID
    supplier_id: UUID
    no_pesanan: str
    alamat: str | None = None
    syarat_bayar_id: UUID | None = None
    details: list[PurchaseOrderSisaDetailResponse] = []
