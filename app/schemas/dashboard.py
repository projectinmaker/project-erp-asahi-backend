"""
Schemas untuk Dashboard.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional
from app.schemas.base import BaseSchema


class LabaRugiWidget(BaseSchema):
    """schema hanya untuk field, bukan model SQLAlchemy"""
    pass


# Override: manual schema tanpa from_attributes karena ini pure response
class LabaRugiWidget(BaseSchema):
    pendapatan: Decimal = Decimal("0")
    hpp: Decimal = Decimal("0")
    laba_kotor: Decimal = Decimal("0")
    beban: Decimal = Decimal("0")
    laba_bersih: Decimal = Decimal("0")


class CashflowWidget(BaseSchema):
    saldo_awal: Decimal = Decimal("0")
    penerimaan: Decimal = Decimal("0")
    pengeluaran: Decimal = Decimal("0")
    saldo_akhir: Decimal = Decimal("0")


class BebanItem(BaseSchema):
    nama_beban: str
    jumlah: Decimal = Decimal("0")


class BebanBiayaWidget(BaseSchema):
    items: List[BebanItem] = []


class TrenPenjualanItem(BaseSchema):
    bulan: str
    total: Decimal = Decimal("0")


class TrenPenjualanWidget(BaseSchema):
    items: List[TrenPenjualanItem] = []
    label_note: Optional[str] = None


class FakturJatuhTempoItem(BaseSchema):
    no_faktur: str
    pelanggan: str
    jumlah: Decimal = Decimal("0")
    jatuh_tempo: str
    status: str
    is_overdue: bool = False
    original_nilai: Optional[Decimal] = None


class FakturJatuhTempoWidget(BaseSchema):
    items: List[FakturJatuhTempoItem] = []


class AktivitasItem(BaseSchema):
    tipe: str
    deskripsi: str
    nomor: str
    tanggal: str
    jumlah: Optional[Decimal] = None


class AktivitasTerbaruWidget(BaseSchema):
    items: List[AktivitasItem] = []


# === Phase 10 — Widget Baru ===

class InventoryValueWidget(BaseSchema):
    """Widget Inventory Value (Roadmap §27: "Inventory Value dari backend SUM StockBalance.nilai")."""
    total_nilai: Decimal = Decimal("0")
    total_qty: int = 0
    barang_count: int = 0
    as_of: Optional[str] = None


class LowStockItem(BaseSchema):
    barang_id: str
    kode: str
    nama: str
    stok: int = 0
    stok_minimum: int = 0
    selisih: int = 0


class LowStockWidget(BaseSchema):
    """Widget Low Stock (Roadmap §27: "Low Stock count backend full dataset")."""
    count: int = 0
    items: List[LowStockItem] = []


class AccountingHealthSummaryWidget(BaseSchema):
    """Widget Accounting Health (Roadmap §27: "Accounting Health visible")."""
    overall_status: str = "UNKNOWN"
    match_count: int = 0
    mismatch_count: int = 0
    not_configured_count: int = 0
    total_checks: int = 0
    error: Optional[str] = None


class DashboardSummaryResponse(BaseSchema):
    laba_rugi: LabaRugiWidget
    cashflow: CashflowWidget
    beban_biaya: BebanBiayaWidget
    tren_penjualan: TrenPenjualanWidget
    faktur_jatuh_tempo: FakturJatuhTempoWidget
    aktivitas_terbaru: AktivitasTerbaruWidget
    # Phase 10 — 3 widget baru
    inventory_value: Optional[InventoryValueWidget] = None
    low_stock: Optional[LowStockWidget] = None
    accounting_health: Optional[AccountingHealthSummaryWidget] = None
    # Phase I — 2 widget baru
    balance_sheet_kpi: Optional[dict] = None
    margin: Optional[dict] = None
