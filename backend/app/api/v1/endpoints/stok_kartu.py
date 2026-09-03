from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.base import PaginatedResponse
from app.schemas.stok_kartu import (
    StokKartuEntryResponse,
    StokKartuSummaryResponse,
    StokKartuLayerInfo,
    MetodeValuasiOption,
)
from app.services import stok_kartu_service as sk_svc


router = APIRouter()


# ==========================================
# GET /stok-kartu/
# ==========================================

@router.get("/stok-kartu/", response_model=PaginatedResponse[StokKartuEntryResponse])
def get_stok_kartu(
    barang_id: UUID = Query(..., description="UUID barang"),
    gudang_id: Optional[UUID] = Query(None, description="Filter gudang (opsional)"),
    date_from: Optional[datetime] = Query(None, description="Filter tanggal mulai"),
    date_to: Optional[datetime] = Query(None, description="Filter tanggal akhir"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Ambil kartu stok (stock card) untuk satu barang.

    Menampilkan riwayat mutasi stok lengkap dengan valuasi per baris.
    """
    try:
        entries, total = sk_svc.get_stok_kartu(
            db=db,
            barang_id=barang_id,
            gudang_id=gudang_id,
            date_from=date_from,
            date_to=date_to,
            skip=skip,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return PaginatedResponse(
        data=entries,
        total=total,
        skip=skip,
        limit=limit,
    )


# ==========================================
# GET /stok-kartu/summary
# ==========================================

@router.get("/stok-kartu/summary", response_model=StokKartuSummaryResponse)
def get_stok_kartu_summary(
    barang_id: UUID = Query(..., description="UUID barang"),
    gudang_id: Optional[UUID] = Query(None, description="Filter gudang (opsional)"),
    db: Session = Depends(get_db),
):
    """Ringkasan posisi stok saat ini beserta nilai dan detail layer (FIFO/FEFO)."""
    try:
        summary = sk_svc.get_stok_kartu_summary(
            db=db,
            barang_id=barang_id,
            gudang_id=gudang_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return summary


# ==========================================
# GET /stok-kartu/valuasi-options
# ==========================================

@router.get("/stok-kartu/valuasi-options", response_model=list[MetodeValuasiOption])
def get_valuasi_options():
    """List metode valuasi yang tersedia."""
    return [MetodeValuasiOption(**opt) for opt in sk_svc.VALUASI_OPTIONS]


# ==========================================
# POST /stok-kartu/rekalkulasi
# ==========================================

@router.post("/stok-kartu/rekalkulasi")
def rekalkulasi_stok_kartu(
    barang_id: UUID = Query(..., description="UUID barang yang direkalkulasi"),
    db: Session = Depends(get_db),
):
    """Rekalkulasi ulang kartu stok & layer FIFO/FEFO untuk satu barang.

    Menghapus semua layer lama, lalu memproses ulang seluruh riwayat mutasi.
    Gunakan jika data kartu stok tidak konsisten.
    """
    try:
        result = sk_svc.rekalkulasi_stok_kartu(db=db, barang_id=barang_id)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Rekalkulasi gagal: {str(e)}")

    return result
