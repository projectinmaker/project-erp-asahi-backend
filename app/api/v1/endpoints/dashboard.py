from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.dashboard import DashboardSummaryResponse
from app.services import laporan_service
from app.services import dashboard_service
from app.services.access_service import effective_permissions

router = APIRouter()

# ── Update ASAHI #018 — pemetaan section dashboard → permission code ──────
# Section hanya dihitung & dikirim bila user berhak melihatnya, sehingga
# tampilan dashboard berbeda per role (dashboard per role). Pemetaan ini
# selaras dengan permission endpoint sumber datanya:
#   - laba rugi/neraca/margin/beban  : paritas laporan keuangan (reports.financial.view)
#   - cashflow                       : paritas laporan kas & bank (reports.cashbank.view)
#   - tren penjualan & invoice JT    : data invoice penjualan (sales.sales_invoice.view)
#   - nilai persediaan & stok menipis: data stok (inventory.stock.view)
#   - accounting health              : laporan rekonsiliasi (reports.reconciliation_report.view)
#   - produksi (#018)                : production.request_order_part.view
#   - pembelian (#018)               : purchase.purchase_order.view
#   - aktivitas terbaru              : semua role (dashboard.operational.view sudah
#                                      digate router ini)
SECTION_PERMISSIONS = {
    'laba_rugi': 'reports.financial.view',
    'beban_biaya': 'reports.financial.view',
    'balance_sheet_kpi': 'reports.financial.view',
    'margin': 'reports.financial.view',
    'cashflow': 'reports.cashbank.view',
    'tren_penjualan': 'sales.sales_invoice.view',
    'faktur_jatuh_tempo': 'sales.sales_invoice.view',
    'inventory_value': 'inventory.stock.view',
    'low_stock': 'inventory.stock.view',
    'accounting_health': 'reports.reconciliation_report.view',
    'produksi': 'production.request_order_part.view',
    'pembelian': 'purchase.purchase_order.view',
}


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    tanggal: str = Query(..., description="Tanggal referensi (YYYY-MM-DD). Bulan & tahun diambil dari sini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Dashboard summary — widget per permission (#018), 13 section opsional.

    Sebelum #018: 11 widget identik untuk semua role. Kini setiap section
    hanya dihitung & dikirim bila user memiliki permission terkait
    (SECTION_PERMISSIONS di atas) — role berbeda melihat dashboard berbeda:
    Staf Engineering → widget Produksi + Aktivitas; Staf Pembelian →
    Pembelian + Stok + Aktivitas; Staf Keuangan → keuangan lengkap tanpa
    otorisasi; Manajer Keuangan → semua widget.

    Widgets:
    1. Laba Rugi (P&L from GL)
    2. Cashflow (Cash Movement)
    3. Beban Biaya (Expense breakdown)
    4. Tren Penjualan (Invoice Turnover — NOT GL Revenue)
    5. Faktur Jatuh Tempo (Remaining outstanding + overdue flag)
    6. Aktivitas Terbaru (Recent posted journals)
    7. Inventory Value (SUM StockBalance.nilai) — Phase 10
    8. Low Stock (count + items) — Phase 10
    9. Accounting Health (summary from 11 reconciliation checks) — Phase 10
    10. Balance Sheet KPI + Margin — Phase I
    11. Produksi (ROP: total, draft, dibuat, terbaru) — #018
    12. Pembelian (PO: total per status, nilai open, terbaru) — #018
    """
    try:
        dt = datetime.strptime(tanggal, "%Y-%m-%d")
        if dt.year < 2:
            raise ValueError('Tanggal referensi tidak mendukung tren enam bulan')
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    bulan = dt.month
    tahun = dt.year

    # ── Permission user (RBAC v2) → tentukan section yang dihitung ──────
    perms, is_super = effective_permissions(db, current_user)

    def allowed(section: str) -> bool:
        if is_super:
            return True
        code = SECTION_PERMISSIONS.get(section)
        return code is None or code in perms

    payload = {}

    # Keuangan — reports.financial.view
    if allowed('laba_rugi'):
        payload['laba_rugi'] = laporan_service.get_dashboard_laba_rugi(db, bulan, tahun)
        payload['beban_biaya'] = laporan_service.get_dashboard_beban_biaya(db, bulan, tahun)
        # Phase I — KPI neraca & margin (sumber data laporan keuangan)
        payload['balance_sheet_kpi'] = dashboard_service.get_balance_sheet_kpi_widget(db, dt)
        payload['margin'] = dashboard_service.get_margin_widget(db, bulan, tahun)

    # Kas & Bank — reports.cashbank.view
    if allowed('cashflow'):
        payload['cashflow'] = laporan_service.get_dashboard_cashflow(db, bulan, tahun)

    # Penjualan — sales.sales_invoice.view
    if allowed('tren_penjualan'):
        payload['tren_penjualan'] = laporan_service.get_dashboard_tren_penjualan(db, bulan, tahun)
        # P0-03 (Re-Audit §7): widget hanya menampilkan invoice overdue
        # (jatuh_tempo < as_of).
        payload['faktur_jatuh_tempo'] = laporan_service.get_dashboard_faktur_jatuh_tempo(db, dt, overdue_only=True)

    # Persediaan — inventory.stock.view
    if allowed('inventory_value'):
        payload['inventory_value'] = dashboard_service.get_inventory_value_widget(db, dt)
        payload['low_stock'] = dashboard_service.get_low_stock_widget(db, limit=10)

    # Kesehatan akuntansi — reports.reconciliation_report.view
    if allowed('accounting_health'):
        payload['accounting_health'] = dashboard_service.get_accounting_health_summary_widget(db)

    # Produksi — production.request_order_part.view (#018)
    if allowed('produksi'):
        payload['produksi'] = dashboard_service.get_production_widget(db)

    # Pembelian — purchase.purchase_order.view (#018)
    if allowed('pembelian'):
        payload['pembelian'] = dashboard_service.get_purchase_widget(db)

    # Aktivitas terbaru — semua role
    payload['aktivitas_terbaru'] = laporan_service.get_dashboard_aktivitas_terbaru(db, dt)

    return DashboardSummaryResponse(**payload)
