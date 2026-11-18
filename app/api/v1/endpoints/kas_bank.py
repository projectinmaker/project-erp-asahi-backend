"""
Kas & Bank Endpoints.
Pembayaran Kas, Penerimaan Kas, Pindah Buku (ex-Transfer Bank, #072).
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
# === Update #044 — Voucher Memorial (Gagal Tagih / penghapusan piutang) ===
from app.schemas.voucher_memorial import (
    VoucherMemorialCreate, VoucherMemorialResponse, VoucherMemorialSummaryResponse,
    # === Update #072 — Outstanding Memorial (uraian hutang & piutang). ===
    OutstandingMemorialResponse, OutstandingMemorialRow,
)
from app.schemas.workflow import HardDeleteRequest
from app.services import kas_bank_service as svc
from app.services import workflow_service
from app.services import voucher_memorial_service as vm_svc  # Update #044 — Gagal Tagih
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
        # === Update #071 — finalisasi mengikuti konfigurasi persetujuan jenis
        # dokumen ini (Preferensi ▸ Persetujuan): non-aktif → langsung final
        # (posting jurnal); aktif → menunggu approver (1 tahap; approve =
        # final) lewat kolom "Persetujuan" di daftar dokumen. ===
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
        # === Update #071 — finalisasi mengikuti konfigurasi persetujuan jenis
        # dokumen ini (Preferensi ▸ Persetujuan): non-aktif → langsung final
        # (posting jurnal); aktif → menunggu approver (1 tahap; approve =
        # final) lewat kolom "Persetujuan" di daftar dokumen. ===
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
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Pindah Buku (ex-Transfer Bank — rename #072) dengan filter dan pagination."""
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
    """Buat Pindah Buku baru (auto-generate no transfer + auto-post jurnal)."""
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
        # === Update #071 — finalisasi mengikuti konfigurasi persetujuan jenis
        # dokumen ini (Preferensi ▸ Persetujuan): non-aktif → langsung final
        # (posting jurnal); aktif → menunggu approver (1 tahap; approve =
        # final) lewat kolom "Persetujuan" di daftar dokumen. ===
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
    """Ambil detail 1 Pindah Buku."""
    item = svc.get_transfer_by_id(db, transfer_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pindah Buku tidak ditemukan")
    return item


@router.put("/transfer/{transfer_id}", response_model=TransferBankResponse)
def update_transfer(
    transfer_id: UUID,
    data_in: TransferBankUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Pindah Buku."""
    item = svc.get_transfer_by_id(db, transfer_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pindah Buku tidak ditemukan")
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
    """Hapus permanen Pindah Buku (hard delete).

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
    """Reverse Pindah Buku yang sudah SELESAI.

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
# === Update #044 — VOUCHER MEMORIAL (GAGAL TAGIH / PENGHAPUSAN PIUTANG) ===
# ==========================================
# Cabang "Gagal Tagih" alur Penerimaan Piutang (revisi pemilik, poin 5):
# Penerimaan Penuh / Sebagian → Voucher Penerimaan Bank/Kas (PenerimaanKas);
# Gagal Tagih → Voucher Memorial (Adjustment) — dokumen non-kas ini.
# Jurnal sistem: Dr Beban Penghapusan Piutang, Cr Piutang Usaha (kontrol).
# === Update #072 — jenis HUTANG (Outstanding Memorial): Dr Hutang Usaha
# (kontrol), Cr Pendapatan Penghapusan Hutang. Gagal Tagih kini berlaku
# untuk hutang juga (permintaan user), dibuat lewat aksi daftar
# Outstanding Memorial. ===

def _voucher_response(item):
    """Bangun response + no_invoice per baris rincian (relasi sales_invoice /
    purchase_invoice di-eager-load get_voucher_by_id / get_voucher_list)."""
    resp = VoucherMemorialResponse.model_validate(item)
    for r, d in zip(item.rincian, resp.rincian):
        # === Update #072 — rincian jenis HUTANG memuat invoice pembelian. ===
        inv = r.purchase_invoice if (item.jenis or 'PIUTANG') == 'HUTANG' else r.sales_invoice
        if inv is not None:
            d.no_invoice = inv.no_form if (item.jenis or 'PIUTANG') == 'HUTANG' else inv.no_invoice
    return resp


# === Update #072 — Outstanding Memorial: uraian HUTANG & PIUTANG (per
# invoice outstanding) + aksi Penerimaan/Pembayaran Penuh, Sebagian, dan
# Gagal Tagih (diproses frontend lewat endpoint pelunasan & voucher
# memorial). Kolom daftar (permintaan user): no dokumen, tanggal, nama
# pelanggan/supplier, inv, nominal, status, aksi. ===
@router.get("/outstanding-memorial", response_model=OutstandingMemorialResponse)
def get_outstanding_memorial_list(
    jenis: Optional[str] = Query(None, description="Filter jenis: PIUTANG | HUTANG (kosong = keduanya)"),
    status_pembayaran: Optional[str] = Query(None, alias="statusPembayaran", description="BELUM_DIBAYAR / PARSIAL / LUNAS / LEBIH_BAYAR"),
    pihak_id: Optional[UUID] = Query(None, alias="pihakId", description="Filter pelanggan/supplier"),
    as_of: Optional[date] = Query(None, alias="asOf"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar outstanding hutang & piutang per invoice (Outstanding Memorial).

    Default hanya menampilkan invoice dengan sisa > 0 (BELUM_DIBAYAR/PARSIAL);
    ``statusPembayaran=LUNAS`` diterima untuk keperluan penelusuran.
    Nominal = sisa tagihan/hutang (net setelah bayar, retur, DP, dan
    penghapusan gagal tagih aktif).
    """
    from app.models import Pelanggan, Supplier
    from app.services import settlement_service as settlement

    jenis_list = ['PIUTANG', 'HUTANG']
    if jenis:
        jenis = jenis.upper()
        if jenis not in jenis_list:
            raise HTTPException(400, "Jenis harus PIUTANG atau HUTANG")
        jenis_list = [jenis]

    rows: list = []
    totals = {'PIUTANG': settlement.ZERO, 'HUTANG': settlement.ZERO}
    for j in jenis_list:
        ar = j == 'PIUTANG'
        model = settlement.invoice_model('piutang' if ar else 'hutang')
        query = db.query(model).filter(model.id.in_(
            settlement.active_documents(db, model, as_of).scalar_subquery()))
        if pihak_id:
            query = query.filter((model.pelanggan_id if ar else model.supplier_id) == pihak_id)
        invoices = query.order_by(model.tanggal, model.id).all()
        summaries = settlement.balances(db, invoices, as_of)

        # Nama pihak (pelanggan/supplier) + referensi eksternal.
        pihak_ids = {s['pihak_id'] for s in summaries.values() if s['pihak_id']}
        pihak_model = Pelanggan if ar else Supplier
        pihak_names = {}
        if pihak_ids:
            pihak_names = {p.id: p.nama for p in db.query(pihak_model).filter(pihak_model.id.in_(pihak_ids)).all()}

        # Referensi SO untuk piutang (invoice dari SO).
        so_map = {}
        if ar:
            from app.models import SalesOrder
            so_ids = {i.sales_order_id for i in invoices if getattr(i, 'sales_order_id', None)}
            if so_ids:
                so_map = {s.id: s.no_pesanan for s in db.query(SalesOrder).filter(SalesOrder.id.in_(so_ids)).all()}

        for obj in invoices:
            s = summaries[obj.id]
            if not s['aktif']:
                continue
            # Default (tanpa filter status): hanya yang masih ada sisanya —
            # uraian hutang/piutang. Filter eksplisit dihormati.
            if status_pembayaran is None and s['sisa_tagihan'] <= 0:
                continue
            if status_pembayaran is not None and s['status_pembayaran'] != status_pembayaran:
                continue
            inv_ref = None
            if ar:
                inv_ref = so_map.get(getattr(obj, 'sales_order_id', None)) or '-'
            else:
                inv_ref = getattr(obj, 'no_faktur', None) or '-'
            row = OutstandingMemorialRow(
                jenis=j,
                invoice_id=obj.id,
                pihak_id=s['pihak_id'],
                no_dokumen=s['no_dokumen'],
                tanggal=obj.tanggal,
                pihak_nama=pihak_names.get(s['pihak_id'], '-') or '-',
                inv=inv_ref,
                nilai_tagihan=s['nilai_tagihan'],
                total_bayar=s['total_bayar'],
                total_retur=s['total_retur'],
                uang_muka=s.get('uang_muka', settlement.ZERO),
                total_gagal_tagih=s.get('total_gagal_tagih', settlement.ZERO),
                nominal=s['sisa_tagihan'],
                kelebihan=s['kelebihan'],
                status=s['status_pembayaran'],
                jatuh_tempo=s['jatuh_tempo'],
                aktif=True,
            )
            rows.append(row)
            totals[j] += s['sisa_tagihan']

    # Urut: tanggal terlama dulu (kronologis uraian hutang/piutang).
    rows.sort(key=lambda r: (r.tanggal, r.jenis))
    total = len(rows)
    return {
        'total_piutang': totals['PIUTANG'],
        'total_hutang': totals['HUTANG'],
        'data': rows[skip:skip + limit],
        'total': total,
        'skip': skip,
        'limit': limit,
    }


@router.get("/voucher-memorial", response_model=PaginatedResponse[VoucherMemorialResponse])
def get_voucher_memorial_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no bukti / catatan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    pelanggan_id: Optional[UUID] = Query(None, description="Filter pelanggan"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    # === Update #072 — filter jenis (PIUTANG/HUTANG) daftar dokumen. ===
    jenis: Optional[str] = Query(None, description="Filter jenis: PIUTANG | HUTANG"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar Voucher Memorial (Gagal Tagih) dengan filter & pagination."""
    data, total = vm_svc.get_voucher_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, pelanggan_id=pelanggan_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
        jenis=jenis,
    )
    return {"data": [_voucher_response(i) for i in data], "total": total, "skip": skip, "limit": limit}


@router.get("/voucher-memorial/next-nomor", response_model=NomorPreviewResponse)
def get_voucher_memorial_next_nomor(
    tanggal: Optional[date] = Query(
        None, description="Tanggal dokumen (YYYY-MM-DD) — menentukan tahun sequence"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Preview nomor Voucher Memorial BERIKUTNYA (indikatif, tanpa create).

    Dipakai form Gagal Tagih untuk menampilkan "VM ASI/YYYY/NNN" sebelum submit.
    """
    return {"nomor": vm_svc.peek_nomor_voucher_memorial(db, tanggal=tanggal)}


@router.post("/voucher-memorial", response_model=VoucherMemorialSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_voucher_memorial(
    data_in: VoucherMemorialCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Voucher Memorial (Gagal Tagih) — penghapusan non-kas.

    === Update #072 — jenis HUTANG: penghapusan HUTANG atas supplier
    (aksi Gagal Tagih baris hutang di Outstanding Memorial; pihak dikirim
    lewat supplierId). Jurnal: Dr Hutang Usaha (kontrol), Cr Pendapatan
    Penghapusan Hutang. ===

    === Update #071 — finalisasi mengikuti konfigurasi persetujuan jenis
    voucher_memorial (Preferensi ▸ Persetujuan): non-aktif → langsung final
    (posting jurnal Dr Beban / Cr Piutang dalam satu transaksi); aktif →
    menunggu approver lewat kolom "Persetujuan" daftar Voucher Memorial. ===
    """
    jenis = (data_in.jenis or "PIUTANG").upper()
    if jenis not in ("PIUTANG", "HUTANG"):
        raise HTTPException(status_code=400, detail="Jenis Voucher Memorial harus PIUTANG atau HUTANG")
    pihak_id = data_in.pelanggan_id if jenis == "PIUTANG" else data_in.supplier_id
    if pihak_id is None:
        field = "pelangganId" if jenis == "PIUTANG" else "supplierId"
        raise HTTPException(status_code=400, detail=f"{field} wajib diisi untuk Voucher Memorial jenis {jenis}")
    try:
        obj = vm_svc.create_gagal_tagih(
            db=db,
            pihak_id=pihak_id,
            tanggal=data_in.tanggal,
            allocation_data=[r.model_dump() for r in data_in.alokasi],
            created_by=current_user.id,
            catatan=data_in.catatan,
            akun_beban_id=data_in.akun_beban_id,
            jenis=jenis,
        )
        # === Update #071 — finalisasi mengikuti konfigurasi persetujuan
        # (mirror create_penerimaan / settlement — lihat catatan create_pembayaran). ===
        workflow_service.direct_complete(db, current_user, 'voucher_memorial', obj.id)
        db.refresh(obj)
        return {
            'id': obj.id, 'no_bukti': obj.no_bukti, 'tanggal': obj.tanggal,
            'jenis': jenis,
            'total_nilai': obj.total_nilai,
            'status': getattr(obj.status, 'value', obj.status),
            'jurnal_umum_id': obj.jurnal_umum_id,
            'pelanggan_id': obj.pelanggan_id,
            'supplier_id': obj.supplier_id,
            'akun_beban_id': obj.akun_beban_id,
            'alokasi': [
                {'invoice_id': (r.sales_invoice_id or r.purchase_invoice_id), 'nilai': r.nilai}
                for r in obj.rincian
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/voucher-memorial/{voucher_id}", response_model=VoucherMemorialResponse)
def get_voucher_memorial_detail(
    voucher_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Voucher Memorial (dipakai tab Cetak Voucher Memorial)."""
    item = vm_svc.get_voucher_by_id(db, voucher_id)
    if not item:
        raise HTTPException(status_code=404, detail="Voucher Memorial tidak ditemukan")
    return _voucher_response(item)


@router.post("/voucher-memorial/{voucher_id}/cancel")
def cancel_voucher_memorial(
    voucher_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan/hapus Voucher Memorial (mirror cancel penerimaan).

    - DRAFT (belum ber-jurnal) → hard delete + snapshot ke log dokumen terhapus.
    - Sudah diposting → jurnal pembalik (Dr Piutang, Cr Beban) + status BATAL,
      sisa tagihan invoice otomatis kembali (balances menghormati pembalik).
    """
    try:
        return cancel_document(
            db, 'voucher_memorial', voucher_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
