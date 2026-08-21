from typing import Type, TypeVar, List, Optional, Any, Tuple
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.schemas.base import BaseSchema

T = TypeVar('T', bound=object)

def get_master_list(
    db: Session,
    Model: Type[T],
    skip: int,
    limit: int,
    search_fields: List[str],
    search: Optional[str] = None
) -> Tuple[List[T], int]:
    """
    Fungsi generik untuk mengambil daftar master data.
    Return: Tuple berisi (list_data, total_count)
    """
    query = db.query(Model)

    if search:
        conditions = [getattr(Model, field).ilike(f"%{search}%") for field in search_fields]
        query = query.filter(or_(*conditions))

    # Hitung total keseluruhan data (SEBELUM di-slice)
    total = query.count()

    # Ambil data sesuai batas skip dan limit
    data = query.order_by(Model.created_at.desc()).offset(skip).limit(limit).all()

    return data, total

def get_master_by_id(db: Session, Model: Type[T], item_id: UUID) -> Optional[T]:
    """Fungsi generik untuk mengambil 1 data master berdasarkan UUID"""
    return db.query(Model).filter(Model.id == item_id).first()

def create_master(db: Session, Model: Type[T], schema_in: BaseSchema) -> T:
    """Fungsi generik untuk membuat data master baru"""
    db_obj = Model(**schema_in.model_dump())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def update_master(db: Session, db_obj: Any, schema_in: BaseSchema) -> Any:
    """Fungsi generik untuk update data master yang sudah ada"""
    update_data = schema_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def soft_delete_master(db: Session, db_obj: Any) -> Any:
    """Mengubah status master data menjadi NONAKTIF"""
    db_obj.status = "NONAKTIF"
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj