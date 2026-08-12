from typing import Type, TypeVar, List, Optional, Any
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import or_

# Generic Type agar IDE tahu return type-nya model apa
T = TypeVar("T", bound=object)


def get_master_list(
    db: Session,
    Model: Type[T],
    skip: int,
    limit: int,
    search_fields: List[str],
    search: Optional[str] = None,
) -> List[T]:
    """
    Fungsi generik untuk mengambil daftar master data.
    Bisa dipakai untuk Pelanggan, Supplier, Barang, dll.
    """
    query = db.query(Model)

    if search:
        # Buat filter LIKE secara dinamis berdasarkan kolom yang diperbolehkan
        conditions = [getattr(Model, field).ilike(f"%{search}%") for field in search_fields]
        query = query.filter(or_(*conditions))

    return query.order_by(Model.created_at.desc()).offset(skip).limit(limit).all()


def get_master_by_id(db: Session, Model: Type[T], item_id: UUID) -> Optional[T]:
    """Fungsi generik untuk mengambil 1 data master berdasarkan UUID"""
    return db.query(Model).filter(Model.id == item_id).first()


def create_master(db: Session, Model: Type[T], schema_in: BaseException) -> T:
    """Fungsi generik untuk membuat data master baru"""
    db_obj = Model(**schema_in.model_dump())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def update_master(db: Session, db_obj: Any, schema_in: BaseException) -> Any:
    """Fungsi generik untuk update data master yang sudah ada"""
    update_data = schema_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj
