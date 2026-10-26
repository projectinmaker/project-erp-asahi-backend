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
    NomorPreviewResponse,  # Update #16 — preview nomor dokumen di form
)
from app.schemas.workflow import HardDeleteRequest
from app.services import kas_bank_service as svc
from app.services import workflow_service
from app.services.cancel_document_service import cancel_document

router = APIRouter()


def _pembayaran_response(item):
    """Update cetak "Bayar Pemasok" — bangun response + detail faktur per alokasi.

    Data diskon/terutang sudah live di kolom payment_allocation; detail faktur
    (no faktur, tanggal, jatuh tempo, nilai tagihan) diisi dari relasi
    purchase_invoice yang di-joinedload get_pembayaran_by_id. Urutan baris
    diurutkan kronologis per tanggal faktur supaya tabel cetak rapi.
    """
    resp = PembayaranKasResponse.model_validate(item)
    for a, d in zip(item.alokasi, resp.alokasi):
        inv = a.purchase_invoice
        if inv is not None:
            d.no_faktur = inv.no_faktur
            if inv.tanggal is not None:
                d.tanggal_faktur = inv.tanggal.date() if hasattr(inv.tanggal, 'date') else inv.tanggal
            d.jatuh_tempo = inv.tanggal_jatuh_tempo
            d.nilai_tagihan = inv.grand_total
    resp.alokasi.sort(key=lambda d: (d.tanggal_faktur is not None, d.tanggal_faktur, d.no_faktur or ''))
    return resp


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
    jenis_transaksi: Optional[str] = Query(
        None, description="Update #02 — filter jenis transaksi "
        "(PELUNASAN_INVOICE / TRANSAKSI_LAINNYA / PELUNASAN_PAJAK)"),
    company_id: Optional[UUID] = Query(None, alias="companyId", description="Filter per perusahaan (AUD-12)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Pembayaran Kas dengan filter dan pagination."""
    data, total = svc.get_pembayaran_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, kas_bank_id=kas_bank_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
        jenis_transaksi=jenis_transaksi, company_id=company_id,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/pembayaran", response_model=PembayaranKasResponse, status_code=status.HTTP_201_CREATED)
def create_pembayaran(
    data_in: PembayaranKasCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Pembayaran Kas baru (auto-generate no bukti + auto-post jurnal).

    Update #16 — nomor dokumen per jenis (paritas Accurate Desktop):
    Transaksi Lainnya → "VC ASI/YYYY/NNN"; Pelunasan Invoice → "PAY ASI/YYYY/NNN".
    no_nukti opsional — kosong → auto-isi = nomor dokumen.
    """
    try:
        rincian_data = [r.model_dump() for r in data_in.rincian]
        obj = svc.create_pembayaran(
            db=db,
            no_nukti=data_in.no_nukti,
            tanggal=data_in.tanggal,
            kas_bank_id=data_in.kas_bank_id,
            rincian_data=rincian_data,
            no_cek=data_in.no_cek,
            cek_kosong=data_in.cek_kosong,  # Update #16 — paritas Accurate
            penerima=data_in.penerima,
            catatan=data_in.catatan,
            auto_post_jurnal=False,  # Posting requires approved workflow.
            created_by=current_user.id,
            # Update cetak "Bayar Pemasok" — info header cek & mata uang
            tanggal_cek=data_in.tanggal_cek,
            jumlah_cek=data_in.jumlah_cek,
            mata_uang=data_in.mata_uang,
            nilai_tukar=data_in.nilai_tukar,
            # === Update #02 — jenis transaksi ===
            jenis_transaksi=data_in.jenis_transaksi,
        )
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'pembayaran_kas', obj.id)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/pembayaran/next-nomor", response_model=NomorPreviewResponse)
def get_next_nomor_pembayaran(
    jenis: Optional[str] = Query(
        None, description="Update #16 — jenis transaksi untuk menentukan prefix "
        "(PELUNASAN_INVOICE → PAY; lainnya → VC, ala Accurate Desktop)"),
    tanggal: Optional[date] = Query(
        None, description="Tanggal dokumen (YYYY-MM-DD) — menentukan tahun sequence"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Preview nomor dokumen pembayaran BERIKUTNYA (indikatif, tanpa create).

    Dipakai form Pembayaran ala Accurate untuk menampilkan nomor voucher yang
    akan dipakai sebelum submit. Nomor final di-generate ulang saat create.
    """
    return {"nomor": svc.peek_nomor_pembayaran(db, jenis=jenis, tanggal=tanggal)}


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
    return _pembayaran_response(item)


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
        return cancel_document(
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
    jenis_transaksi: Optional[str] = Query(
        None, description="Update #02 — filter jenis transaksi "
        "(PELUNASAN_INVOICE / TRANSAKSI_LAINNYA)"),
    company_id: Optional[UUID] = Query(None, alias="companyId", description="Filter per perusahaan (AUD-12)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Penerimaan Kas dengan filter dan pagination."""
    data, total = svc.get_penerimaan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, kas_bank_id=kas_bank_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
        jenis_transaksi=jenis_transaksi, company_id=company_id,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/penerimaan", response_model=PenerimaanKasResponse, status_code=status.HTTP_201_CREATED)
def create_penerimaan(
    data_in: PenerimaanKasCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Penerimaan Kas baru (auto-generate no bukti + auto-post jurnal).

    Update #16 — nomor dokumen per jenis (paritas Accurate Desktop):
    Transaksi Lainnya → "PB ASI/YYYY/NNN"; Pelunasan Invoice → "REC ASI/YYYY/NNN".
    no_nukti opsional — kosong → auto-isi = nomor dokumen.
    """
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
            # === Update #02 — jenis transaksi ===
            jenis_transaksi=data_in.jenis_transaksi,
        )
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'penerimaan_kas', obj.id)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/penerimaan/next-nomor", response_model=NomorPreviewResponse)
def get_next_nomor_penerimaan(
    jenis: Optional[str] = Query(
        None, description="Update #16 — jenis transaksi untuk menentukan prefix "
        "(PELUNASAN_INVOICE → REC; lainnya → PB, ala Accurate Desktop)"),
    tanggal: Optional[date] = Query(
        None, description="Tanggal dokumen (YYYY-MM-DD) — menentukan tahun sequence"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Preview nomor dokumen penerimaan BERIKUTNYA (indikatif, tanpa create).

    Dipakai form Penerimaan ala Accurate untuk menampilkan nomor voucher yang
    akan dipakai sebelum submit. Nomor final di-generate ulang saat create.
    """
    return {"nomor": svc.peek_nomor_penerimaan(db, jenis=jenis, tanggal=tanggal)}


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
        return cancel_document(
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
    company_id: Optional[UUID] = Query(None, alias="companyId", description="Filter per perusahaan (AUD-12)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Transfer Bank dengan filter dan pagination."""
    data, total = svc.get_transfer_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
        company_id=company_id,
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
        return cancel_document(
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
