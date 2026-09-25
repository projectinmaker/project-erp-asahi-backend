from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.laporan import (
    NeracaSaldoResponse,
    PerubahanModalResponse,
    LabaRugiResponse,
    NeracaResponse,
    ArusKasResponse,
    BukuBesarResponse,
    MutasiKasBankResponse,
    RekapKasBankResponse,
    UmurPiutangResponse,
    UmurHutangResponse,
    ReportValidationResponse,
    RekonsiliasiPersediaanResponse,
    RekonsiliasiPersediaanRingkasanResponse,
    AuditTransaksiPersediaanResponse,
)
from app.services import laporan_service
from app.services import rekonsiliasi_persediaan_service
from app.services.reporting_ledger import day_start, day_end

from app.api.reporting import report_scope

router = APIRouter(dependencies=[Depends(report_scope)])


# ==========================================
# LAPORAN KEUANGAN
# ==========================================

@router.get("/neraca-saldo", response_model=NeracaSaldoResponse)
def get_neraca_saldo(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Neraca Saldo (Trial Balance) — verifikasi Debit = Kredit."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_neraca_saldo(db, date_from, date_to)


@router.get("/perubahan-modal", response_model=PerubahanModalResponse)
def get_perubahan_modal(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Perubahan Modal (Statement of Changes in Equity)."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_perubahan_modal(db, date_from, date_to)


@router.get("/laba-rugi", response_model=LabaRugiResponse)
def get_laba_rugi(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Laba Rugi."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_laba_rugi(db, date_from, date_to)


@router.get("/neraca", response_model=NeracaResponse)
def get_neraca(
    tanggal: str = Query(..., description="Tanggal posisi neraca (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Neraca (posisi keuangan)."""
    try:
        dt = day_end(datetime.strptime(tanggal, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_neraca(db, dt)


@router.get("/arus-kas", response_model=ArusKasResponse)
def get_arus_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Arus Kas."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_arus_kas(db, date_from, date_to)


# ==========================================
# UMUR PIUTANG / HUTANG
# ==========================================

@router.get("/umur-piutang", response_model=UmurPiutangResponse)
def get_umur_piutang(
    as_of: str = Query(..., description="Tanggal posisi (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Umur Piutang (Aging Receivables)."""
    try:
        dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_umur_piutang(db, dt)


@router.get("/umur-hutang", response_model=UmurHutangResponse)
def get_umur_hutang(
    as_of: str = Query(..., description="Tanggal posisi (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Umur Hutang (Aging Payables)."""
    try:
        dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_umur_hutang(db, dt)


# ==========================================
# BUKU BESAR
# ==========================================

@router.get("/buku-besar", response_model=BukuBesarResponse)
def get_buku_besar(
    akun_id: UUID = Query(..., description="ID Akun Perkiraan"),
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rincian Buku Besar per akun."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    try:
        return laporan_service.get_buku_besar(db, akun_id, date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==========================================
# KAS & BANK
# ==========================================

@router.get("/mutasi-kas", response_model=MutasiKasBankResponse)
def get_mutasi_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Mutasi Kas."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="KAS")


@router.get("/mutasi-bank", response_model=MutasiKasBankResponse)
def get_mutasi_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Mutasi Bank."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="BANK")


@router.get("/rekap-kas-bank", response_model=RekapKasBankResponse)
def get_rekap_kas_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekap Kas & Bank (saldo awal, masuk, keluar, akhir per akun)."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_rekap_kas_bank(db, date_from, date_to)


@router.get('/validasi', response_model=ReportValidationResponse)
def validate_financial_reports(dari: date, sampai: date, db=Depends(get_current_db)):
    if dari > sampai:
        raise HTTPException(400, 'Tanggal dari tidak boleh setelah sampai')
    return laporan_service.validate_reports(db, day_start(dari), day_end(sampai))


# ==========================================
# Tahap 3 — Rekonsiliasi Persediaan vs Buku Besar
# ==========================================

@router.get("/rekonsiliasi-persediaan", response_model=RekonsiliasiPersediaanResponse)
def get_rekonsiliasi_persediaan(
    as_of: Optional[str] = Query(None, description="Tanggal hari ini (YYYY-MM-DD); tanggal historis belum tersedia. Saldo terkini mencakup semua jurnal POSTED."),
    only_mismatch: bool = Query(False, description="Hanya tampilkan akun/barang yang selisihnya != 0."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekonsiliasi saldo stok terkini (seluruh gudang/organisasi) dan seluruh jurnal POSTED.

    Termasuk jurnal bertanggal masa depan yang telah diposting. Tanggal historis
    dan filter organisasi ditolak karena stok belum memiliki snapshot tersebut.
    Mapping barang diprioritaskan; fallback kategori mengikuti resolver posting.
    """
    from datetime import datetime
    as_of_dt = None
    if as_of:
        try:
            as_of_dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")
    return rekonsiliasi_persediaan_service.get_rekonsiliasi_persediaan(
        db, as_of=as_of_dt, only_mismatch=only_mismatch
    )


@router.get("/rekonsiliasi-persediaan/ringkasan", response_model=RekonsiliasiPersediaanRingkasanResponse)
def get_ringkasan_rekonsiliasi_persediaan(
    as_of: Optional[str] = Query(None, description="Tanggal hari ini (YYYY-MM-DD); tanggal historis belum tersedia. Saldo terkini mencakup semua jurnal POSTED."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ringkasan cepat rekonsiliasi persediaan (untuk dashboard / alerting)."""
    from datetime import datetime
    as_of_dt = None
    if as_of:
        try:
            as_of_dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")
    return rekonsiliasi_persediaan_service.get_ringkasan_rekonsiliasi(db, as_of=as_of_dt)


@router.get("/audit-persediaan", response_model=AuditTransaksiPersediaanResponse)
def get_audit_persediaan(
    dari: Optional[str] = Query(None, description="Tanggal awal (YYYY-MM-DD). Default: 2000-01-01."),
    sampai: Optional[str] = Query(None, description="Tanggal akhir (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Audit trail transaksi persediaan: penerimaan, pengiriman, retur, penyesuaian.

    Verifikasi bahwa setiap transaksi yang mengubah stok juga menghasilkan
    jurnal Persediaan yang sesuai. Anomali = transaksi dengan nilai > 0
    tapi tidak ada jurnal (cek auto_post_jurnal atau error posting).
    """
    from datetime import datetime
    date_from = None
    date_to = None
    if dari:
        try:
            date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format dari harus YYYY-MM-DD")
    if sampai:
        try:
            date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format sampai harus YYYY-MM-DD")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    return rekonsiliasi_persediaan_service.audit_transaksi_persediaan(
        db, date_from=date_from, date_to=date_to
    )


# ==========================================
# PHASE 9 — FINANCIAL RECONCILIATION & ACCOUNTING HEALTH
# ==========================================

@router.get("/rekonsiliasi/grni")
def get_grni_reconciliation_endpoint(
    as_of: Optional[str] = Query(None, description="Tanggal as-of (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekonsiliasi Open GRNI vs GRNI GL (Master Roadmap §26).

    Membandingkan:
    - Open GRNI = sum penerimaan barang SELESAI yang belum di-invoice
    - GRNI GL = saldo akun PENERIMAAN_DALAM_PROSES dari POSTED journals

    Selisih harus 0 (tolerance 1 cent).
    """
    from app.services.financial_reconciliation_service import get_grni_reconciliation
    from datetime import datetime

    as_of_dt = None
    if as_of:
        try:
            as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")

    return get_grni_reconciliation(db, as_of_dt)


@router.get("/rekonsiliasi/cashflow-vs-bs")
def get_cashflow_vs_bs_reconciliation_endpoint(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekonsiliasi Cash Flow Ending Cash vs Balance Sheet Cash (Master Roadmap §26).

    Membandingkan:
    - Cash Flow Ending = saldo akhir kas/bank dari laporan arus kas
    - Balance Sheet Cash = total saldo akun kas/bank di neraca per tanggal akhir

    Selisih harus 0.
    """
    from app.services.financial_reconciliation_service import (
        get_cash_flow_vs_balance_sheet_reconciliation,
    )
    from datetime import datetime

    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(400, "Format tanggal harus YYYY-MM-DD")

    return get_cash_flow_vs_balance_sheet_reconciliation(db, date_from, date_to)


@router.get("/rekonsiliasi/equity-vs-bs")
def get_equity_vs_bs_reconciliation_endpoint(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekonsiliasi Changes in Equity Closing vs Balance Sheet Equity (Master Roadmap §26).

    Membandingkan:
    - Equity Closing = total modal akhir dari laporan perubahan modal
    - Balance Sheet Equity = total ekuitas di neraca per tanggal akhir

    Selisih harus 0.
    """
    from app.services.financial_reconciliation_service import (
        get_equity_vs_balance_sheet_reconciliation,
    )
    from datetime import datetime

    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(400, "Format tanggal harus YYYY-MM-DD")

    return get_equity_vs_balance_sheet_reconciliation(db, date_from, date_to)


@router.get("/accounting-health")
def get_accounting_health_endpoint(
    as_of: Optional[str] = Query(None, description="Tanggal as-of (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Accounting Health Dashboard — aggregate semua reconciliation status (Master Roadmap §27).

    Menampilkan status MATCH/MISMATCH untuk 11 reconciliation checks:
    1. Trial Balance (Debit = Credit)
    2. Balance Sheet (Assets = Liabilities + Equity)
    3. AR Aging = AR GL
    4. AP Aging = AP GL
    5. Inventory Valuation = Inventory GL
    6. Fixed Asset Register = FA GL
    7. Cash/Bank Ledger = Cash/Bank GL
    8. Open GRNI = GRNI GL
    9. Cash Flow Ending = Balance Sheet Cash
    10. Changes in Equity Closing = Balance Sheet Equity
    11. Cash Flow Movement (Opening + Change - Ending = 0)

    Response (camelCase — kontrak frontend reports/accounting-health.tsx):
    {
        "asOf": "2026-09-15T...",
        "overallStatus": "HEALTHY" | "ISSUES_FOUND",
        "reconciliations": [
            {"name": "Trial Balance", "status": "MATCH", "selisih": "0.00", "detail": "..."},
            ...
        ],
        "summary": {
            "totalChecks": 11,
            "matchCount": 10,
            "mismatchCount": 1,
            "notConfiguredCount": 0
        }
    }
    """
    from app.services.accounting_health_service import get_accounting_health
    from datetime import datetime

    as_of_dt = None
    if as_of:
        try:
            as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")

    return get_accounting_health(db, as_of_dt)
