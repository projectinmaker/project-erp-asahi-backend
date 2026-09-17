"""
Penutupan Periode Endpoints.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.penutupan_periode import (
    PenutupanPeriodeResponse,
    TutupPeriodeRequest,
    BukaPeriodeRequest,
)
from app.services import penutupan_periode_service as svc

router = APIRouter()


@router.get("/penutupan-periode", response_model=PaginatedResponse[PenutupanPeriodeResponse])
def get_periode_list(
    tahun: Optional[int] = Query(None, description="Filter tahun"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status (DITUTUP/DIBUKA)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar penutupan periode."""
    data, total = svc.get_periode_list(
        db, tahun=tahun, status=status_filter, skip=skip, limit=limit
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.get("/penutupan-periode/status", response_model=dict)
def check_periode_status(
    tahun: int = Query(..., description="Tahun"),
    bulan: int = Query(..., description="Bulan (1-12)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Cek status penutupan suatu periode."""
    return svc.get_periode_status(db, tahun=tahun, bulan=bulan)


@router.post("/penutupan-periode/tutup", response_model=PenutupanPeriodeResponse)
def tutup_periode(
    data_in: TutupPeriodeRequest,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Tutup periode (lock + opsional jurnal penutupan)."""
    try:
        return svc.tutup_periode(
            db=db,
            tahun=data_in.tahun,
            bulan=data_in.bulan,
            user_id=current_user.id,
            keterangan=data_in.keterangan,
            with_closing_entry=data_in.with_closing_entry,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/penutupan-periode/buka", response_model=PenutupanPeriodeResponse)
def buka_periode(
    data_in: BukaPeriodeRequest,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buka kembali periode yang sudah ditutup.

    Phase 8 — Master Roadmap §25:
        - "Reopen reason + authorization" — alasan wajib diisi, user harus manajer/admin
        - "Sequential reopen" — tidak boleh reopen periode lama kalau periode setelahnya masih DITUTUP

    Hanya manajer/admin yang bisa reopen. Kalau user biasa, akan return 403.
    """
    try:
        return svc.buka_periode(
            db=db,
            tahun=data_in.tahun,
            bulan=data_in.bulan,
            user_id=current_user.id,
            alasan=data_in.alasan,
            user=current_user,  # ← Phase 8: pass user untuk authorization check
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        # HTTPException from _validate_reopen_authorization (403) perlu pass-through
        if hasattr(e, 'status_code') and e.status_code == 403:
            raise
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ============================================================
# PHASE 8 — PRE-CLOSE READINESS & GL RECONCILIATION
# ============================================================

@router.get("/penutupan-periode/pre-close-readiness")
def get_pre_close_readiness(
    tahun: int = Query(..., description="Tahun periode yang akan ditutup"),
    bulan: int = Query(..., ge=1, le=12, description="Bulan (1-12)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Cek readiness sebelum menutup periode (Master Roadmap §25: "Pre-close readiness").

    Check yang dilakukan:
    1. Trial Balance balanced (debit = kredit)
    2. Balance Sheet balanced (Aset = Kewajiban + Ekuitas)
    3. Perubahan Modal reconciled
    4. Arus Kas reconciled
    5. Tidak ada jurnal DRAFT yang belum diposting
    6. Tidak ada jurnal tidak valid (unbalanced / akun non-DETAIL / dll)
    7. Periode sebelumnya sudah ditutup (sequential closing)
    8. Periode ini belum ditutup

    Response:
    {
        "ready": true | false,
        "periode": {"tahun": 2026, "bulan": 8},
        "checks": {
            "trial_balance_balanced": true,
            "trial_balance_ending_balanced": true,
            "balance_sheet_balanced": true,
            "equity_reconciled": true,
            "cash_flow_reconciled": true,
            "no_draft_journals": true,
            "no_invalid_journals": true,
            "previous_period_closed": true,
            "period_not_already_closed": true
        },
        "blocking_issues": [],
        "detail": {
            "report_validation": { ... },
            "draft_journals_count": 0,
            "invalid_journals_count": 0,
            "previous_period": {"tahun": 2026, "bulan": 7, "closed": true}
        }
    }

    Kalau ready=false, frontend tampilkan blocking_issues dan user harus
    fix issue tersebut sebelum bisa tutup periode.
    """
    return svc.get_pre_close_readiness(db, tahun, bulan)


@router.get("/penutupan-periode/gl-reconciliation")
def get_gl_reconciliation(
    tahun: int = Query(..., description="Tahun periode"),
    bulan: int = Query(..., ge=1, le=12, description="Bulan (1-12)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """GL reconciliation check — Trial Balance Debit = Credit (Master Roadmap §25).

    Membandingkan:
    - Total Debit vs Total Kredit dari semua jurnal POSTED di periode tsb
    - Total saldo debit vs total saldo kredit (ending balance)
    - Persamaan Neraca (Aset = Kewajiban + Ekuitas)

    Response:
    {
        "periode": {"tahun": 2026, "bulan": 8},
        "trial_balance": {
            "total_debit": "12345678.00",
            "total_kredit": "12345678.00",
            "selisih_mutasi": "0.00",
            "total_saldo_debit": "9876543.00",
            "total_saldo_kredit": "9876543.00",
            "selisih_saldo": "0.00"
        },
        "balance_sheet": {
            "total_aset": "5000000.00",
            "total_kewajiban": "2000000.00",
            "total_ekuitas": "3000000.00",
            "selisih": "0.00"
        },
        "reconciliation_status": "MATCH" | "MISMATCH"
    }
    """
    from app.services.laporan_service import get_neraca_saldo, get_neraca
    from app.services.reporting_ledger import month_bounds

    date_from, date_to = month_bounds(tahun, bulan)

    # Trial Balance
    tb = get_neraca_saldo(db, date_from, date_to)
    # Balance Sheet (as of end of period)
    bs = get_neraca(db, date_to)

    # Reconciliation status
    tb_mutasi_match = tb['selisih'] == 0
    tb_saldo_match = (tb['total_saldo_debit'] - tb['total_saldo_kredit']) == 0
    bs_match = bs['selisih'] == 0
    reconciliation_status = 'MATCH' if (tb_mutasi_match and tb_saldo_match and bs_match) else 'MISMATCH'

    return {
        'periode': {'tahun': tahun, 'bulan': bulan},
        'trial_balance': {
            'total_debit': str(tb['total_debit']),
            'total_kredit': str(tb['total_kredit']),
            'selisih_mutasi': str(tb['selisih']),
            'total_saldo_debit': str(tb['total_saldo_debit']),
            'total_saldo_kredit': str(tb['total_saldo_kredit']),
            'selisih_saldo': str(tb['total_saldo_debit'] - tb['total_saldo_kredit']),
            'mutasi_match': tb_mutasi_match,
            'saldo_match': tb_saldo_match,
        },
        'balance_sheet': {
            'total_aset': str(bs.get('total_aset', 0)),
            'total_kewajiban': str(bs.get('total_kewajiban', 0)),
            'total_ekuitas': str(bs.get('total_ekuitas', 0)),
            'selisih': str(bs['selisih']),
            'match': bs_match,
        },
        'reconciliation_status': reconciliation_status,
    }
