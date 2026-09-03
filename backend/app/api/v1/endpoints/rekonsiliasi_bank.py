"""
Rekonsiliasi Bank Endpoints.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.rekonsiliasi_bank import (
    RekonsiliasiBankCreate,
    RekonsiliasiBankUpdate,
    RekonsiliasiBankResponse,
    RekonsiliasiDetailCreate,
    RekonsiliasiDetailUpdate,
    RekonsiliasiDetailResponse,
    SaldoBukuPreviewResponse,
)
from app.services import rekonsiliasi_bank_service as svc

router = APIRouter()


# ==========================================
# LIST & DETAIL
# ==========================================

@router.get("/rekonsiliasi-bank", response_model=PaginatedResponse[RekonsiliasiBankResponse])
def get_rekonsiliasi_list(
    kas_bank_akun_id: Optional[UUID] = Query(None, description="Filter kas/bank"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar rekonsiliasi bank."""
    data, total = svc.get_rekonsiliasi_list(
        db, kas_bank_akun_id=kas_bank_akun_id, status=status_filter,
        skip=skip, limit=limit,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.get("/rekonsiliasi-bank/{rekonsiliasi_id}", response_model=RekonsiliasiBankResponse)
def get_rekonsiliasi_detail(
    rekonsiliasi_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 rekonsiliasi bank dengan semua detail lines."""
    item = svc.get_rekonsiliasi_by_id(db, rekonsiliasi_id)
    if not item:
        raise HTTPException(status_code=404, detail="Rekonsiliasi Bank tidak ditemukan")
    return item


# ==========================================
# PREVIEW SALDO BUKU
# ==========================================

@router.get("/rekonsiliasi-bank/preview-saldo-buku", response_model=SaldoBukuPreviewResponse)
def preview_saldo_buku(
    kas_bank_akun_id: UUID = Query(..., description="ID Kas/Bank"),
    tanggal_akhir: datetime = Query(..., description="Tanggal akhir (bank statement date)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Preview saldo buku tanpa membuat rekonsiliasi. Digunakan frontend sebelum create."""
    try:
        from app.models.master.kas_bank_akun import KasBankAkun
        kb = db.query(KasBankAkun).filter(KasBankAkun.id == kas_bank_akun_id).first()
        if not kb:
            raise HTTPException(status_code=404, detail="Kas/Bank tidak ditemukan")

        saldo_buku = svc.compute_saldo_buku(db, kas_bank_akun_id, tanggal_akhir)
        return SaldoBukuPreviewResponse(
            kas_bank_akun_id=kas_bank_akun_id,
            kas_bank_nama=kb.nama,
            tanggal_akhir=tanggal_akhir,
            saldo_buku=saldo_buku,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ==========================================
# CREATE HEADER
# ==========================================

@router.post("/rekonsiliasi-bank", response_model=RekonsiliasiBankResponse, status_code=status.HTTP_201_CREATED)
def create_rekonsiliasi(
    data_in: RekonsiliasiBankCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat rekonsiliasi bank baru (DRAFT)."""
    try:
        return svc.create_rekonsiliasi(
            db=db,
            kas_bank_akun_id=data_in.kas_bank_akun_id,
            tanggal_akhir=data_in.tanggal_akhir,
            saldo_bank=data_in.saldo_bank,
            user_id=current_user.id,
            keterangan=data_in.keterangan,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ==========================================
# UPDATE HEADER
# ==========================================

@router.put("/rekonsiliasi-bank/{rekonsiliasi_id}", response_model=RekonsiliasiBankResponse)
def update_rekonsiliasi(
    rekonsiliasi_id: UUID,
    data_in: RekonsiliasiBankUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update header rekonsiliasi (hanya DRAFT)."""
    item = svc.get_rekonsiliasi_by_id(db, rekonsiliasi_id)
    if not item:
        raise HTTPException(status_code=404, detail="Rekonsiliasi Bank tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_rekonsiliasi(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ==========================================
# DETAIL CRUD
# ==========================================

@router.post("/rekonsiliasi-bank/{rekonsiliasi_id}/detail", response_model=RekonsiliasiDetailResponse, status_code=status.HTTP_201_CREATED)
def add_detail(
    rekonsiliasi_id: UUID,
    data_in: RekonsiliasiDetailCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Tambah detail line ke rekonsiliasi (hanya DRAFT)."""
    try:
        return svc.add_detail(
            db=db,
            rekonsiliasi_id=rekonsiliasi_id,
            tipe=data_in.tipe,
            keterangan=data_in.keterangan,
            jumlah=data_in.jumlah,
            sisi=data_in.sisi,
            akun_perkiraan_id=data_in.akun_perkiraan_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/rekonsiliasi-bank/{rekonsiliasi_id}/detail/{detail_id}", response_model=RekonsiliasiDetailResponse)
def update_detail(
    rekonsiliasi_id: UUID,
    detail_id: UUID,
    data_in: RekonsiliasiDetailUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update detail line (hanya DRAFT)."""
    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_detail(db, detail_id=detail_id, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/rekonsiliasi-bank/{rekonsiliasi_id}/detail/{detail_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_detail(
    rekonsiliasi_id: UUID,
    detail_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus detail line (hanya DRAFT)."""
    try:
        svc.remove_detail(db, detail_id=detail_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ==========================================
# COMPLETE & VOID
# ==========================================

@router.post("/rekonsiliasi-bank/{rekonsiliasi_id}/selesai", response_model=RekonsiliasiBankResponse)
def complete_rekonsiliasi(
    rekonsiliasi_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Selesaikan rekonsiliasi (DRAFT → SELESAI). Validasi balance & post jurnal penyesuaian."""
    try:
        return svc.complete_rekonsiliasi(
            db=db, rekonsiliasi_id=rekonsiliasi_id, user_id=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/rekonsiliasi-bank/{rekonsiliasi_id}/batal", response_model=RekonsiliasiBankResponse)
def void_rekonsiliasi(
    rekonsiliasi_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan rekonsiliasi (DRAFT/SELESAI → BATAL)."""
    try:
        return svc.void_rekonsiliasi(db, rekonsiliasi_id=rekonsiliasi_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
