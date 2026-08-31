 """
Aset Tetap Endpoints.
CRUD + status management (hapus, perbaikan, aktifkan kembali).
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.aset_tetap import (
    AsetTetapCreate, AsetTetapUpdate, AsetTetapResponse,
)
from app.services import aset_tetap_service as svc

router = APIRouter()


# ==========================================
# ASET TETAP
# ==========================================

@router.get("", response_model=PaginatedResponse[AsetTetapResponse])
def get_aset_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan kode, nama, catatan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status (AKTIF/DIHAPUSKAN/DALAM_PERBAIKAN)"),
    kategori_aset_id: Optional[UUID] = Query(None, description="Filter kategori aset"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Aset Tetap dengan filter dan pagination."""
    data, total = svc.get_aset_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, kategori_aset_id=kategori_aset_id,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("", response_model=AsetTetapResponse, status_code=status.HTTP_201_CREATED)
def create_aset(
    data_in: AsetTetapCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Aset Tetap baru."""
    try:
        return svc.create_aset(
            db=db,
            kode=data_in.kode,
            nama=data_in.nama,
            kategori_aset_id=data_in.kategori_aset_id,
            akun_aset_id=data_in.akun_aset_id,
            akun_akumulasi_id=data_in.akun_akumulasi_id,
            akun_beban_id=data_in.akun_beban_id,
            tanggal_mulai=data_in.tanggal_mulai,
            kuantitas=data_in.kuantitas,
            nilai_perolehan=data_in.nilai_perolehan,
            catatan=data_in.catatan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{aset_id}", response_model=AsetTetapResponse)
def get_aset_detail(
    aset_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Aset Tetap."""
    item = svc.get_aset_by_id(db, aset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Aset Tetap tidak ditemukan")
    return item


@router.put("/{aset_id}", response_model=AsetTetapResponse)
def update_aset(
    aset_id: UUID,
    data_in: AsetTetapUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Aset Tetap."""
    item = svc.get_aset_by_id(db, aset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Aset Tetap tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_aset(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{aset_id}/hapus", response_model=AsetTetapResponse)
def hapus_aset(
    aset_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus Aset Tetap (soft delete — status ke DIHAPUSKAN)."""
    item = svc.get_aset_by_id(db, aset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Aset Tetap tidak ditemukan")
    try:
        return svc.hapus_aset(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{aset_id}/perbaikan", response_model=AsetTetapResponse)
def set_perbaikan(
    aset_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Set Aset Tetap ke status DALAM_PERBAIKAN."""
    item = svc.get_aset_by_id(db, aset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Aset Tetap tidak ditemukan")
    try:
        return svc.set_perbaikan(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{aset_id}/aktifkan", response_model=AsetTetapResponse)
def aktifkan_kembali(
    aset_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Aktifkan kembali Aset Tetap (dari DALAM_PERBAIKAN ke AKTIF)."""
    item = svc.get_aset_by_id(db, aset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Aset Tetap tidak ditemukan")
    try:
        return svc.aktifkan_kembali(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
