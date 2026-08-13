from typing import List, Optional, Tuple # <-- Tambahkan Tuple
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun
from app.schemas.coa import COACreate, COAUpdate

def get_coa_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    header: Optional[HeaderCOA] = None,
    tingkat: Optional[TingkatAkun] = None,
    search: Optional[str] = None
) -> Tuple[List[AkunPerkiraan], int]: # <-- UBAH RETURN TYPE
    """Mengambil daftar COA beserta total datanya"""
    query = db.query(AkunPerkiraan)

    if header:
        query = query.filter(AkunPerkiraan.header == header)
    if tingkat:
        query = query.filter(AkunPerkiraan.tingkat == tingkat)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                AkunPerkiraan.kode.ilike(search_pattern),
                AkunPerkiraan.nama.ilike(search_pattern)
            )
        )

    # Hitung total SEBELUM di-slice (penting untuk pagination)
    total = query.count()

    # Slice datanya
    data = query.order_by(AkunPerkiraan.kode).offset(skip).limit(limit).all()

    return data, total # <-- RETURN TUPLE

# ... (fungsi lainnya tetap sama) ...


def get_coa_by_id(db: Session, coa_id: UUID) -> Optional[AkunPerkiraan]:
    """Mengambil 1 COA berdasarkan UUID"""
    return db.query(AkunPerkiraan).filter(AkunPerkiraan.id == coa_id).first()


def get_coa_by_kode(db: Session, kode: str) -> Optional[AkunPerkiraan]:
    """Mengambil 1 COA berdasarkan Kode Akun"""
    return db.query(AkunPerkiraan).filter(AkunPerkiraan.kode == kode).first()


def create_coa(db: Session, coa_in: COACreate) -> AkunPerkiraan:
    """Membuat COA baru"""
    db_obj = AkunPerkiraan(**coa_in.model_dump())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def update_coa(db: Session, db_obj: AkunPerkiraan, obj_in: COAUpdate) -> AkunPerkiraan:
    """Update data COA yang sudah ada"""
    update_data = obj_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_obj, field, value)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj
