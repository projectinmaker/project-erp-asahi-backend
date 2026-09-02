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
    """Buka kembali periode yang sudah ditutup."""
    try:
        return svc.buka_periode(
            db=db,
            tahun=data_in.tahun,
            bulan=data_in.bulan,
            user_id=current_user.id,
            alasan=data_in.alasan,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
