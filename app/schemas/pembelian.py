"""
Schemas untuk modul Pembelian.
PurchaseOrder, PurchaseInvoice, PurchaseRetur, PenerimaanBarang + Detail tabel.
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
    # === Update ASAHI #002 — tracking item PO ke SO + kategori belanja ===
    # Opsional: barang umum/consumable tidak terikat SO. Satu PO bisa memuat
    # baris dari beberapa SO berbeda (relasi per baris, bukan per dokumen).
    sales_order_id: Optional[UUID] = None
    # Dasar belanja: CONSUMABLE / PROJECT / ASSET / ATK — penentu tujuan
    # posting COA saat invoice/penerimaan di-posting (divalidasi di service).
    jenis_pembelian: Optional[str] = None


class PurchaseOrderDetailCreate(PurchaseOrderDetailBase):
    pass


class SalesOrderMiniResponse(BaseSchema):
    """SO ringkas untuk nested di baris PO (Update ASAHI #002 — kolom Ket. SO)."""
    id: UUID
    no_pesanan: str
    pelanggan_nama: Optional[str] = None


class PurchaseOrderDetailResponse(PurchaseOrderDetailBase):
    id: UUID
    barang: Optional[BarangSimpleResponse] = None
    satuan: Optional[SatuanSimpleResponse] = None  # Phase B
    # === Update ASAHI #002 — nested SO untuk kolom "Keterangan SO" ===
    sales_order: Optional[SalesOrderMiniResponse] = None


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
    # === Update ASAHI — pilihan PPh23/PPN saat input PO (bisa keduanya,
    # salah satu, atau tidak sama sekali) ===
    ppn_applicable: bool = True
    pph23_applicable: bool = False
    pph23: Decimal = Decimal("2")
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
    # === Update ASAHI #002 — PIC Gudang (kolom Pengiriman PO) ===
    # None = biarkan backend mengisi snapshot dari master alamat pengiriman;
    # string kosong = sengaja dikosongkan (tetap disimpan apa adanya).
    pic_gudang: Optional[str] = None
    pic_gudang_telepon: Optional[str] = None


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
    # === Update ASAHI — pilihan PPh23/PPN opsional ===
    ppn_applicable: Optional[bool] = None
    pph23_applicable: Optional[bool] = None
    pph23: Optional[Decimal] = None
    keterangan: Optional[str] = None
    # Phase B — new fields
    syarat_bayar_id: Optional[UUID] = None
    currency: Optional[str] = None
    # === Update ASAHI #3 — alamat pengiriman (gudang tujuan) + PPIC ===
    alamat_pengiriman_id: Optional[UUID] = None
    ppic: Optional[bool] = None
    # === Update ASAHI #002 — PIC Gudang ===
    pic_gudang: Optional[str] = None
    pic_gudang_telepon: Optional[str] = None
    # Phase B — support detail update
    details: Optional[List[PurchaseOrderDetailCreate]] = None


class PurchaseOrderResponse(PurchaseOrderBase):
    id: UUID
    no_pesanan: str
    sub_total: Decimal
    total_diskon: Decimal
    total_ppn: Decimal
    total_pph23: Decimal
    total_biaya_tambahan: Decimal
    grand_total: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None  # LEGACY — should always be null
    supplier_name_snapshot: Optional[str] = None  # Phase B
    # === Update ASAHI #3 — snapshot teks alamat pengiriman untuk cetak PO ===
    alamat_pengiriman: Optional[str] = None
    # === Update ASAHI #002 — snapshot PIC Gudang untuk cetak PO ===
    pic_gudang: Optional[str] = None
    pic_gudang_telepon: Optional[str] = None
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
    # AUD-10 (update #04): default None = pakai tarif global (app_setting
    # RATE_PPN) — di-resolve endpoint sebelum service; klien tetap bisa kirim
    # tarif custom per dokumen.
    ppn: Optional[Decimal] = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    # === AUD-09 (update #04) — PPh 23 pada Purchase Invoice ===
    # Mirror SI/PO: potongan PPh 23 supplier tercatat sejak invoice (hutang
    # net + Cr Hutang PPh 23 saat posting). Default false = perilaku lama
    # (PPh 23 hanya dicatat saat pelunasan).
    pph23: Optional[Decimal] = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    pph23_applicable: bool = False
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
    # AUD-09 — PPh 23 PI (hanya bisa diubah saat masih draft)
    pph23: Optional[Decimal] = None
    pph23_applicable: Optional[bool] = None
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
    total_pph23: Decimal = Decimal("0")
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
    # AUD-12 (update #05) — PPh23 retur derived dari PI sumber (net-of-PPh23)
    pph23_applicable: bool = False
    pph23: Decimal = Decimal("0")
    total_pph23: Decimal = Decimal("0")
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


# ==========================================
# LPPB — LAPORAN PEMERIKSAAN & PENGUJIAN BARANG (Update ASAHI #005)
# Dokumen RECORD QC barang masuk. Tidak posting stok/jurnal.
# ==========================================
class LppbDetailCreate(BaseSchema):
    """Satu baris pemeriksaan/pengujian.

    Baris barang: barang_id + purchase_order_detail_id + snapshot PO terisi
    (prefill dari GET /purchase-order/{id}/sisa di sisi frontend).
    Baris jasa pengujian (mis. NDT): barang_id NULL — hanya deskripsi
    manual; dibuat berpasangan di bawah barangnya lewat urutan frontend.
    """
    urutan: int = 1
    barang_id: UUID | None = None
    purchase_order_detail_id: UUID | None = None
    kode_material: str | None = None
    deskripsi_spesifikasi: str = Field(min_length=1, max_length=2000)
    satuan: str | None = None
    qty_po: int = 0
    qty_penerimaan: int = 0
    qty_baik: int = 0
    qty_tidak_baik: int = 0
    # qty_sudah/belum_diterima dihitung SERVER dari akumulasi penerimaan
    # PO + qty_penerimaan baris ini (frontend mengirim hanya sebagai info
    # prefill; nilai final yang dipakai hasil kalkulasi server).


class LppbDetailResponse(BaseSchema):
    id: UUID
    urutan: int
    barang_id: UUID | None = None
    purchase_order_detail_id: UUID | None = None
    kode_material: str | None = None
    deskripsi_spesifikasi: str
    satuan: str | None = None
    qty_po: int
    qty_penerimaan: int
    qty_baik: int
    qty_tidak_baik: int
    qty_sudah_diterima: int
    qty_belum_diterima: int


class LppbBase(BaseSchema):
    tanggal: datetime
    tanggal_terima: datetime | None = None
    purchase_order_id: UUID
    nomor_do: str | None = None
    nomor_ri: str | None = None
    proyek: str | None = None
    revisi: str | None = None
    no_format: str | None = None
    keterangan: str | None = None
    mengetahui_jabatan: str | None = None
    mengetahui_nama: str | None = None


class LppbCreate(LppbBase):
    details: List[LppbDetailCreate] = Field(min_length=1, description="Minimal 1 baris pemeriksaan")


class LppbUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    tanggal_terima: Optional[datetime] = None
    purchase_order_id: Optional[UUID] = None
    nomor_do: Optional[str] = None
    nomor_ri: Optional[str] = None
    proyek: Optional[str] = None
    revisi: Optional[str] = None
    no_format: Optional[str] = None
    keterangan: Optional[str] = None
    mengetahui_jabatan: Optional[str] = None
    mengetahui_nama: Optional[str] = None
    details: Optional[List[LppbDetailCreate]] = None


class LppbListResponse(BaseSchema):
    """Baris list ringan (tanpa items)."""
    id: UUID
    no_form: str
    tanggal: datetime
    tanggal_terima: Optional[datetime] = None
    purchase_order_id: UUID
    supplier_id: UUID
    nomor_do: Optional[str] = None
    nomor_ri: Optional[str] = None
    proyek: Optional[str] = None
    revisi: Optional[str] = None
    no_format: Optional[str] = None
    keterangan: Optional[str] = None
    mengetahui_jabatan: Optional[str] = None
    mengetahui_nama: Optional[str] = None
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    total_item: int = 0
    purchase_order: Optional[PurchaseOrderSimpleResponse] = None
    supplier: Optional[SupplierSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None


class LppbResponse(LppbBase):
    id: UUID
    no_form: str
    supplier_id: UUID
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    purchase_order: Optional[PurchaseOrderSimpleResponse] = None
    supplier: Optional[SupplierSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    details: List[LppbDetailResponse] = []
