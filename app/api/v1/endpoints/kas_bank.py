"""
Kas & Bank Endpoints.
Pembayaran Kas, Penerimaan Kas, Transfer Bank.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.kas_bank import (
    PembayaranKasCreate, PembayaranKasUpdate, PembayaranKasResponse,
    PenerimaanKasCreate, PenerimaanKasUpdate, PenerimaanKasResponse,
    TransferBankCreate, TransferBankUpdate, TransferBankResponse,
)
from app.schemas.workflow import HardDeleteRequest
from app.services import kas_bank_service as svc
from app.services import workflow_service
from app.services.hard_delete_service import hard_delete_document

router = APIRouter()


# ==========================================
# PEMBAYARAN KAS
# ==========================================

@router.get("/pembayaran", response_model=PaginatedResponse[PembayaranKasResponse])
def get_pembayaran_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no bukti, penerima, no nukti"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    kas_bank_id: Optional[UUID] = Query(None, description="Filter kas/bank"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Pembayaran Kas dengan filter dan pagination."""
    data, total = svc.get_pembayaran_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, kas_bank_id=kas_bank_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/pembayaran", response_model=PembayaranKasResponse, status_code=status.HTTP_201_CREATED)
def create_pembayaran(
    data_in: PembayaranKasCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Pembayaran Kas baru (auto-generate no bukti + auto-post jurnal)."""
    try:
        rincian_data = [r.model_dump() for r in data_in.rincian]
        obj = svc.create_pembayaran(
            db=db,
            no_nukti=data_in.no_nukti,
            tanggal=data_in.tanggal,
            kas_bank_id=data_in.kas_bank_id,
            rincian_data=rincian_data,
            no_cek=data_in.no_cek,
            penerima=data_in.penerima,
            catatan=data_in.catatan,
            auto_post_jurnal=False,  # Posting requires approved workflow.
            created_by=current_user.id,
        )
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'pembayaran_kas', obj.id)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/pembayaran/{pembayaran_id}", response_model=PembayaranKasResponse)
def get_pembayaran_detail(
    pembayaran_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Pembayaran Kas."""
    item = svc.get_pembayaran_by_id(db, pembayaran_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pembayaran Kas tidak ditemukan")
    return item


@router.put("/pembayaran/{pembayaran_id}", response_model=PembayaranKasResponse)
def update_pembayaran(
    pembayaran_id: UUID,
    data_in: PembayaranKasUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Pembayaran Kas."""
    item = svc.get_pembayaran_by_id(db, pembayaran_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pembayaran Kas tidak ditemukan")
    if item.status == "BATAL":
        raise HTTPException(status_code=400, detail="Pembayaran sudah dibatalkan, tidak bisa diupdate")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_pembayaran(db, db_obj=item, **update_data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/pembayaran/{pembayaran_id}/cancel")
def cancel_pembayaran(
    pembayaran_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Pembayaran Kas (hard delete).

    Dokumen + rincian + jurnal terkait dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return hard_delete_document(
            db, 'pembayaran_kas', pembayaran_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PENERIMAAN KAS
# ==========================================

@router.get("/penerimaan", response_model=PaginatedResponse[PenerimaanKasResponse])
def get_penerimaan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no bukti, pemberi, no nukti"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    kas_bank_id: Optional[UUID] = Query(None, description="Filter kas/bank"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Penerimaan Kas dengan filter dan pagination."""
    data, total = svc.get_penerimaan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, kas_bank_id=kas_bank_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/penerimaan", response_model=PenerimaanKasResponse, status_code=status.HTTP_201_CREATED)
def create_penerimaan(
    data_in: PenerimaanKasCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Penerimaan Kas baru (auto-generate no bukti + auto-post jurnal)."""
    try:
        rincian_data = [r.model_dump() for r in data_in.rincian]
        obj = svc.create_penerimaan(
            db=db,
            no_nukti=data_in.no_nukti,
            tanggal=data_in.tanggal,
            kas_bank_id=data_in.kas_bank_id,
            rincian_data=rincian_data,
            no_cek=data_in.no_cek,
            pemberi=data_in.pemberi,
            catatan=data_in.catatan,
            auto_post_jurnal=False,  # Posting requires approved workflow.
            created_by=current_user.id,
        )
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'penerimaan_kas', obj.id)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/penerimaan/{penerimaan_id}", response_model=PenerimaanKasResponse)
def get_penerimaan_detail(
    penerimaan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Penerimaan Kas."""
    item = svc.get_penerimaan_by_id(db, penerimaan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penerimaan Kas tidak ditemukan")
    return item


@router.put("/penerimaan/{penerimaan_id}", response_model=PenerimaanKasResponse)
def update_penerimaan(
    penerimaan_id: UUID,
    data_in: PenerimaanKasUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Penerimaan Kas."""
    item = svc.get_penerimaan_by_id(db, penerimaan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penerimaan Kas tidak ditemukan")
    if item.status == "BATAL":
        raise HTTPException(status_code=400, detail="Penerimaan sudah dibatalkan, tidak bisa diupdate")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_penerimaan(db, db_obj=item, **update_data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/penerimaan/{penerimaan_id}/cancel")
def cancel_penerimaan(
    penerimaan_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Penerimaan Kas (hard delete).

    Dokumen + rincian + jurnal terkait dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return hard_delete_document(
            db, 'penerimaan_kas', penerimaan_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# TRANSFER BANK
# ==========================================

@router.get("/transfer", response_model=PaginatedResponse[TransferBankResponse])
def get_transfer_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no transfer"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Transfer Bank dengan filter dan pagination."""
    data, total = svc.get_transfer_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/transfer", response_model=TransferBankResponse, status_code=status.HTTP_201_CREATED)
def create_transfer(
    data_in: TransferBankCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Transfer Bank baru (auto-generate no transfer + auto-post jurnal)."""
    try:
        obj = svc.create_transfer(
            db=db,
            tanggal=data_in.tanggal,
            dari_kas_bank_id=data_in.dari_kas_bank_id,
            ke_kas_bank_id=data_in.ke_kas_bank_id,
            nilai_transfer=data_in.nilai_transfer,
            biaya_transfer=data_in.biaya_transfer,
            informasi=data_in.informasi,
            auto_post_jurnal=False,  # Posting requires approved workflow.
            created_by=current_user.id,
        )
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'transfer_bank', obj.id)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/transfer/{transfer_id}", response_model=TransferBankResponse)
def get_transfer_detail(
    transfer_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Transfer Bank."""
    item = svc.get_transfer_by_id(db, transfer_id)
    if not item:
        raise HTTPException(status_code=404, detail="Transfer Bank tidak ditemukan")
    return item


@router.put("/transfer/{transfer_id}", response_model=TransferBankResponse)
def update_transfer(
    transfer_id: UUID,
    data_in: TransferBankUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Transfer Bank."""
    item = svc.get_transfer_by_id(db, transfer_id)
    if not item:
        raise HTTPException(status_code=404, detail="Transfer Bank tidak ditemukan")
    if item.status == "BATAL":
        raise HTTPException(status_code=400, detail="Transfer sudah dibatalkan, tidak bisa diupdate")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_transfer(db, db_obj=item, **update_data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/transfer/{transfer_id}/cancel")
def cancel_transfer(
    transfer_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Transfer Bank (hard delete).

    Dokumen + rincian + jurnal terkait dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return hard_delete_document(
            db, 'transfer_bank', transfer_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/transfer/{transfer_id}/reverse", response_model=TransferBankResponse)
def reverse_transfer(
    transfer_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
    reason: Optional[str] = Query(None, description="Alasan reversal (optional)"),
):
    """Reverse Transfer Bank yang sudah SELESAI.

    Phase 6 — Master Roadmap §23: "Void reconciliation creates controlled reversal".

    Berbeda dengan cancel (yang hanya untuk status DRAFT), reverse bisa untuk
    transfer yang sudah SELESAI — jurnal sudah posted.

    Reverse akan:
    1. Reverse jurnal transfer via posting_service.reverse_journal
       - Reverse: Dr Bank Asal / Cr Bank Tujuan / Cr Beban Transfer (pembalik)
    2. Set status TransferBank ke BATAL
    3. Catat reversal via audit trail jurnal_umum (reversal_of_id)

    Catatan: Transfer bank TIDAK menggerakkan stock movement, jadi tidak perlu
    call reverse_stock_movement. Hanya reverse jurnal.
    """
    from app.services.inventory_reversal_service import reverse_transfer as _reverse

    try:
        result = _reverse(
            db, transfer_id, current_user.id,
            reason=reason or "Reversal transfer"
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
