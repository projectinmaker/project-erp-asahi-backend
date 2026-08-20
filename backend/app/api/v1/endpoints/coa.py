from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.akun_perkiraan import HeaderCOA, TingkatAkun
from app.models.master.pengguna import Pengguna
from app.schemas.coa import COACreate, COAUpdate, COAResponse
from app.services import coa_service
from app.schemas.base import PaginatedResponse

router = APIRouter()


@router.get("/", response_model=PaginatedResponse[COAResponse]) # <-- UBAH INI
def read_coa_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    header: Optional[HeaderCOA] = Query(None),
    tingkat: Optional[TingkatAkun] = Query(None),
    search: Optional[str] = Query(None, description="Cari berdasarkan kode atau nama"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Ambil daftar Chart of Accounts (COA) dengan info pagination."""
    data, total = coa_service.get_coa_list(db, skip=skip, limit=limit, header=header, tingkat=tingkat, search=search)

    return {
        "data": data,
        "total": total,
        "skip": skip,
        "limit": limit
    }


@router.post("/", response_model=COAResponse, status_code=status.HTTP_201_CREATED)
def create_coa(
    coa_in: COACreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),  # <-- DITAMBAHKAN
):
    """Tambah Akun Perkiraan (COA) baru."""
    existing = coa_service.get_coa_by_kode(db, kode=coa_in.kode)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kode akun '{coa_in.kode}' sudah terdaftar.",
        )
    return coa_service.create_coa(db, coa_in=coa_in)


@router.get("/{coa_id}", response_model=COAResponse)
def read_coa_detail(
    coa_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),  # <-- DITAMBAHKAN
):
    """Ambil detail 1 Akun Perkiraan berdasarkan ID."""
    coa = coa_service.get_coa_by_id(db, coa_id=coa_id)
    if not coa:
        raise HTTPException(status_code=404, detail="Akun Perkiraan tidak ditemukan")
    return coa


@router.put("/{coa_id}", response_model=COAResponse)
def update_coa(
    coa_id: UUID,
    coa_in: COAUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),  # <-- DITAMBAHKAN
):
    """Update data Akun Perkiraan."""
    coa = coa_service.get_coa_by_id(db, coa_id=coa_id)
    if not coa:
        raise HTTPException(status_code=404, detail="Akun Perkiraan tidak ditemukan")
    return coa_service.update_coa(db, db_obj=coa, obj_in=coa_in)
