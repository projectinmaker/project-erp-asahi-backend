from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from app.schemas.base import BaseSchema


# ==========================================
# HELPER: Nested response untuk relasi
# ==========================================

class BarangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class GudangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class MetodeValuasiOption(BaseSchema):
    value: str
    label: str


# ==========================================
# STOK KARTU ENTRY (satu baris kartu stok)
# ==========================================

class StokKartuEntryResponse(BaseSchema):
    """Satu baris dalam kartu stok (stok card)."""
    id: UUID
    tanggal: datetime
    tipe: str
    ref_module: Optional[str] = None
    ref_no: Optional[str] = None
    keterangan: Optional[str] = None

    # MASUK
    masuk_qty: int = 0
    masuk_harga: Decimal = Decimal("0")
    masuk_total: Decimal = Decimal("0")

    # KELUAR
    keluar_qty: int = 0
    keluar_harga: Decimal = Decimal("0")
    keluar_total: Decimal = Decimal("0")

    # SALDO
    saldo_qty: int = 0
    saldo_harga: Decimal = Decimal("0")
    saldo_total: Decimal = Decimal("0")

    gudang: Optional[GudangSimpleResponse] = None


# ==========================================
# STOK KARTU SUMMARY (ringkasan nilai stok saat ini)
# ==========================================

class StokKartuLayerInfo(BaseSchema):
    """Info satu layer FIFO/FEFO."""
    id: UUID
    harga_satuan: Decimal
    qty_sisa: int
    total_nilai: Decimal
    tanggal_masuk: datetime
    ref_no: Optional[str] = None


class StokKartuSummaryResponse(BaseSchema):
    """Ringkasan posisi stok + nilai saat ini."""
    barang_id: UUID
    barang_kode: str
    barang_nama: str
    metode_valuasi: str

    # Posisi stok saat ini
    stok_qty: int
    harga_pokok: Decimal
    total_nilai: Decimal

    # Detail layer (hanya diisi jika FIFO/FEFO)
    layers: List[StokKartuLayerInfo] = []
