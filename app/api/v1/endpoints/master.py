from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.master.barang import Barang
from app.schemas.base import PaginatedResponse
from app.schemas.master import (
    PelangganCreate, PelangganUpdate, PelangganResponse,
    SupplierCreate, SupplierUpdate, SupplierResponse,
    BarangCreate, BarangUpdate, BarangResponse
)
from app.services import master_service

router = APIRouter()

# ==========================================
# PELANGGAN ENDPOINTS
# ==========================================
@router.get("/pelanggan", response_model=PaginatedResponse[PelangganResponse])
def get_pelanggan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="Cari berdasarkan nama atau kode"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Pelanggan, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/pelanggan", response_model=PelangganResponse, status_code=status.HTTP_201_CREATED)
def create_pelanggan(
    data_in: PelangganCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Pelanggan, data_in)

@router.get("/pelanggan/{pelanggan_id}", response_model=PelangganResponse)
def get_pelanggan_detail(
    pelanggan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    return item

@router.put("/pelanggan/{pelanggan_id}", response_model=PelangganResponse)
def update_pelanggan(
    pelanggan_id: UUID,
    data_in: PelangganUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/pelanggan/{pelanggan_id}", status_code=status.HTTP_200_OK)
def delete_pelanggan(
    pelanggan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Pelanggan sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Pelanggan berhasil dinonaktifkan"}


# ==========================================
# SUPPLIER ENDPOINTS
# ==========================================
@router.get("/supplier", response_model=PaginatedResponse[SupplierResponse])
def get_supplier_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Supplier, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/supplier", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
def create_supplier(
    data_in: SupplierCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Supplier, data_in)

@router.get("/supplier/{supplier_id}", response_model=SupplierResponse)
def get_supplier_detail(supplier_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    return item

@router.put("/supplier/{supplier_id}", response_model=SupplierResponse)
def update_supplier(supplier_id: UUID, data_in: SupplierUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/supplier/{supplier_id}", status_code=status.HTTP_200_OK)
def delete_supplier(supplier_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Supplier sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Supplier berhasil dinonaktifkan"}


# ==========================================
# BARANG ENDPOINTS
# ==========================================
@router.get("/barang", response_model=PaginatedResponse[BarangResponse])
def get_barang_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Barang, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/barang", response_model=BarangResponse, status_code=status.HTTP_201_CREATED)
def create_barang(
    data_in: BarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Barang, data_in)

@router.get("/barang/{barang_id}", response_model=BarangResponse)
def get_barang_detail(barang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    return item

@router.put("/barang/{barang_id}", response_model=BarangResponse)
def update_barang(barang_id: UUID, data_in: BarangUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/barang/{barang_id}", status_code=status.HTTP_200_OK)
def delete_barang(barang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Barang sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Barang berhasil dinonaktifkan"}