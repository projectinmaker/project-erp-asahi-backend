"""Schemas modul Produksi — Request Order Part (pre-PO dari Engineering)."""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import BaseSchema


# ==========================================
# Nested response ringan untuk relasi
# ==========================================
class SupplierSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class PelangganSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class SalesOrderInfoResponse(BaseSchema):
    """Nested SO ringan — definisi LOKAL (pola schemas/persediaan.py)
    untuk menghindari circular import. Dipakai form ROP menampilkan
    SO yang dipilih di kanan atas."""
    id: UUID
    no_pesanan: str
    customer_code: Optional[str] = None
    customer_po_number: Optional[str] = None
    status: Optional[str] = None
    tanggal: Optional[datetime] = None


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    username: str
    nama_lengkap: str


# ==========================================
# REQUEST ORDER PART — ITEM
# ==========================================
class RequestOrderPartItemBase(BaseSchema):
    no: int = 0
    # Kolom ORDER
    id_part: Optional[str] = None
    description: Optional[str] = None
    material: Optional[str] = None
    qty: Decimal = Decimal("0")
    # Kolom STOK — QS bertipe data seperti ID PART (kode, update #003)
    qs: Optional[str] = None
    supplier_id: Optional[UUID] = None
    # KETERANGAN — dua sub-kolom di bawah header grup (update #003)
    keterangan_order: Optional[str] = None
    keterangan_stok: Optional[str] = None

    @field_validator("qs", mode="before")
    @classmethod
    def _qs_coerce_str(cls, value):
        """Kompatibilitas transisi: payload lama mengirim QS sebagai angka —
        konversi ke string agar tidak 422 (tipe kolom kini varchar)."""
        if value is None or isinstance(value, str):
            return value
        return str(value)


class RequestOrderPartItemCreate(RequestOrderPartItemBase):
    """Item ROP untuk payload create/update. `no` di-renumber server-side."""
    pass


class RequestOrderPartItemResponse(RequestOrderPartItemBase):
    id: UUID
    supplier: Optional[SupplierSimpleResponse] = None


# ==========================================
# REQUEST ORDER PART — HEADER
# ==========================================
class RequestOrderPartBase(BaseSchema):
    tanggal: datetime
    project_name: str = Field(min_length=1, description="Nama project")
    model: Optional[str] = None
    sales_order_id: UUID = Field(description="SO sumber — customer ditarik otomatis")
    catatan: Optional[str] = None
    # Blok tanda tangan (input nama)
    dibuat_oleh: Optional[str] = None
    diperiksa_oleh: Optional[str] = None
    diketahui_oleh: Optional[str] = None
    disetujui_oleh: Optional[str] = None


class RequestOrderPartCreate(RequestOrderPartBase):
    items: List[RequestOrderPartItemCreate] = Field(min_length=1, description="Minimal 1 baris part")


class RequestOrderPartUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    project_name: Optional[str] = None
    model: Optional[str] = None
    sales_order_id: Optional[UUID] = None
    catatan: Optional[str] = None
    dibuat_oleh: Optional[str] = None
    diperiksa_oleh: Optional[str] = None
    diketahui_oleh: Optional[str] = None
    disetujui_oleh: Optional[str] = None
    items: Optional[List[RequestOrderPartItemCreate]] = None


class RequestOrderPartListResponse(BaseSchema):
    """Baris list tanpa items (berat) — hanya agregat jumlah baris."""
    id: UUID
    no_rop: str
    tanggal: datetime
    project_name: str
    model: Optional[str] = None
    sales_order_id: UUID
    pelanggan_id: UUID
    catatan: Optional[str] = None
    dibuat_oleh: Optional[str] = None
    diperiksa_oleh: Optional[str] = None
    diketahui_oleh: Optional[str] = None
    disetujui_oleh: Optional[str] = None
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    total_item: int = 0
    sales_order: Optional[SalesOrderInfoResponse] = None
    pelanggan: Optional[PelangganSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None


class RequestOrderPartResponse(RequestOrderPartBase):
    id: UUID
    no_rop: str
    pelanggan_id: UUID
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    sales_order: Optional[SalesOrderInfoResponse] = None
    pelanggan: Optional[PelangganSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    items: List[RequestOrderPartItemResponse] = []


# ==========================================
# OPSI/DROPDOWN FORM ROP (update ASAHI #018)
# ==========================================
class RopSalesOrderOption(BaseSchema):
    """Opsi SO ringan untuk dropdown form ROP (endpoint /opsi).

    Hanya field yang dipakai form: nomor SO (label), nama pelanggan (subtitle),
    info kartu SO (Customer PO Number, tanggal, status) — tanpa nilai finansial.
    """
    id: UUID
    no_pesanan: str
    customer_po_number: Optional[str] = None
    tanggal: Optional[datetime] = None
    status: Optional[str] = None
    pelanggan: Optional[PelangganSimpleResponse] = None


class RequestOrderPartOptionsResponse(BaseSchema):
    """Dropdown form ROP digabung satu endpoint, scoped permission produksi.

    Sebelum #018 form memanggil GET /penjualan/sales-order (butuh
    sales.sales_order.view) dan GET /master/supplier-dropdown (butuh
    master.supplier.view). Role Staf Engineering kini hanya berpermission
    produksi — kedua sumber data itu dilayani endpoint ini.
    """
    sales_orders: List[RopSalesOrderOption] = []
    suppliers: List[SupplierSimpleResponse] = []
