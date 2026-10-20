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
    company_id: Optional[str] = Query(None, alias="companyId", description="AUD-17: scope perusahaan (global bila kosong)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar penutupan periode (AUD-17: filter scope perusahaan)."""
    data, total = svc.get_periode_list(
        db, tahun=tahun, status=status_filter, skip=skip, limit=limit,
        company_id=_uuid_or_none(company_id),
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


def _uuid_or_none(value: Optional[str]):
    if not value:
        return None
    from uuid import UUID as _UUID
    try:
        return _UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="companyId tidak valid")


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
    """Tutup periode (lock + opsional jurnal penutupan).

    AUD-17: body boleh membawa companyId → tutup khusus perusahaan tsb
    (bila kosong = global, mengunci semua perusahaan).
    """
    try:
        return svc.tutup_periode(
            db=db,
            tahun=data_in.tahun,
            bulan=data_in.bulan,
            user_id=current_user.id,
            keterangan=data_in.keterangan,
            with_closing_entry=data_in.with_closing_entry,
            company_id=getattr(data_in, "company_id", None),
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
            company_id=getattr(data_in, "company_id", None),
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

    Response (camelCase — kontrak wire frontend penutupan-periode.tsx):
    {
        "ready": true | false,
        "periode": {"tahun": 2026, "bulan": 8},
        "checks": {
            "trialBalanceBalanced": true,
            "trialBalanceEndingBalanced": true,
            "balanceSheetBalanced": true,
            "equityReconciled": true,
            "cashFlowReconciled": true,
            "noDraftJournals": true,
            "noInvalidJournals": true,
            "previousPeriodClosed": true,
            "periodNotAlreadyClosed": true
        },
        "blockingIssues": [],
        "detail": {
            "reportValidation": { ... },
            "draftJournalsCount": 0,
            "invalidJournalsCount": 0,
            "previousPeriod": {"tahun": 2026, "bulan": 7, "closed": true}
        }
    }

    Kalau ready=false, frontend tampilkan blockingIssues dan user harus
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

    Response (camelCase — kontrak wire frontend penutupan-periode.tsx):
    {
        "periode": {"tahun": 2026, "bulan": 8},
        "trialBalance": {
            "totalDebit": "12345678.00",
            "totalKredit": "12345678.00",
            "selisihMutasi": "0.00",
            "totalSaldoDebit": "9876543.00",
            "totalSaldoKredit": "9876543.00",
            "selisihSaldo": "0.00",
            "mutasiMatch": true,
            "saldoMatch": true
        },
        "balanceSheet": {
            "totalAset": "5000000.00",
            "totalKewajiban": "2000000.00",
            "totalEkuitas": "3000000.00",
            "selisih": "0.00",
            "match": true
        },
        "reconciliationStatus": "MATCH" | "MISMATCH"
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
        'trialBalance': {
            'totalDebit': str(tb['total_debit']),
            'totalKredit': str(tb['total_kredit']),
            'selisihMutasi': str(tb['selisih']),
            'totalSaldoDebit': str(tb['total_saldo_debit']),
            'totalSaldoKredit': str(tb['total_saldo_kredit']),
            'selisihSaldo': str(tb['total_saldo_debit'] - tb['total_saldo_kredit']),
            'mutasiMatch': tb_mutasi_match,
            'saldoMatch': tb_saldo_match,
        },
        'balanceSheet': {
            'totalAset': str(bs.get('total_aset', 0)),
            'totalKewajiban': str(bs.get('total_kewajiban', 0)),
            'totalEkuitas': str(bs.get('total_ekuitas', 0)),
            'selisih': str(bs['selisih']),
            'match': bs_match,
        },
        'reconciliationStatus': reconciliation_status,
    }


# ============================================================
# AUD-14 (update #04) — CLOSING TAHUNAN
# Transfer laba berjalan (322000) → laba ditahan (321000).
# ============================================================
def _tahun_payload(rec) -> dict:
    """Serialisasi record PenutupanTahun (tanpa response_model ORM)."""
    from datetime import timezone as _tz
    def _iso(dt):
        return dt.isoformat() if dt else None
    return {
        "tahun": rec.tahun, "status": rec.status, "labaRugi": str(rec.laba_rugi) if rec.laba_rugi is not None else None,
        "keterangan": rec.keterangan, "jurnalPenutupanId": str(rec.jurnal_penutupan_id) if rec.jurnal_penutupan_id else None,
        "companyId": str(rec.company_id) if getattr(rec, "company_id", None) else None,
        "closedAt": _iso(rec.closed_at), "reopenedAt": _iso(rec.reopened_at),
        "reopenAlasan": rec.reopen_alasan,
    }
@router.get("/penutupan-periode/tahun")
def get_tahun_list(
    company_id: Optional[str] = Query(None, alias="companyId", description="AUD-17: scope perusahaan"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar tahun buku + status penutupan tahun (AUD-14; AUD-17 scope company)."""
    return svc.get_tahun_list(db, company_id=_uuid_or_none(company_id))


@router.post("/penutupan-periode/tutup-tahun/{tahun}")
def tutup_tahun(
    tahun: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Tutup tahun buku: transfer saldo Laba (Rugi) Tahun Berjalan → Laba
    Ditahan (jurnal CL-YEAR-{tahun}, dikecualikan dari laporan laba rugi).

    Syarat: tahun sudah berlalu + 12 bulan DITUTUP. Payload opsional:
    {"keterangan": "...", "withClosingEntry": true} (default true).
    """
    payload = payload or {}
    try:
        rec = svc.tutup_tahun(
            db=db,
            tahun=tahun,
            user_id=current_user.id,
            keterangan=payload.get("keterangan"),
            with_closing_entry=payload.get("withClosingEntry", True),
            company_id=_uuid_or_none(payload.get("companyId")),
        )
        return _tahun_payload(rec)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/penutupan-periode/buka-tahun/{tahun}")
def buka_tahun(
    tahun: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buka kembali tahun buku (reversal jurnal transfer + alasan wajib +
    hanya manajer/admin). Bulan tetap terkunci — buka bulan lewat endpoint
    /penutupan-periode/buka setelah tahun dibuka."""
    payload = payload or {}
    try:
        rec = svc.buka_tahun(
            db=db,
            tahun=tahun,
            user_id=current_user.id,
            alasan=payload.get("alasan"),
            user=current_user,
            company_id=_uuid_or_none(payload.get("companyId")),
        )
        return _tahun_payload(rec)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 403:
            raise
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
