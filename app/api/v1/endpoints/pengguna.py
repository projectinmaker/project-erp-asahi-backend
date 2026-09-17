from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
import bcrypt

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.pengguna import PenggunaCreate, PenggunaUpdate, PenggunaResponse

router = APIRouter()


@router.get("", response_model=PaginatedResponse[PenggunaResponse])
def get_pengguna_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="Cari berdasarkan nama, username, atau email"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """List semua pengguna dengan pagination."""
    query = db.query(Pengguna)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            Pengguna.nama_lengkap.ilike(search_term)
            | Pengguna.username.ilike(search_term)
            | Pengguna.email.ilike(search_term)
        )

    total = query.count()
    data = query.order_by(Pengguna.created_at.desc()).offset(skip).limit(limit).all()

    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("", response_model=PenggunaResponse, status_code=status.HTTP_201_CREATED)
def create_pengguna(
    data_in: PenggunaCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat pengguna baru. Password akan di-hash otomatis."""
    # Cek duplikat username
    existing = db.query(Pengguna).filter(Pengguna.username == data_in.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")

    # Cek duplikat email
    existing_email = db.query(Pengguna).filter(Pengguna.email == data_in.email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Email sudah digunakan")

    # Hash password
    hashed_password = bcrypt.hashpw(data_in.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    db_obj = Pengguna(
        username=data_in.username,
        nama_lengkap=data_in.nama_lengkap,
        email=data_in.email,
        password_hash=hashed_password,
        role=data_in.role,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@router.get("/{pengguna_id}", response_model=PenggunaResponse)
def get_pengguna_detail(
    pengguna_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Detail satu pengguna."""
    item = db.query(Pengguna).filter(Pengguna.id == pengguna_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")
    return item


@router.put("/{pengguna_id}", response_model=PenggunaResponse)
def update_pengguna(
    pengguna_id: UUID,
    data_in: PenggunaUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update pengguna. Jika password dikirim, akan di-hash ulang."""
    item = db.query(Pengguna).filter(Pengguna.id == pengguna_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)

    # Hash password jika di-update
    if "password" in update_data and update_data["password"]:
        update_data["password_hash"] = bcrypt.hashpw(
            update_data.pop("password").encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
    else:
        update_data.pop("password", None)

    for field, value in update_data.items():
        setattr(item, field, value)

    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{pengguna_id}", status_code=status.HTTP_200_OK)
def delete_pengguna(
    pengguna_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Nonaktifkan pengguna (soft delete)."""
    if current_user.id == pengguna_id:
        raise HTTPException(status_code=400, detail="Tidak bisa menonaktifkan akun sendiri")

    item = db.query(Pengguna).filter(Pengguna.id == pengguna_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Pengguna sudah tidak aktif")

    item.status = "NONAKTIF"
    db.add(item)
    db.commit()
    return {"message": "Pengguna berhasil dinonaktifkan"}
