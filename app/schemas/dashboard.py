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


# === Update ASAHI #018 — Widget Produksi & Pembelian (dashboard per role) ===

class ProduksiRopTerbaruItem(BaseSchema):
    """ROP terbaru untuk widget Produksi."""
    id: str
    no_rop: str
    tanggal: Optional[str] = None
    project_name: str = ""
    model: Optional[str] = None
    status: str = "DRAFT"
    total_item: int = 0


class ProduksiWidget(BaseSchema):
    """Ringkasan Request Order Part (modul Produksi) — #018.

    Ditampilkan bila user punya production.request_order_part.view
    (role Staf Engineering, Manajer Keuangan, Viewer Management, dll.).
    """
    total_rop: int = 0
    draft: int = 0
    dibuat: int = 0
    total_part: int = 0
    terbaru: List[ProduksiRopTerbaruItem] = []


class PembelianPoItem(BaseSchema):
    """PO terbaru untuk widget Pembelian."""
    id: str
    no_pesanan: str
    tanggal: Optional[str] = None
    supplier: str = ""
    grand_total: Decimal = Decimal("0")
    status: str = "DRAFT"


class PembelianWidget(BaseSchema):
    """Ringkasan Purchase Order — #018.

    Ditampilkan bila user punya purchase.purchase_order.view
    (role Staf Pembelian, Manajer Keuangan, dll.).
    """
    total_po: int = 0
    draft: int = 0
    diproses: int = 0
    selesai: int = 0
    dibatalkan: int = 0
    nilai_open: Decimal = Decimal("0")
    terbaru: List[PembelianPoItem] = []


class DashboardSummaryResponse(BaseSchema):
    """Response /dashboard/summary — SEMUA section kini opsional (#018).

    Sebelum #018 semua widget dikirim ke semua role. Kini setiap section
    hanya dihitung & dikirim bila user punya permission terkait — dashboard
    menjadi berbeda per role (lihat endpoint dashboard.py untuk pemetaan
    section → permission code).
    """
    # Keuangan — reports.financial.view
    laba_rugi: Optional[LabaRugiWidget] = None
    beban_biaya: Optional[BebanBiayaWidget] = None
    # Kas & Bank — reports.cashbank.view
    cashflow: Optional[CashflowWidget] = None
    # Penjualan — sales.sales_invoice.view
    tren_penjualan: Optional[TrenPenjualanWidget] = None
    faktur_jatuh_tempo: Optional[FakturJatuhTempoWidget] = None
    # Aktivitas — selalu tersedia untuk semua role
    aktivitas_terbaru: Optional[AktivitasTerbaruWidget] = None
    # Persediaan — inventory.stock.view
    inventory_value: Optional[InventoryValueWidget] = None
    low_stock: Optional[LowStockWidget] = None
    # Kesehatan akuntansi — reports.reconciliation_report.view
    accounting_health: Optional[AccountingHealthSummaryWidget] = None
    # Neraca KPI & margin — reports.financial.view
    balance_sheet_kpi: Optional[dict] = None
    margin: Optional[dict] = None
    # Produksi — production.request_order_part.view (#018)
    produksi: Optional[ProduksiWidget] = None
    # Pembelian — purchase.purchase_order.view (#018)
    pembelian: Optional[PembelianWidget] = None
