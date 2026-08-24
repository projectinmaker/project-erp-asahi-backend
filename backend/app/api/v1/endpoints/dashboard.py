from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.dashboard import DashboardSummaryResponse
from app.services import laporan_service

router = APIRouter()


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    tanggal: str = Query(..., description="Tanggal referensi (YYYY-MM-DD). Bulan & tahun diambil dari sini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Dashboard summary: 6 widget dalam 1 response."""
    try:
        dt = datetime.strptime(tanggal, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    bulan = dt.month
    tahun = dt.year

    laba_rugi = laporan_service.get_dashboard_laba_rugi(db, bulan, tahun)
    cashflow = laporan_service.get_dashboard_cashflow(db, bulan, tahun)
    beban_biaya = laporan_service.get_dashboard_beban_biaya(db, bulan, tahun)
    tren_penjualan = laporan_service.get_dashboard_tren_penjualan(db, bulan, tahun)
    faktur_jt = laporan_service.get_dashboard_faktur_jatuh_tempo(db)
    aktivitas = laporan_service.get_dashboard_aktivitas_terbaru(db)

    return DashboardSummaryResponse(
        laba_rugi=laba_rugi,
        cashflow=cashflow,
        beban_biaya=beban_biaya,
        tren_penjualan=tren_penjualan,
        faktur_jatuh_tempo=faktur_jt,
        aktivitas_terbaru=aktivitas,
    )
