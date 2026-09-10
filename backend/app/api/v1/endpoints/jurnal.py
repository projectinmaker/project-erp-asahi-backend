from app.services.accounting_control import atomic_accounting_write
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_db, get_current_user
from app.api.reporting import report_scope
from app.services.organization_service import apply_scope
from app.models.master.pengguna import Pengguna
from app.models.transaksi.jurnal import JurnalUmum, RefModule, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail
from app.schemas.base import PaginatedResponse
from app.schemas.jurnal import JurnalUmumListResponse, JurnalUmumDetailResponse, JurnalDetailItemCreate, JurnalManualCreate
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.services.accounting_control import require_unposted
from app.services.posting_service import validate_entries

router = APIRouter()


@atomic_accounting_write
def _update_manual(db, db_obj, data_in, actor):
    require_unposted(db_obj)
    from app.services.workflow_service import role, APPROVERS, find_workflow, append_event
    from app.services.penutupan_periode_service import validate_periode_not_closed
    if actor.id != db_obj.created_by and role(actor) not in APPROVERS:
        raise HTTPException(403, 'Hanya pembuat atau manajer/admin yang dapat mengedit draft')
    validate_periode_not_closed(db, data_in.tanggal)
    entries = [JurnalEntryItem(d.akun_perkiraan_id, d.debit, d.kredit, d.keterangan) for d in data_in.details]
    debit, kredit = validate_entries(db, entries)
    db_obj.details.clear()
    db.flush()
    for e in entries:
        db_obj.details.append(JurnalDetail(akun_perkiraan_id=e.akun_perkiraan_id, debit=e.debit, kredit=e.kredit, keterangan=e.keterangan))
    from app.services.reporting_ledger import local_datetime
    db_obj.tanggal = local_datetime(data_in.tanggal)
    db_obj.keterangan = data_in.keterangan
    db_obj.total_debit, db_obj.total_kredit = debit, kredit
    wf = find_workflow(db, db_obj)
    if wf:
        append_event(db, wf, 'edit', 'DRAFT', actor.id)
    return db_obj


@router.put('/manual/{jurnal_id}', response_model=JurnalUmumDetailResponse)
def update_manual(jurnal_id: UUID, data_in: JurnalManualCreate, db: Session = Depends(get_current_db),
                  current_user: Pengguna = Depends(get_current_user)):
    obj = db.get(JurnalUmum, jurnal_id)
    if not obj or obj.ref_module != RefModule.MANUAL or obj.reversal_of_id:
        raise HTTPException(404, 'Jurnal manual tidak ditemukan')
    try:
        return _update_manual(db, obj, data_in, current_user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


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
    _scope=Depends(report_scope),
):
    """List semua jurnal umum dengan filter dan pagination."""
    query = apply_scope(db, db.query(JurnalUmum).options(joinedload(JurnalUmum.creator)), JurnalUmum)

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
@atomic_accounting_write
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
            status=StatusJurnal.DRAFT,
        )
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        db.rollback()
        raise

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
