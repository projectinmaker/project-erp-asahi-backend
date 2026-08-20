"""
Persediaan Endpoints.
PenyesuaianStok, PemindahanBarang, PermintaanBarang.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.persediaan import (
    PenyesuaianStokCreate, PenyesuaianStokUpdate, PenyesuaianStokResponse,
    PemindahanBarangCreate, PemindahanBarangUpdate, PemindahanBarangResponse,
    PermintaanBarangCreate, PermintaanBarangUpdate, PermintaanBarangResponse,
)
from app.services import persediaan_service as svc

router = APIRouter()


# ==========================================
# PENYESUAIAN STOK
# ==========================================

@router.get("/penyesuaian-stok", response_model=PaginatedResponse[PenyesuaianStokResponse])
def get_penyesuaian_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no adj, alasan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    barang_id: Optional[UUID] = Query(None, description="Filter barang"),
    tipe: Optional[str] = Query(None, description="Filter tipe (TAMBAH/KURANG)"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Penyesuaian Stok dengan filter dan pagination."""
    data, total = svc.get_penyesuaian_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, barang_id=barang_id, tipe=tipe,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/penyesuaian-stok", response_model=PenyesuaianStokResponse, status_code=status.HTTP_201_CREATED)
def create_penyesuaian(
    data_in: PenyesuaianStokCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Penyesuaian Stok baru (auto-generate no adj + auto-post jurnal)."""
    try:
        return svc.create_penyesuaian(
            db=db,
            tanggal=data_in.tanggal,
            barang_id=data_in.barang_id,
            tipe=data_in.tipe,
            qty=data_in.qty,
            biaya_satuan=data_in.biaya_satuan,
            alasan=data_in.alasan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/penyesuaian-stok/{adj_id}", response_model=PenyesuaianStokResponse)
def get_penyesuaian_detail(
    adj_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Penyesuaian Stok."""
    item = svc.get_penyesuaian_by_id(db, adj_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penyesuaian Stok tidak ditemukan")
    return item


@router.put("/penyesuaian-stok/{adj_id}", response_model=PenyesuaianStokResponse)
def update_penyesuaian(
    adj_id: UUID,
    data_in: PenyesuaianStokUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Penyesuaian Stok."""
    item = svc.get_penyesuaian_by_id(db, adj_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penyesuaian Stok tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_penyesuaian(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/penyesuaian-stok/{adj_id}/approve", response_model=PenyesuaianStokResponse)
def approve_penyesuaian(
    adj_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Setujui Penyesuaian Stok."""
    item = svc.get_penyesuaian_by_id(db, adj_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penyesuaian Stok tidak ditemukan")
    try:
        return svc.approve_penyesuaian(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/penyesuaian-stok/{adj_id}/cancel", response_model=PenyesuaianStokResponse)
def cancel_penyesuaian(
    adj_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Penyesuaian Stok."""
    item = svc.get_penyesuaian_by_id(db, adj_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penyesuaian Stok tidak ditemukan")
    try:
        return svc.cancel_penyesuaian(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PEMINDAHAN BARANG
# ==========================================

@router.get("/pemindahan", response_model=PaginatedResponse[PemindahanBarangResponse])
def get_pemindahan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no pemindahan, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    proses: Optional[str] = Query(None, description="Filter proses (KIRIM/TERIMA)"),
    dari_gudang_id: Optional[UUID] = Query(None, description="Filter gudang asal"),
    ke_gudang_id: Optional[UUID] = Query(None, description="Filter gudang tujuan"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Pemindahan Barang dengan filter dan pagination."""
    data, total = svc.get_pemindahan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, proses=proses,
        dari_gudang_id=dari_gudang_id, ke_gudang_id=ke_gudang_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/pemindahan", response_model=PemindahanBarangResponse, status_code=status.HTTP_201_CREATED)
def create_pemindahan(
    data_in: PemindahanBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Pemindahan Barang baru (auto-generate no pemindahan)."""
    try:
        return svc.create_pemindahan(
            db=db,
            tanggal=data_in.tanggal,
            proses=data_in.proses,
            dari_gudang_id=data_in.dari_gudang_id,
            ke_gudang_id=data_in.ke_gudang_id,
            barang_id=data_in.barang_id,
            qty=data_in.qty,
            auto_post_jurnal=data_in.auto_post_jurnal,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/pemindahan/{pb_id}", response_model=PemindahanBarangResponse)
def get_pemindahan_detail(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Pemindahan Barang."""
    item = svc.get_pemindahan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pemindahan Barang tidak ditemukan")
    return item


@router.put("/pemindahan/{pb_id}", response_model=PemindahanBarangResponse)
def update_pemindahan(
    pb_id: UUID,
    data_in: PemindahanBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Pemindahan Barang."""
    item = svc.get_pemindahan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pemindahan Barang tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_pemindahan(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pemindahan/{pb_id}/approve", response_model=PemindahanBarangResponse)
def approve_pemindahan(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Setujui Pemindahan Barang."""
    item = svc.get_pemindahan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pemindahan Barang tidak ditemukan")
    try:
        return svc.approve_pemindahan(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pemindahan/{pb_id}/cancel", response_model=PemindahanBarangResponse)
def cancel_pemindahan(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Pemindahan Barang."""
    item = svc.get_pemindahan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pemindahan Barang tidak ditemukan")
    try:
        return svc.cancel_pemindahan(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PERMINTAAN BARANG
# ==========================================

@router.get("/permintaan", response_model=PaginatedResponse[PermintaanBarangResponse])
def get_permintaan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no permintaan, diajukan oleh, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    barang_id: Optional[UUID] = Query(None, description="Filter barang"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Permintaan Barang dengan filter dan pagination."""
    data, total = svc.get_permintaan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, barang_id=barang_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/permintaan", response_model=PermintaanBarangResponse, status_code=status.HTTP_201_CREATED)
def create_permintaan(
    data_in: PermintaanBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Permintaan Barang baru (auto-generate no permintaan)."""
    try:
        return svc.create_permintaan(
            db=db,
            tanggal=data_in.tanggal,
            barang_id=data_in.barang_id,
            qty=data_in.qty,
            diajukan_oleh=data_in.diajukan_oleh,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/permintaan/{req_id}", response_model=PermintaanBarangResponse)
def get_permintaan_detail(
    req_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Permintaan Barang."""
    item = svc.get_permintaan_by_id(db, req_id)
    if not item:
        raise HTTPException(status_code=404, detail="Permintaan Barang tidak ditemukan")
    return item


@router.put("/permintaan/{req_id}", response_model=PermintaanBarangResponse)
def update_permintaan(
    req_id: UUID,
    data_in: PermintaanBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Permintaan Barang."""
    item = svc.get_permintaan_by_id(db, req_id)
    if not item:
        raise HTTPException(status_code=404, detail="Permintaan Barang tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_permintaan(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/permintaan/{req_id}/approve", response_model=PermintaanBarangResponse)
def approve_permintaan(
    req_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Setujui Permintaan Barang."""
    item = svc.get_permintaan_by_id(db, req_id)
    if not item:
        raise HTTPException(status_code=404, detail="Permintaan Barang tidak ditemukan")
    try:
        return svc.approve_permintaan(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/permintaan/{req_id}/cancel", response_model=PermintaanBarangResponse)
def cancel_permintaan(
    req_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Permintaan Barang."""
    item = svc.get_permintaan_by_id(db, req_id)
    if not item:
        raise HTTPException(status_code=404, detail="Permintaan Barang tidak ditemukan")
    try:
        return svc.cancel_permintaan(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
