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
    # Update ASAHI — cetak Delivery Order (Pengiriman, dulu "Surat Jalan"):
    # alamat perusahaan dari master pelanggan ditampilkan di bawah nama
    # "Kirim ke" pada cetakan.
    alamat: Optional[str] = None


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


class GudangSimpleResponse(BaseSchema):
    """=== Update #066 — gudang asal pengiriman (DO): label gudang di form
    & daftar DO. Definisi LOKAL (mirror pola stok_kartu/persediaan — id/kode/
    nama) agar modul penjualan tidak bergantung modul lain. ==="""
    id: UUID
    kode: str
    nama: str


class SatuanSimpleResponse(BaseSchema):
    id: UUID
    nama: str


class SalesOrderSimpleResponse(BaseSchema):
    id: UUID
    no_pesanan: str
    # Update #4 — ikut mengalir ke semua response memakai nested ini
    # (SalesInvoiceResponse.salesOrder, PengirimanBarangResponse.salesOrder, dll).
    customer_po_number: str | None = None
    # === Update #040 — sumber penawaran ikut mengalir ke nested SO supaya
    # form/detail/cetakan Invoice bisa menampilkan kotak SISA PEMBAYARAN
    # penawaran (alur DP bertahap: invoice DP → sisa → SO baru dari penawaran
    # sama). Auto-populate dari ORM (SalesOrder.penawaran_id); None = SO manual. ===
    penawaran_id: UUID | None = None


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
    # === Update #032 — barang_id OPSIONAL: baris bisa berupa ITEM BEBAS ===
    # (jasa/custom dari penawaran; wajib item_nama saat barang_id NULL).
    barang_id: UUID | None = None
    item_nama: str | None = None
    item_satuan: str | None = None
    harga: Decimal = Decimal("0")
    qty: int = 0
    diskon: Optional[Decimal] = Decimal("0")
    sub_total: Decimal = Decimal("0")
    # === Update #042 — field `dp` per-baris DIHAPUS: DP/Pelunasan kini satu
    # nilai header invoice (dpPelunasan) yang didistribusikan proporsional
    # otomatis oleh backend (baris SO selalu nilai penuh harga × qty). ===
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
    # === Update ASAHI — revisi Sales Order ===
    # Ekspedisi & Penjual dihapus dari dokumen SO.
    # customer_code = singkatan bebas nama perusahaan customer (input manual)
    # yang menjadi segmen nomor: "SO ASI/TBI/2026/001"; kosong → "SO ASI/-/2026/001".
    customer_code: Optional[str] = None
    tanggal_pengiriman: Optional[datetime] = None  # label UI/cetak: "Target Pengiriman"
    alamat_pengiriman: Optional[str] = None
    diskon_global: Optional[Decimal] = Decimal("0")
    keterangan: Optional[str] = None
    # === Update #042 — PPN dihapus dari SO (pajak hanya di INVOICE): field
    # ppn/total_ppn dihapus dari schema create/update/response. ===
    # Phase B — deprecated, SO tidak boleh posting jurnal
    auto_post_jurnal: bool = False
    # Phase B — new fields
    customer_po_number: Optional[str] = None
    customer_po_date: Optional[date] = None
    currency: str = 'IDR'


class SalesOrderCreate(SalesOrderBase):
    details: List[SalesOrderDetailCreate]
    biaya_tambahan: List[TransaksiBiayaCreate] = []
    # === Update #032 — SO dibuat dari Penawaran (form SO: "Pilih Penawaran"
    # atau tombol konversi): link ke dokumen sumber. Repeat order — satu
    # penawaran boleh dipakai beberapa SO; kosong = SO manual. ===
    penawaran_id: UUID | None = None
    # === Update #031 — Pembayaran Termin (field di form SO setelah Diskon):
    # baris-baris nominal saja (mis. 3x pembayaran). Kosong = SO tanpa termin.
    # Update #035 — TERMIN BEBAS: nominal ditentukan user; total TIDAK
    # diwajibkan sama dengan grand_total (kurang → sisa lewat Invoice;
    # lebih → diperbolehkan; service hanya menolak nominal <= 0). ===
    pembayaran_termin: List[Decimal] = []


class SalesOrderUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    pelanggan_id: Optional[UUID] = None
    syarat_bayar_id: Optional[UUID] = None
    # ekspedisi/penjual dihapus (revisi SO); customer_code TIDAK diizinkan
    # di update karena bagian dari nomor dokumen (immutable setelah create).
    tanggal_pengiriman: Optional[datetime] = None
    alamat_pengiriman: Optional[str] = None
    diskon_global: Optional[Decimal] = None
    keterangan: Optional[str] = None
    # === Update #042 — ppn dihapus dari update SO (kolom model di-drop). ===
    # Phase B — new fields
    customer_po_number: Optional[str] = None
    customer_po_date: Optional[date] = None
    currency: Optional[str] = None
    # Phase B — support detail update
    details: Optional[List[SalesOrderDetailCreate]] = None


class SalesOrderResponse(SalesOrderBase):
    id: UUID
    no_pesanan: str
    # === Update #032 — sumber penawaran (repeat order; None = SO manual) ===
    penawaran_id: Optional[UUID] = None
    penawaran_no: Optional[str] = None
    penawaran_revisi: Optional[int] = None
    sub_total: Decimal
    total_diskon: Decimal
    # === Update #042 — total_ppn dihapus: SO tidak lagi ber-PPN (PPN hanya
    # di INVOICE); dasar_pajak (DPP) ikut dihapus karena tak bermakna tanpa
    # PPN. Grand total = subtotal − diskon + biaya. ===
    total_biaya_tambahan: Decimal
    grand_total: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None  # LEGACY — should always be null
    fulfillment_status: Optional[str] = None  # Phase B
    # === Update #031 — ringkasan termin pembayaran (kolom "Pembayaran" di
    # daftar SO: mis. "2/3 · Rp 30.000.000"). 0/None = SO tanpa termin. ===
    termin_total: Optional[Decimal] = None
    termin_dibayar: Optional[Decimal] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    pelanggan: Optional[PelangganSimpleResponse] = None
    syarat_bayar: Optional[SyaratBayarSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None  # LEGACY
    details: List[SalesOrderDetailResponse] = []
    biaya_tambahan: List[TransaksiBiayaResponse] = []


# ==========================================
# SALES INVOICE DETAIL
# ==========================================
class SalesInvoiceDetailBase(BaseSchema):
    # === Update #032 — barang_id OPSIONAL: baris invoice bisa berupa ITEM
    # BEBAS (jasa/custom dari penawaran → SO; wajib item_nama bila tanpa
    # barang). Pendapatan baris bebas posting ke akun default. ===
    barang_id: UUID | None = None
    item_nama: str | None = None
    item_satuan: str | None = None
    harga: Decimal = Decimal("0")
    qty: int = 0
    diskon: Optional[Decimal] = Decimal("0")
    sub_total: Decimal = Decimal("0")
    # === Update #042 — field `dp` per-baris invoice DIHAPUS: nilai yang
    # ditagih dikendalikan header dpPelunasan (distribusi proporsional
    # otomatis; sub_total baris = porsi dpPelunasan saat > 0). ===
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
    # AUD-10: None = pakai tarif global (RATE_PPN), di-resolve endpoint
    ppn: Optional[Decimal] = None
    # === Update ASAHI — pilihan PPh23/PPN saat input SI (bisa keduanya,
    # salah satu, atau tidak sama sekali) ===
    ppn_applicable: bool = True
    pph23_applicable: bool = False
    # AUD-10: None = pakai tarif global (RATE_PPH23), di-resolve endpoint
    pph23: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: bool = False
    # === Update #042 — DP/Pelunasan (field header): "nilai yang ditagih
    # invoice ini". Invoice ter-link SO: None = auto-isi sisa tagihan SO
    # (guard 0 < x ≤ sisa). Standalone: None/0 = perilaku lama; bila diisi
    # harus 0 < x ≤ Σ gross baris (harga × qty). Saat > 0 baris invoice
    # otomatis didistribusikan proporsional (sub_total baris = porsi). ===
    dp_pelunasan: Optional[Decimal] = None


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
    # === Update ASAHI — pilihan PPh23/PPN opsional ===
    ppn_applicable: Optional[bool] = None
    pph23_applicable: Optional[bool] = None
    pph23: Optional[Decimal] = None
    keterangan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None
    # === Update #042 — DP/Pelunasan opsional saat update: bila dikirim
    # → divalidasi ulang terhadap sisa tagihan SO (invoice ini dikecualikan);
    # None = pertahankan nilai tersimpan. ===
    dp_pelunasan: Optional[Decimal] = None
    # Update #3 — penggantian baris detail (termasuk link source-line
    # sales_order_detail_id / delivery_detail_id) saat invoice masih draft.
    details: list[SalesInvoiceDetailCreate] | None = None


class SalesInvoiceResponse(SalesInvoiceBase):
    id: UUID
    no_invoice: str
    sub_total: Decimal
    total_diskon: Decimal
    total_ppn: Decimal
    total_pph23: Decimal
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
    # === Update #042 — DP/Pelunasan: nilai yang ditagih invoice ini
    # (0 = invoice lama / tagih penuh). soSisaNilai = sisa tagihan NILAI SO
    # SETELAH invoice ini (None bila standalone tanpa SO) — dihitung ORM
    # property SalesInvoice.so_sisa_nilai (object_session; grand_total SO −
    # Σ nilai-tagih invoice non-DIBATALKAN milik SO). ===
    dp_pelunasan: Decimal = Decimal("0")
    so_sisa_nilai: Optional[Decimal] = None

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
    # AUD-10: None = pakai tarif global (RATE_PPN), di-resolve endpoint
    ppn: Optional[Decimal] = None
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
    # AUD-12 (update #05) — PPh23 retur derived dari SI sumber (net-of-PPh23)
    pph23_applicable: bool = False
    pph23: Decimal = Decimal("0")
    total_pph23: Decimal = Decimal("0")
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
    # === Update #041 — barang_id/satuan_id OPSIONAL + item bebas: baris DO
    # bisa berupa ITEM BEBAS dari SO manufaktur/jasa/custom (wajib item_nama
    # bila barang_id NULL). Baris bebas TIDAK menggerakkan stok/HPP saat
    # finish — murni baris surat jalan. ===
    barang_id: UUID | None = None
    item_nama: str | None = None
    item_satuan: str | None = None
    qty: int = Field(gt=0, description="Qty pengiriman harus bilangan bulat positif (> 0)")
    satuan_id: Optional[UUID] = None
    # Phase C fix (Catatan Audit Delivery §7): link ke SO detail untuk over-delivery check
    sales_order_detail_id: Optional[UUID] = None
    # === Update #066 — snapshot harga jual & diskon % line SO (kolom
    # nominal di form/daftar/detail DO). Kosong/null → backend mengambil
    # harga/diskon line SO saat create (lihat penjualan_service). ===
    harga: Optional[Decimal] = None
    diskon: Optional[Decimal] = None


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
    # Fix E2E audit: expose link jurnal HPP (kolom jurnal_umum_id ada di model;
    # modul lain (SI/PI/kas-bank) sudah expose — pengiriman sebelumnya bocor
    # link jurnalnya sehingga FE tidak bisa menampilkan nomor jurnal terkait).
    jurnal_umum_id: Optional[UUID] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    sales_order: Optional[SalesOrderSimpleResponse] = None
    pelanggan: Optional[PelangganSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    # === Update #066 — gudang asal pengiriman (label di form & daftar DO;
    # gudang fleksibel — boleh non-aktif, wajib hanya bila ada baris master). ===
    gudang: Optional[GudangSimpleResponse] = None
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
    # === Update #032 — barang_id OPSIONAL: baris bisa berupa ITEM BEBAS ===
    # (jasa/custom). is_bebas=True → baris TIDAK bisa dikirim (tidak ada di
    # gudang) tapi BOLEH difakturkan; UI pengiriman menyembunyikan baris ini.
    barang_id: UUID | None = None
    item_nama: str | None = None
    item_satuan: str | None = None
    is_bebas: bool = False
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
    # === Update #042 — field `dp` per-baris DIHAPUS (DP/Pelunasan kini
    # header invoice dpPelunasan; baris SO selalu nilai penuh). ===


class SalesOrderSisaBiayaResponse(BaseSchema):
    """=== Update #068 — satu baris biaya tambahan SO (prefill form Invoice).

    Biaya SO ikut difakturkan saat tarik data supaya Σ nilai-tagih invoice
    mencapai grand_total SO (tanpa ini SO nyangkut di Diproses selamanya —
    lihat komentar endpoint /sales-order/{id}/sisa). ===
    """
    nama: str
    nilai: float = 0.0


class SalesOrderSisaResponse(BaseSchema):
    sales_order_id: UUID
    pelanggan_id: UUID
    syarat_bayar_id: UUID | None = None
    no_pesanan: str
    alamat_pengiriman: str | None = None
    # === Update #042 — sisa tagihan NILAI SO = max(0, grand_total −
    # Σ nilai-tagih invoice non-DIBATALKAN milik SO; nilai-tagih invoice =
    # dp_pelunasan bila > 0, selain itu sub_total). Patokan DP/Pelunasan
    # invoice berikutnya atas SO ini. ===
    sisa_nilai: float = 0.0
    details: list[SalesOrderSisaDetailResponse] = []
    # === Update #068 — baris biaya tambahan SO (opsional; default kosong —
    # konsumen lama aman). Dipakai form Invoice untuk prefill tabel biaya. ===
    biaya_tambahan: list[SalesOrderSisaBiayaResponse] = []


# ==========================================
# SISA PENGIRIMAN / DO (tarik No DO — Update #004)
# ==========================================
class PengirimanSisaDetailResponse(BaseSchema):
    """Satu baris DO + qty yang sudah difakturkan.

    qtyTerfaktur = SUM invoice detail (status DIPROSES/SELESAI) yang ter-link
    ke baris DO ini via delivery_detail_id
    (mirror sales_validation.get_qty_invoiced_so_far).
    sisaFaktur = qtyKirim - qtyTerfaktur (min 0).
    harga/diskon ditarik dari line SO asal bila baris DO ter-link
    (sales_order_detail_id) — baris DO tanpa link SO mengembalikan 0
    (user mengisi manual di form invoice).
    """
    delivery_detail_id: UUID
    sales_order_detail_id: UUID | None = None
    # === Update #041 — barang_id OPSIONAL (baris DO item bebas — SO
    # manufaktur/custom). ===
    barang_id: UUID | None = None
    item_nama: str | None = None
    item_satuan: str | None = None
    kode_barang: str | None = None
    nama_barang: str | None = None
    satuan_id: UUID | None = None
    satuan_nama: str | None = None
    qty_kirim: int
    qty_terfaktur: int
    sisa_faktur: int
    harga: float = 0
    diskon: float = 0
    # === Update #042 — field `dp` DIHAPUS (baris SO tidak lagi ber-DP;
    # nilai tagih dikendalikan dpPelunasan header invoice). ===


class PengirimanSisaResponse(BaseSchema):
    pengiriman_id: UUID
    no_surat_jalan: str
    tanggal: datetime
    sales_order_id: UUID
    no_pesanan: str | None = None
    pelanggan_id: UUID
    pelanggan_nama: str | None = None
    alamat_pengiriman: str | None = None
    gudang_id: UUID | None = None
    details: list[PengirimanSisaDetailResponse] = []


# ==========================================
# PENAWARAN (quotation — Update #3)
# ==========================================
class PenawaranDetailBase(BaseSchema):
    # === Update #031 — barang_id OPSIONAL: baris bisa berupa item BEBAS yang
    # diketik manual (belum ada di master barang). Baris bebas wajib punya
    # item_nama; item_satuan/katalog_id opsional. Item bebas tidak masuk
    # master data — tersimpan di katalog penawaran (upsert service). ===
    barang_id: UUID | None = None
    katalog_id: UUID | None = None
    item_nama: str | None = None
    item_satuan: str | None = None
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


class PenawaranSoRingkas(BaseSchema):
    """SO turunan penawaran — baris daftar di kartu pemakaian/sisa (Update #039).

    Dipakai form/detail penawaran untuk menampilkan SO-SO yang dibuat dari
    penawaran yang sama (alur DP bertahap: 1 penawaran → SO DP → SO sisa).
    Hanya SO AKTIF (non-DIBATALKAN) yang disertakan (model.so_list).
    """
    id: UUID
    no_pesanan: str
    tanggal: datetime
    status: str
    grand_total: Decimal = Decimal("0")


class PenawaranBase(BaseSchema):
    tanggal: datetime
    berlaku_hingga: date | None = None
    pelanggan_id: UUID
    syarat_bayar_id: UUID | None = None
    alamat_pengiriman: str | None = None
    keterangan: str | None = None
    # === Update ASAHI #003 — dibawa ke SO saat konversi ===
    # No. PO customer → SalesOrder.customer_po_number.
    customer_po_number: str | None = None
    # Singkatan customer → segmen nomor SO (SO ASI/<singkatan>/YYYY/NNN).
    customer_code: str | None = None
    mata_uang: str = "IDR"
    diskon_global: Decimal | None = Decimal("0")
    # === Update #066 — PPN & PPh23 opsional (mirror Purchase Order): flag
    # dikirim sesuai checkbox form; totalPpn menambah & totalPph23 memotong
    # grand total (dihitung backend refresh_totals). Sejak #042 pajak
    # penawaran dihapus; kini kembali opsional per dokumen. ===
    ppn: Decimal = Decimal("11")
    ppn_applicable: bool = True
    pph23: Decimal = Decimal("2")
    pph23_applicable: bool = False


class PenawaranCreate(PenawaranBase):
    details: list[PenawaranDetailCreate]
    biaya_tambahan: list[TransaksiBiayaCreate] = []
    # === Update #031 — No Quotation: user hanya input bulan & tahun; nomor
    # urut otomatis per pelanggan (reset tahunan), singkatan dari master. ===
    bulan: int | None = Field(None, ge=1, le=12, description="Bulan nomor Quotation (1-12)")
    tahun: int | None = Field(None, ge=2000, le=2100, description="Tahun nomor Quotation")


class PenawaranUpdate(BaseSchema):
    tanggal: datetime | None = None
    berlaku_hingga: date | None = None
    pelanggan_id: UUID | None = None
    syarat_bayar_id: UUID | None = None
    alamat_pengiriman: str | None = None
    keterangan: str | None = None
    # === Update ASAHI #003 ===
    customer_po_number: str | None = None
    customer_code: str | None = None
    mata_uang: str | None = None
    diskon_global: Decimal | None = None
    # === Update #066 — flag pajak bisa diubah saat edit (mirror PO). ===
    ppn: Decimal | None = None
    ppn_applicable: bool | None = None
    pph23: Decimal | None = None
    pph23_applicable: bool | None = None
    details: list[PenawaranDetailCreate] | None = None
    biaya_tambahan: list[TransaksiBiayaCreate] | None = None


class PenawaranResponse(PenawaranBase):
    id: UUID
    no_penawaran: str
    # === Update #032 — revisi dokumen (1 = asal; 2 = Rev. 2, dst) + jumlah
    # pemakaian SO (repeat order; baris ini dihitung dari link SO). ===
    revisi_ke: int = 1
    digunakan_count: int = 0
    # === Update #039 — SISA PENAWARAN (alur DP bertahap: satu penawaran,
    # banyak SO per tahap pembayaran; acuan tagihan tetap invoice). ===
    # dipakai_so_total = Σ grand_total SO AKTIF turunan; sisa_penawaran =
    # grand_total − dipakai (bisa NEGATIF bila SO melebihi — warning UI,
    # bukan blok); so_list = SO turunan aktif utk kartu pemakaian. ===
    dipakai_so_total: Decimal = Decimal("0")
    sisa_penawaran: Decimal = Decimal("0")
    so_list: list[PenawaranSoRingkas] = []
    sub_total: Decimal
    total_diskon: Decimal
    # === Update #066 — pajak penawaran kembali opsional: response membawa
    # total PPN/PPh23 (grand_total sudah termasuk keduanya). ===
    total_ppn: Decimal = Decimal("0")
    total_pph23: Decimal = Decimal("0")
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


# ==========================================
# Update #031 — KATALOG ITEM PENAWARAN + NO QUOTATION + TERMIN SO
# ==========================================
class PenawaranKatalogItemResponse(BaseSchema):
    """Item katalog penawaran (item bebas yang pernah dipakai — saran cepat
    saat membuat penawaran berikutnya; tidak ada di master barang)."""
    id: UUID
    nama: str
    satuan_nama: str | None = None
    harga: Decimal = Decimal("0")
    dipakai_count: int = 1
    keterangan: str | None = None


class NomorQuotationPreviewResponse(BaseSchema):
    """Preview No Quotation berikutnya (indikatif; final saat create)."""
    nomor: str


# ==========================================
# Update #032 — REVISI PENAWARAN + DROPDOWN PENAWARAN UNTUK SO
# ==========================================
class PenawaranUntukSoItem(BaseSchema):
    """Baris dropdown "Pilih Penawaran" di form Sales Order.

    Hanya penawaran berstatus DRAFT / SELESAI (SELESAI tetap bisa dipilih —
    repeat order; DIREVISI & DIBATALKAN tidak ditawarkan).
    """
    id: UUID
    no_penawaran: str
    revisi_ke: int = 1
    status: str
    tanggal: datetime
    pelanggan_id: UUID
    pelanggan_nama: str | None = None
    grand_total: Decimal = Decimal("0")
    digunakan_count: int = 0
    # === Update #039 — sisa penawaran (grand_total − Σ SO aktif) untuk
    # subtitle dropdown "Pilih Penawaran" di form SO: user langsung lihat
    # patokan sisa SEBELUM memilih penawaran. ===
    sisa_penawaran: Decimal = Decimal("0")
    jumlah_item: int = 0


class SoTerminRingkas(BaseSchema):
    id: UUID
    urutan: int
    nominal: Decimal


class SoTerminResponse(BaseSchema):
    """Satu baris termin pembayaran SO + status bayarnya."""
    id: UUID
    sales_order_id: UUID
    urutan: int
    nominal: Decimal
    penerimaan_kas_id: Optional[UUID] = None
    dibayar_pada: Optional[datetime] = None
    # No bukti + status penerimaan pembayar (info ringan utk tampilan).
    no_bukti_penerimaan: Optional[str] = None
    status_penerimaan: Optional[str] = None


class SoTerminOpenItem(BaseSchema):
    """SO dengan termin belum dibayar — pilihan dropdown di form Penerimaan."""
    sales_order_id: UUID
    no_pesanan: str
    pelanggan_id: Optional[UUID] = None
    pelanggan_nama: Optional[str] = None
    grand_total: Decimal = Decimal("0")
    termin_belum_dibayar: List[SoTerminRingkas] = []
    sisa_termin: Decimal = Decimal("0")
    # === Update #036 — SO sudah punya invoice AKTIF (POSTED): pembayaran
    # termin berikutnya otomatis diterapkan ke akun Piutang invoice (bukan
    # Uang Muka) — form menampilkan info ini ke user. ===
    sudah_difakturkan: bool = False


class TerimaPembayaranTerminRequest(BaseSchema):
    """Body POST /penjualan/sales-order/{so_id}/terima-pembayaran."""
    termin_id: UUID
    kas_bank_id: UUID
    tanggal: datetime
    # Akun lawan jurnal (K). None = default Setting Akun UANG_MUKA_PENJUALAN
    # (bisa diganti per transaksi — jawaban user E9).
    akun_perkiraan_id: Optional[UUID] = None
    no_nukti: Optional[str] = None
    catatan: Optional[str] = None
