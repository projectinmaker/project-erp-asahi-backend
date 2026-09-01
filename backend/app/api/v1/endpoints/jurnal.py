from typing import Optional, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.transaksi.jurnal import JurnalUmum, RefModule, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail
from app.schemas.base import PaginatedResponse
from app.schemas.jurnal import JurnalUmumListResponse, JurnalUmumDetailResponse, JurnalDetailItemCreate, JurnalManualCreate
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem

router = APIRouter()


@router.get("", response_model=PaginatedResponse[JurnalUmumListResponse])
def get_jurnal_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="Cari berdasarkan no jurnal, keterangan, atau tipe transaksi"),
    ref_module: Optional[RefModule] = Query(None, description="Filter berdasarkan modul referensi"),
    status_filter: Optional[StatusJurnal] = Query(None, alias="status", description="Filter status jurnal"),
    tanggal_mulai: Optional[datetime] = Query(None, alias="tanggalMulai", description="Filter tanggal mulai"),
    tanggal_akhir: Optional[datetime] = Query(None, alias="tanggalAkhir", description="Filter tanggal akhir"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """List semua jurnal umum dengan filter dan pagination."""
    query = db.query(JurnalUmum).options(joinedload(JurnalUmum.creator))

    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            JurnalUmum.no_jurnal.ilike(search_term)
            | JurnalUmum.keterangan.ilike(search_term)
            | JurnalUmum.tipe_transaksi.ilike(search_term)
        )

    # Module filter
    if ref_module:
        query = query.filter(JurnalUmum.ref_module == ref_module)

    # Status filter
    if status_filter:
        query = query.filter(JurnalUmum.status == status_filter)

    # Date range filter
    if tanggal_mulai:
        query = query.filter(JurnalUmum.tanggal >= tanggal_mulai)
    if tanggal_akhir:
        query = query.filter(JurnalUmum.tanggal <= tanggal_akhir)

    total = query.count()
    data = query.order_by(JurnalUmum.tanggal.desc(), JurnalUmum.no_jurnal.desc()) \
        .offset(skip).limit(limit).all()

    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.get("/{jurnal_id}", response_model=JurnalUmumDetailResponse)
def get_jurnal_detail(
    jurnal_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Detail jurnal umum beserta semua line items."""
    item = (
        db.query(JurnalUmum)
        .options(
            joinedload(JurnalUmum.creator),
            joinedload(JurnalUmum.details).joinedload(JurnalDetail.akun_perkiraan),
        )
        .filter(JurnalUmum.id == jurnal_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Jurnal Umum tidak ditemukan")
    return item


# ==========================================
# JURNAL MANUAL
# ==========================================
@router.post("/manual", response_model=JurnalUmumDetailResponse, status_code=status.HTTP_201_CREATED)
def create_jurnal_manual(
    data_in: JurnalManualCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Jurnal Umum secara manual.

    User menginput sendiri baris-baris debit dan kredit.
    Sistem akan:
    1. Validasi balance (total debit == total kredit)
    2. Generate nomor jurnal otomatis
    3. Set tipe_transaksi = 'MANUAL' dan ref_module = MANUAL
    """
    if not data_in.details or len(data_in.details) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Minimal 2 baris jurnal (debit dan kredit)",
        )

    # Validasi balance
    total_debit = sum(d.debit for d in data_in.details)
    total_kredit = sum(d.kredit for d in data_in.details)
    if total_debit != total_kredit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Jurnal tidak balance: total debit={total_debit}, total kredit={total_kredit}",
        )

    # Validasi tidak ada baris yang debit dan kredit keduanya 0
    for i, d in enumerate(data_in.details):
        if d.debit == 0 and d.kredit == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Baris {i+1}: debit dan kredit tidak boleh keduanya 0",
            )

    # Convert ke JurnalEntryItem
    entries = [
        JurnalEntryItem(
            akun_perkiraan_id=d.akun_perkiraan_id,
            debit=d.debit,
            kredit=d.kredit,
            keterangan=d.keterangan,
        )
        for d in data_in.details
    ]

    try:
        jurnal = auto_posting_jurnal(
            db=db,
            ref_module=RefModule.MANUAL,
            ref_no="",  # Manual jurnal tidak punya ref_no dokumen
            entries=entries,
            keterangan=data_in.keterangan,
            tanggal=data_in.tanggal,
            created_by=current_user.id,
            tipe_transaksi="MANUAL",
            status=StatusJurnal.POSTED,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Return detail response
    return (
        db.query(JurnalUmum)
        .options(
            joinedload(JurnalUmum.creator),
            joinedload(JurnalUmum.details).joinedload(JurnalDetail.akun_perkiraan),
        )
        .filter(JurnalUmum.id == jurnal.id)
        .first()
    )
