from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.dashboard import DashboardSummaryResponse
from app.services import laporan_service
from app.services import dashboard_service

from app.api.reporting import report_scope

router = APIRouter(dependencies=[Depends(report_scope)])


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    tanggal: str = Query(..., description="Tanggal referensi (YYYY-MM-DD). Bulan & tahun diambil dari sini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Dashboard summary: 9 widget dalam 1 response (Phase 10).

    Widgets:
    1. Laba Rugi (P&L from GL)
    2. Cashflow (Cash Movement)
    3. Beban Biaya (Expense breakdown)
    4. Tren Penjualan (Invoice Turnover — NOT GL Revenue)
    5. Faktur Jatuh Tempo (Remaining outstanding + overdue flag)
    6. Aktivitas Terbaru (Recent posted journals)
    7. Inventory Value (SUM StockBalance.nilai) — Phase 10 NEW
    8. Low Stock (count + items) — Phase 10 NEW
    9. Accounting Health (summary from 11 reconciliation checks) — Phase 10 NEW
    """
    try:
        dt = datetime.strptime(tanggal, "%Y-%m-%d")
        if dt.year < 2:
            raise ValueError('Tanggal referensi tidak mendukung tren enam bulan')
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    bulan = dt.month
    tahun = dt.year

    # Existing 6 widgets
    laba_rugi = laporan_service.get_dashboard_laba_rugi(db, bulan, tahun)
    cashflow = laporan_service.get_dashboard_cashflow(db, bulan, tahun)
    beban_biaya = laporan_service.get_dashboard_beban_biaya(db, bulan, tahun)
    tren_penjualan = laporan_service.get_dashboard_tren_penjualan(db, bulan, tahun)
    # P0-03 (Re-Audit §7): Faktur Jatuh Tempo widget harus hanya menampilkan
    # invoice yang overdue (jatuh_tempo < as_of). Sebelumnya endpoint memanggil
    # tanpa overdue_only=True, sehingga invoice belum jatuh tempo ikut masuk.
    faktur_jt = laporan_service.get_dashboard_faktur_jatuh_tempo(db, dt, overdue_only=True)
    aktivitas = laporan_service.get_dashboard_aktivitas_terbaru(db, dt)

    # Phase 10 — 3 new widgets
    inventory_value = dashboard_service.get_inventory_value_widget(db, dt)
    low_stock = dashboard_service.get_low_stock_widget(db, limit=10)
    accounting_health = dashboard_service.get_accounting_health_summary_widget(db)

    # Phase I — 2 new widgets
    balance_sheet_kpi = dashboard_service.get_balance_sheet_kpi_widget(db, dt)
    margin = dashboard_service.get_margin_widget(db, bulan, tahun)

    return DashboardSummaryResponse(
        laba_rugi=laba_rugi,
        cashflow=cashflow,
        beban_biaya=beban_biaya,
        tren_penjualan=tren_penjualan,
        faktur_jatuh_tempo=faktur_jt,
        aktivitas_terbaru=aktivitas,
        inventory_value=inventory_value,
        low_stock=low_stock,
        accounting_health=accounting_health,
        balance_sheet_kpi=balance_sheet_kpi,
        margin=margin,
    )
