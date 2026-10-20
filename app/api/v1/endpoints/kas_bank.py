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
    KasIdentifikasiCreate, KasIdentifikasiResponse, KasIdentifikasiListResponse,
    IdentifikasiPiutangCreate, IdentifikasiHutangCreate,
    PenampungMonitoringResponse,
    PajakMonitoringResponse,
    KasBankSimpleResponse, AkunPerkiraanSimpleResponse,
)
from app.schemas.workflow import HardDeleteRequest
from app.services import kas_bank_service as svc
from app.services import kas_identifikasi_service as identifikasi_svc
from app.services import workflow_service
from app.services.hard_delete_service import hard_delete_document

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


def _kas_bank_simple(kas_bank):
    """Update #02 — nested kas/bank utk KasIdentifikasiListResponse."""
    if kas_bank is None:
        return None
    akun = getattr(kas_bank, "akun_perkiraan", None)
    return KasBankSimpleResponse(
        id=kas_bank.id, kode=kas_bank.kode, nama=kas_bank.nama,
        jenis=getattr(kas_bank, "jenis", ""),
        akun_perkiraan=(
            AkunPerkiraanSimpleResponse(id=akun.id, kode=akun.kode, nama=akun.nama)
            if akun is not None else None
        ),
    )


def _identifikasi_list_response(result) -> KasIdentifikasiListResponse:
    """Update #02 — map hasil get_identifikasi_list ke response schema."""
    dokumen = result["dokumen"]
    rincian = list(getattr(dokumen, "rincian", None) or [])
    penampung = None
    if rincian:
        akun = getattr(rincian[0], "akun_perkiraan", None)
        if akun is not None:
            penampung = AkunPerkiraanSimpleResponse(id=akun.id, kode=akun.kode, nama=akun.nama)
    status = getattr(dokumen.status, "value", dokumen.status)
    return KasIdentifikasiListResponse(
        dokumen_id=dokumen.id,
        jenis_dokumen=("penerimaan" if hasattr(dokumen, "pemberi") else "pembayaran"),
        no_bukti_dokumen=dokumen.no_bukti,
        tanggal_dokumen=dokumen.tanggal,
        total_nilai_dokumen=dokumen.total_nilai,
        jenis_transaksi_dokumen=(getattr(dokumen, "jenis_transaksi", None) or "TRANSAKSI_LAINNYA"),
        status_dokumen=str(status),
        kas_bank=_kas_bank_simple(getattr(dokumen, "kas_bank", None)),
        penampung_akun=penampung,
        teridentifikasi=result["teridentifikasi"],
        sisa_belum_teridentifikasi=result["sisa"],
        identifikasi=result["identifikasi"],
    )


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
        "(PELUNASAN_INVOICE / TRANSAKSI_LAINNYA / BELUM_TERIDENTIFIKASI)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Pembayaran Kas dengan filter dan pagination."""
    data, total = svc.get_pembayaran_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, kas_bank_id=kas_bank_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
        jenis_transaksi=jenis_transaksi,
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
            # Update cetak "Bayar Pemasok" — info header cek & mata uang
            tanggal_cek=data_in.tanggal_cek,
            jumlah_cek=data_in.jumlah_cek,
            mata_uang=data_in.mata_uang,
            nilai_tukar=data_in.nilai_tukar,
            # === Update #02 — jenis transaksi & nilai tunggal ===
            jenis_transaksi=data_in.jenis_transaksi,
            nilai=data_in.nilai,
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
    jenis_transaksi: Optional[str] = Query(
        None, description="Update #02 — filter jenis transaksi "
        "(PELUNASAN_INVOICE / TRANSAKSI_LAINNYA / BELUM_TERIDENTIFIKASI)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Penerimaan Kas dengan filter dan pagination."""
    data, total = svc.get_penerimaan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, kas_bank_id=kas_bank_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
        jenis_transaksi=jenis_transaksi,
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
            # === Update #02 — jenis transaksi & nilai tunggal ===
            jenis_transaksi=data_in.jenis_transaksi,
            nilai=data_in.nilai,
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


# ==========================================
# UPDATE #02 — IDENTIFIKASI & ALOKASI
# (dokumen BELUM_TERIDENTIFIKASI)
# ==========================================

@router.get("/penerimaan/{penerimaan_id}/identifikasi", response_model=KasIdentifikasiListResponse)
def get_penerimaan_identifikasi(
    penerimaan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #02 — histori identifikasi + sisa belum teridentifikasi satu Penerimaan Kas."""
    try:
        result = identifikasi_svc.get_identifikasi_list(db, "penerimaan", penerimaan_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _identifikasi_list_response(result)


@router.post(
    "/penerimaan/{penerimaan_id}/identifikasi",
    response_model=KasIdentifikasiResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_penerimaan_identifikasi(
    penerimaan_id: UUID,
    data_in: KasIdentifikasiCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #02 — Identifikasi & Alokasi Penerimaan Kas Belum Teridentifikasi.

    Pindahkan nilai dari akun penampung ke akun-akun yang benar (bisa
    bertahap) lewat jurnal reklasifikasi — sisi Kas/Bank tidak dijurnal
    ulang (tidak ada jurnal bank ganda). Langsung posted + bisa dibatalkan
    (jurnal pembalik) lewat endpoint cancel.
    """
    try:
        obj = identifikasi_svc.create_identifikasi(
            db, "penerimaan", penerimaan_id,
            tanggal=data_in.tanggal,
            alokasi_data=[r.model_dump() for r in data_in.alokasi],
            catatan=data_in.catatan,
            created_by=current_user.id,
        )
        refreshed = identifikasi_svc.get_identifikasi_by_id(db, obj.id)
        return refreshed or obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/pembayaran/{pembayaran_id}/identifikasi",
    response_model=KasIdentifikasiResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pembayaran_identifikasi(
    pembayaran_id: UUID,
    data_in: KasIdentifikasiCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #02 — Identifikasi & Alokasi Pembayaran Kas Belum Teridentifikasi."""
    try:
        obj = identifikasi_svc.create_identifikasi(
            db, "pembayaran", pembayaran_id,
            tanggal=data_in.tanggal,
            alokasi_data=[r.model_dump() for r in data_in.alokasi],
            catatan=data_in.catatan,
            created_by=current_user.id,
        )
        refreshed = identifikasi_svc.get_identifikasi_by_id(db, obj.id)
        return refreshed or obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/pembayaran/{pembayaran_id}/identifikasi", response_model=KasIdentifikasiListResponse)
def get_pembayaran_identifikasi(
    pembayaran_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #02 — histori identifikasi + sisa belum teridentifikasi satu Pembayaran Kas."""
    try:
        result = identifikasi_svc.get_identifikasi_list(db, "pembayaran", pembayaran_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _identifikasi_list_response(result)


# ==========================================
# UPDATE #03 — IDENTIFIKASI PIUTANG / HUTANG
# (dokumen BELUM_TERIDENTIFIKASI → pihak + invoice)
# ==========================================

@router.post(
    "/penerimaan/{penerimaan_id}/identifikasi-piutang",
    response_model=KasIdentifikasiResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_penerimaan_identifikasi_piutang(
    penerimaan_id: UUID,
    data_in: IdentifikasiPiutangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #03 — Identifikasi penerimaan Belum Teridentifikasi ke pelanggan.

    User membuka transaksi penerimaan, memilih pelanggan + invoice penjualan
    outstanding, lalu sistem mereklasifikasi akun penampung ke akun piutang
    pelanggan (Dr Penampung / Dr PPh 23 Dibayar di Muka / Cr Piutang) DAN
    membuat alokasi invoice — outstanding invoice terbarui tanpa jurnal
    penerimaan bank ganda. Identifikasi penuh → dokumen menjadi
    PELUNASAN_INVOICE; pembatalan via endpoint cancel sesi.
    """
    try:
        obj = identifikasi_svc.create_identifikasi_piutang(
            db, penerimaan_id, data_in.pelanggan_id,
            tanggal=data_in.tanggal,
            alokasi_data=[r.model_dump() for r in data_in.alokasi],
            catatan=data_in.catatan,
            created_by=current_user.id,
        )
        refreshed = identifikasi_svc.get_identifikasi_by_id(db, obj.id)
        return refreshed or obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/pembayaran/{pembayaran_id}/identifikasi-hutang",
    response_model=KasIdentifikasiResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pembayaran_identifikasi_hutang(
    pembayaran_id: UUID,
    data_in: IdentifikasiHutangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #03 — Identifikasi pembayaran Belum Teridentifikasi ke supplier.

    Pilih supplier + invoice pembelian outstanding → jurnal reklasifikasi
    Cr Penampung / Dr Hutang supplier / Cr Potongan Hutang / Cr Hutang PPh 23
    + alokasi invoice; outstanding terbarui tanpa jurnal bank ganda.
    """
    try:
        obj = identifikasi_svc.create_identifikasi_hutang(
            db, pembayaran_id, data_in.supplier_id,
            tanggal=data_in.tanggal,
            alokasi_data=[r.model_dump() for r in data_in.alokasi],
            catatan=data_in.catatan,
            created_by=current_user.id,
        )
        refreshed = identifikasi_svc.get_identifikasi_by_id(db, obj.id)
        return refreshed or obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/penampung/monitoring", response_model=PenampungMonitoringResponse)
def get_penerimaan_penampung_monitoring(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #03 — monitoring saldo akun penampung + rekonsiliasi ke nol.

    Saldo GL tiap akun penampung (penerimaan 216001 / pembayaran 117004 /
    legacy 216000 + akun aktual dokumen lama) dibandingkan dengan Σ sisa
    dokumen Belum Teridentifikasi aktif; plus cek jurnal balance, jurnal
    identifikasi tidak menyentuh kas, dan tidak ada outstanding negatif
    (subledger vs General Ledger konsisten).
    """
    return identifikasi_svc.get_penampung_monitoring(db)


@router.get("/pajak/monitoring", response_model=PajakMonitoringResponse)
def get_pajak_monitoring(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """AUD-11 (update #04) — monitoring hutang pajak + riwayat pelunasan.

    Rekap saldo akun hutang pajak (TAX_PAYABLE: PPh 21/23/29/Final) dari GL
    + daftar pembayaran jenis PELUNASAN_PAJAK. Pelunasan pajak dicatat lewat
    POST /kas-bank/pembayaran dengan jenisTransaksi='PELUNASAN_PAJAK'
    (akun lawan wajib akun hutang pajak).
    """
    return svc.get_pajak_monitoring(db)


@router.post("/identifikasi/penerimaan/{identifikasi_id}/cancel", response_model=KasIdentifikasiResponse)
def cancel_penerimaan_identifikasi(
    identifikasi_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #02 — batalkan sesi identifikasi penerimaan (jurnal pembalik).

    Jurnal identifikasi asli tetap POSTED (audit trail); sisa belum
    teridentifikasi dokumen naik kembali.
    """
    try:
        obj = identifikasi_svc.cancel_identifikasi(
            db, identifikasi_id, current_user.id,
            reason=payload.reason if payload else None,
        )
        refreshed = identifikasi_svc.get_identifikasi_by_id(db, obj.id)
        return refreshed or obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/identifikasi/pembayaran/{identifikasi_id}/cancel", response_model=KasIdentifikasiResponse)
def cancel_pembayaran_identifikasi(
    identifikasi_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update #02 — batalkan sesi identifikasi pembayaran (jurnal pembalik)."""
    try:
        obj = identifikasi_svc.cancel_identifikasi(
            db, identifikasi_id, current_user.id,
            reason=payload.reason if payload else None,
        )
        refreshed = identifikasi_svc.get_identifikasi_by_id(db, obj.id)
        return refreshed or obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
