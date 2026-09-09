from typing import Type, TypeVar, List, Optional, Any, Tuple
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, UniqueConstraint
from sqlalchemy.exc import IntegrityError

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

def _duplicate_detail(Model: Type[T], data: dict, error: IntegrityError) -> str:
    """Gunakan metadata constraint, tanpa membocorkan detail SQL/database."""
    table = Model.__table__
    constraint_name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    for constraint in list(table.constraints) + list(table.indexes):
        if not isinstance(constraint, UniqueConstraint) and not getattr(constraint, "unique", False):
            continue
        columns = list(constraint.columns)
        name = constraint.name or f"{table.name}_{'_'.join(c.name for c in columns)}_key"
        if name == constraint_name and len(columns) == 1:
            field = columns[0].name
            label = "NIK" if field == "nik" else field.replace("_", " ").capitalize()
            if field in data:
                return f"{label} {table.name.replace('_', ' ')} '{data[field]}' sudah digunakan"
    return f"Data {table.name.replace('_', ' ')} sudah digunakan"


def create_master(db: Session, Model: Type[T], schema_in: BaseSchema) -> T:
    """Fungsi generik untuk membuat data master baru"""
    data = schema_in.model_dump()
    db_obj = Model(**data)
    db.add(db_obj)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        # PostgreSQL unique_violation; constraint lain tetap ditangani sebagai error asli.
        if getattr(error.orig, "pgcode", None) == "23505":
            raise HTTPException(status_code=400, detail=_duplicate_detail(Model, data, error)) from error
        raise
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
