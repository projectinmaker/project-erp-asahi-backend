"""Produksi Endpoints — Request Order Part (form pre-PO dari Engineering).

Update ASAHI #003: endpoint approve diganti /buat (workflow tanpa
persetujuan — lihat buat_request_order_part)."""

from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.produksi import (
    RequestOrderPartCreate,
    RequestOrderPartUpdate,
    RequestOrderPartResponse,
    RequestOrderPartListResponse,
)
from app.services import produksi_service as svc

router = APIRouter()


# ==========================================
# REQUEST ORDER PART
# ==========================================

@router.get("/request-order-part", response_model=PaginatedResponse[RequestOrderPartListResponse])
def get_request_order_part_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari no ROP, project, model, no SO, nama customer"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status (DRAFT/DIBUAT)"),
    sales_order_id: Optional[UUID] = Query(None, description="Filter per Sales Order"),
    tanggal_from: Optional[datetime] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[datetime] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Request Order Part dengan filter dan pagination."""
    try:
        data, total = svc.get_request_order_part_list(
            db, skip=skip, limit=limit, search=search,
            status=status_filter, sales_order_id=sales_order_id,
            tanggal_from=tanggal_from, tanggal_to=tanggal_to,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/request-order-part", response_model=RequestOrderPartResponse, status_code=status.HTTP_201_CREATED)
def create_request_order_part(
    data_in: RequestOrderPartCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Request Order Part baru (auto-generate no "ROP ASI/YYYY/NNN").

    Customer ditarik otomatis dari Sales Order yang dipilih.
    """
    try:
        return svc.create_request_order_part(
            db=db,
            tanggal=data_in.tanggal,
            project_name=data_in.project_name,
            sales_order_id=data_in.sales_order_id,
            items=[i.model_dump() for i in data_in.items],
            created_by=current_user.id,
            model=data_in.model,
            catatan=data_in.catatan,
            dibuat_oleh=data_in.dibuat_oleh,
            diperiksa_oleh=data_in.diperiksa_oleh,
            diketahui_oleh=data_in.diketahui_oleh,
            disetujui_oleh=data_in.disetujui_oleh,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/request-order-part/{rop_id}", response_model=RequestOrderPartResponse)
def get_request_order_part(
    rop_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Request Order Part (lengkap dengan items)."""
    item = svc.get_request_order_part_by_id(db, rop_id)
    if not item:
        raise HTTPException(status_code=404, detail="Request Order Part tidak ditemukan")
    return item


@router.put("/request-order-part/{rop_id}", response_model=RequestOrderPartResponse)
def update_request_order_part(
    rop_id: UUID,
    data_in: RequestOrderPartUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update Request Order Part (hanya status DRAFT)."""
    item = svc.get_request_order_part_by_id(db, rop_id)
    if not item:
        raise HTTPException(status_code=404, detail="Request Order Part tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    items = update_data.pop("items", None)
    try:
        return svc.update_request_order_part(db, db_obj=item, items=items, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/request-order-part/{rop_id}/buat", response_model=RequestOrderPartResponse)
def buat_request_order_part(
    rop_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """'Buat ROP' (finalisasi): DRAFT → DIBUAT (update ASAHI #003).

    Workflow baru tanpa persetujuan: form diisi → Simpan (DRAFT) →
    tombol "Buat ROP". Dokumen final tidak bisa diedit lagi.
    """
    item = svc.get_request_order_part_by_id(db, rop_id)
    if not item:
        raise HTTPException(status_code=404, detail="Request Order Part tidak ditemukan")
    try:
        return svc.buat_request_order_part(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/request-order-part/{rop_id}/cancel")
def cancel_request_order_part(
    rop_id: UUID,
    payload: Optional[Dict[str, Any]] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Request Order Part — non-destruktif bila sudah diposting.

    DRAFT/belum ber-jurnal → hard delete (snapshot ke log dokumen terhapus,
    modul Histori). Sudah diposting → jurnal pembalik + status BATAL/
    DIBATALKAN; dokumen dan jurnal asli dipertahankan untuk audit.
    """
    item = svc.get_request_order_part_by_id(db, rop_id)
    if not item:
        raise HTTPException(status_code=404, detail="Request Order Part tidak ditemukan")
    reason = (payload or {}).get("reason")
    try:
        return svc.cancel_request_order_part(db, db_obj=item, user=current_user, reason=reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
