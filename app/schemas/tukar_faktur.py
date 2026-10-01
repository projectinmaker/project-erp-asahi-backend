"""Schemas untuk modul Tukar Faktur (proof of receipt) — Update #4.

Dokumen tanda terima faktur: TANPA jurnal, TANPA stok, TANPA workflow.
Simple-response didefinisikan LOKAL (pola schemas/persediaan.py) agar
tidak menimbulkan circular import dengan schemas/penjualan.py.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.base import BaseSchema


# ==========================================
# HELPER: Nested response untuk relasi (lokal)
# ==========================================
class BarangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    harga_pokok: Decimal = Decimal("0")


class SatuanSimpleResponse(BaseSchema):
    id: UUID
    nama: str


class PelangganSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class SalesInvoiceSimpleResponse(BaseSchema):
    id: UUID
    no_invoice: str


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    username: str
    nama_lengkap: str


# ==========================================
# TUKAR FAKTUR DETAIL
# ==========================================
class TukarFakturDetailCreate(BaseSchema):
    barang_id: UUID
    satuan_id: UUID | None = None
    qty: int = 0


class TukarFakturDetailResponse(TukarFakturDetailCreate):
    id: UUID
    barang: BarangSimpleResponse | None = None
    satuan: SatuanSimpleResponse | None = None


# ==========================================
# TUKAR FAKTUR
# ==========================================
class TukarFakturCreate(BaseSchema):
    tanggal: datetime
    sales_invoice_id: UUID
    keterangan: str | None = None


class TukarFakturUpdate(BaseSchema):
    tanggal: datetime | None = None
    keterangan: str | None = None


class TukarFakturResponse(BaseSchema):
    id: UUID
    no_tukar_faktur: str
    tanggal: datetime
    sales_invoice_id: UUID
    pelanggan_id: UUID
    no_so: str | None = None
    no_po_customer: str | None = None
    no_surat_jalan: str | None = None
    total: Decimal
    keterangan: str | None = None
    status: str
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
    pelanggan: PelangganSimpleResponse | None = None
    sales_invoice: SalesInvoiceSimpleResponse | None = None
    creator: PenggunaSimpleResponse | None = None
    details: list[TukarFakturDetailResponse] = []
