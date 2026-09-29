"""Endpoint Histori Dokumen Terhapus — audit trail hard delete (Task 27-a).

Semua pembatalan (/cancel) kini menghapus dokumen secara permanen; modul ini
menyajikan jejak penghapusan lengkap (ringkasan + snapshot JSON per dokumen).
"""
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.transaksi.deleted_document_log import DeletedDocumentLog
from app.schemas.base import PaginatedResponse
from app.schemas.histori import DeletedDocumentLogResponse, DeletedDocumentLogDetailResponse

router = APIRouter()


@router.get('/dokumen-terhapus', response_model=PaginatedResponse[DeletedDocumentLogResponse])
def list_deleted_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    document_type: Optional[str] = Query(None, alias='documentType',
                                         description='Filter jenis dokumen (tablename dokumen sumber)'),
    search: Optional[str] = Query(None, description='Cari nomor dokumen (documentNumber / field di header snapshot)'),
    tanggal_from: Optional[date] = Query(None, alias='tanggalMulai', description='Filter tanggal hapus mulai'),
    tanggal_to: Optional[date] = Query(None, alias='tanggalSampai', description='Filter tanggal hapus sampai'),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar dokumen yang dihapus permanen (ringkas, tanpa snapshot)."""
    query = db.query(DeletedDocumentLog).options(joinedload(DeletedDocumentLog.deleter))
    if document_type:
        query = query.filter(DeletedDocumentLog.document_type == document_type)
    if search:
        pattern = f'%{search}%'
        # Cari nomor dokumen utama ATAU field nomor di header snapshot
        # (mis. no_nukti pembayaran, no_faktur invoice, dsb.).
        query = query.filter(or_(
            DeletedDocumentLog.document_number.ilike(pattern),
            DeletedDocumentLog.snapshot['header'].astext.ilike(pattern),
        ))
    if tanggal_from:
        query = query.filter(DeletedDocumentLog.deleted_at >= datetime.combine(tanggal_from, time.min))
    if tanggal_to:
        query = query.filter(DeletedDocumentLog.deleted_at <= datetime.combine(tanggal_to, time.max))
    total = query.count()
    rows = (query.order_by(DeletedDocumentLog.deleted_at.desc(), DeletedDocumentLog.created_at.desc())
            .offset(skip).limit(limit).all())
    items = [DeletedDocumentLogResponse.model_validate(row) for row in rows]
    return {'data': items, 'total': total, 'skip': skip, 'limit': limit}


@router.get('/dokumen-terhapus/{log_id}', response_model=DeletedDocumentLogDetailResponse)
def get_deleted_document(
    log_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Detail satu entri histori dokumen terhapus termasuk snapshot lengkap."""
    row = (db.query(DeletedDocumentLog)
           .options(joinedload(DeletedDocumentLog.deleter))
           .filter(DeletedDocumentLog.id == log_id)
           .first())
    if not row:
        raise HTTPException(404, 'Log dokumen terhapus tidak ditemukan')
    return DeletedDocumentLogDetailResponse.model_validate(row)
