from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.master.karyawan import Karyawan
from app.schemas.base import PaginatedResponse
from app.schemas.karyawan import KaryawanCreate, KaryawanUpdate, KaryawanResponse
from app.services import master_service

router = APIRouter()


@router.get("", response_model=PaginatedResponse[KaryawanResponse])
def get_karyawan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="Cari berdasarkan nama atau NIK"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """List semua karyawan dengan pagination."""
    data, total = master_service.get_master_list(db, Karyawan, skip, limit, ["nama", "nik"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("", response_model=KaryawanResponse, status_code=status.HTTP_201_CREATED)
def create_karyawan(
    data_in: KaryawanCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat data karyawan baru."""
    # Cek duplikat NIK
    existing = db.query(Karyawan).filter(Karyawan.nik == data_in.nik).first()
    if existing:
        raise HTTPException(status_code=400, detail="NIK sudah terdaftar")

    return master_service.create_master(db, Karyawan, data_in)


@router.get("/{karyawan_id}", response_model=KaryawanResponse)
def get_karyawan_detail(
    karyawan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Detail satu karyawan."""
    item = master_service.get_master_by_id(db, Karyawan, karyawan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Karyawan tidak ditemukan")
    return item


@router.put("/{karyawan_id}", response_model=KaryawanResponse)
def update_karyawan(
    karyawan_id: UUID,
    data_in: KaryawanUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data karyawan."""
    item = master_service.get_master_by_id(db, Karyawan, karyawan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Karyawan tidak ditemukan")

    # Cek duplikat NIK jika diubah
    update_data = data_in.model_dump(exclude_unset=True)
    if "nik" in update_data and update_data["nik"] != item.nik:
        existing = db.query(Karyawan).filter(Karyawan.nik == update_data["nik"]).first()
        if existing:
            raise HTTPException(status_code=400, detail="NIK sudah terdaftar")

    return master_service.update_master(db, item, data_in)


@router.delete("/{karyawan_id}", status_code=status.HTTP_200_OK)
def delete_karyawan(
    karyawan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Nonaktifkan karyawan (soft delete)."""
    item = master_service.get_master_by_id(db, Karyawan, karyawan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Karyawan tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Karyawan sudah tidak aktif")

    master_service.soft_delete_master(db, item)
    return {"message": "Karyawan berhasil dinonaktifkan"}
