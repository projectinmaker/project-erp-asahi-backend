"""
Penjualan Endpoints.
SalesOrder, SalesInvoice, SalesRetur, PengirimanBarang.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.transaksi.penjualan.sales_order import StatusPenjualan
from app.models.transaksi.jurnal import JurnalUmum, RefModule
from sqlalchemy import func as sa_func
from app.schemas.base import PaginatedResponse
from app.schemas.penjualan import (
    SalesOrderCreate, SalesOrderUpdate, SalesOrderResponse,
    SalesInvoiceCreate, SalesInvoiceUpdate, SalesInvoiceResponse,
    SalesReturCreate, SalesReturUpdate, SalesReturResponse,
    PengirimanBarangCreate, PengirimanBarangUpdate, PengirimanBarangResponse,
    SalesOrderSisaResponse,
    PengirimanSisaResponse,
    PenawaranCreate, PenawaranUpdate, PenawaranResponse,
    # === Update #031 — No Quotation + katalog item + termin SO ===
    PenawaranKatalogItemResponse,
    NomorQuotationPreviewResponse,
    SoTerminResponse,
    SoTerminOpenItem,
    # === Update #032 — dropdown penawaran utk form SO ===
    PenawaranUntukSoItem,
    TerimaPembayaranTerminRequest,
)
# Update #031 — response penerimaan termin SO (mirror kas_bank endpoint).
from app.schemas.kas_bank import PenerimaanKasResponse
from app.schemas.tukar_faktur import (
    TukarFakturCreate, TukarFakturUpdate, TukarFakturResponse,
)
from app.services import penjualan_service as svc
from app.services import penawaran_service
from app.services import tukar_faktur_service
from app.services import workflow_service
from app.services.cancel_document_service import cancel_document
from app.services.sales_validation import (
    get_qty_delivered_so_far,
    get_qty_invoiced_for_so_detail,
    get_qty_invoiced_so_far,
)
from app.schemas.workflow import HardDeleteRequest


def _service_kwargs(fn, data: dict) -> dict:
    """Saring field payload agar hanya parameter yang diterima service yang diteruskan.

    Mencegah HTTP 500 (TypeError) ketika schema menerima field yang tidak
    didukung signature service (mis. details/customer_po_*/currency pada PUT).
    Field tak didukung diabaikan secara diam-diam (update header-only).
    """
    import inspect
    params = inspect.signature(fn).parameters
    return {k: v for k, v in data.items() if k in params}

router = APIRouter()


# ==========================================
# SALES ORDER
# ==========================================

# === Update #031 — termin pembayaran SO (dideklarasikan SEBELUM route
# /sales-order/{so_id} supaya path literal tidak tertelan param UUID) ===


@router.get("/sales-order/termin-open", response_model=list[SoTerminOpenItem])
def get_sales_order_termin_open(
    search: str | None = Query(None, description="Cari no pesanan / nama pelanggan"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """SO yang masih punya termin BELUM dibayar — pilihan di form Penerimaan.

    Satu SO bisa dipanggil berkali-kali untuk pembayaran (tiap penerimaan
    membayar SATU termin — Update #031).
    """
    try:
        return svc.get_open_termin_sos(db, search=search, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/sales-order", response_model=PaginatedResponse[SalesOrderResponse])
def get_sales_order_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no pesanan, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    pelanggan_id: Optional[UUID] = Query(None, description="Filter pelanggan"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Sales Order dengan filter dan pagination."""
    data, total = svc.get_sales_order_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, pelanggan_id=pelanggan_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/sales-order", response_model=SalesOrderResponse, status_code=status.HTTP_201_CREATED)
def create_sales_order(
    data_in: SalesOrderCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Sales Order baru.

    Update #031: SO LANGSUNG JADI tanpa workflow — finalisasi dipaksa untuk
    semua user (bukan hanya admin). Pembayaran termin (baris nominal) opsional.
    """
    try:
        details_data = [d.model_dump() for d in data_in.details]
        biaya_data = [b.model_dump() for b in data_in.biaya_tambahan]
        obj = svc.create_sales_order(
            db=db,
            tanggal=data_in.tanggal,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            biaya_data=biaya_data,
            syarat_bayar_id=data_in.syarat_bayar_id,
            # Update ASAHI (revisi SO): ekspedisi/penjual dihapus;
            # customer_code = singkatan customer → segmen nomor "SO ASI/TBI/2026/001".
            customer_code=data_in.customer_code,
            tanggal_pengiriman=data_in.tanggal_pengiriman,
            alamat_pengiriman=data_in.alamat_pengiriman,
            diskon_global=data_in.diskon_global,
            keterangan=data_in.keterangan,
            auto_post_jurnal=False,
            created_by=current_user.id,
            customer_po_number=data_in.customer_po_number,
            customer_po_date=data_in.customer_po_date,
            currency=data_in.currency,
            # === Update #042 — PPN dihapus dari SO (resolve/pass ppn dihapus;
            # pajak hanya dihitung di INVOICE). ===
            # === Update #031 — Pembayaran Termin (baris nominal) ===
            pembayaran_termin=list(data_in.pembayaran_termin or []),
            # === Update #032 — link penawaran sumber ("Pilih Penawaran" di
            # form SO; repeat order — satu penawaran boleh dipakai banyak SO) ===
            penawaran_id=data_in.penawaran_id,
        )
        # Update #031 — SO langsung final tanpa langkah approval untuk SEMUA
        # user (force=True); order berhenti di APPROVED (terminal, tanpa jurnal).
        workflow_service.direct_complete(db, current_user, 'sales_order', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/sales-order/{so_id}", response_model=SalesOrderResponse)
def get_sales_order_detail(
    so_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Sales Order."""
    item = svc.get_sales_order_by_id(db, so_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Order tidak ditemukan")
    return item


@router.get("/sales-order/{so_id}/sisa", response_model=SalesOrderSisaResponse)
def get_sales_order_sisa(
    so_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Sisa qty per baris Sales Order (fitur tarik data — Update #3).

    - qtyTerkirim = SUM qty pengiriman berstatus DIPROSES/SELESAI untuk baris
      SO tersebut (mirror sales_validation.get_qty_delivered_so_far).
    - qtyTerfaktur = QTY EFEKTIF invoice berstatus DIPROSES/SELESAI lewat dua
      jalur link: sales_order_detail_id langsung ATAU delivery_detail_id dari
      pengiriman baris SO tersebut (Update #042 — Σ sub_total/harga bila
      harga > 0, supaya invoice DP parsial tidak menghabiskan sisa qty).
    - sisaKirim = qtyPesanan - qtyTerkirim (min 0);
      sisaFaktur = qtyPesanan - qtyTerfaktur (min 0).
    - Update #032: baris ITEM BEBAS (barang_id None) ikut keluar dengan
      is_bebas=True — tidak bisa dikirim (UI pengiriman menyembunyikan)
      tapi bisa difakturkan (sisaFaktur dihitung normal via link detail).
    - Update #042: sisaNilai = sisa tagihan NILAI SO = max(0, grand_total −
      Σ nilai-tagih invoice non-DIBATALKAN; nilai-tagih = dp_pelunasan bila
      > 0 selain itu sub_total) — patokan DP/Pelunasan invoice berikutnya.
      Field dp per baris dihapus (DP/Pelunasan kini header invoice).
    """
    so = svc.get_sales_order_by_id(db, so_id)
    if not so:
        raise HTTPException(status_code=404, detail="Sales Order tidak ditemukan")

    details = []
    for d in so.details:
        qty_terkirim = get_qty_delivered_so_far(db, d.id)
        qty_terfaktur = get_qty_invoiced_for_so_detail(db, d.id)
        is_bebas = d.barang_id is None
        details.append({
            "sales_order_detail_id": d.id,
            "barang_id": d.barang_id,
            "item_nama": d.item_nama,
            "item_satuan": d.item_satuan,
            "is_bebas": is_bebas,
            "kode_barang": d.barang.kode if d.barang else None,
            "nama_barang": (d.barang.nama if d.barang else d.item_nama),
            "satuan_id": d.satuan_id,
            "satuan_nama": (d.satuan.nama if d.satuan else d.item_satuan),
            "qty_pesanan": d.qty,
            "qty_terkirim": qty_terkirim,
            "qty_terfaktur": qty_terfaktur,
            "sisa_kirim": max(d.qty - qty_terkirim, 0),
            "sisa_faktur": max(d.qty - qty_terfaktur, 0),
            "harga": float(d.harga or 0),
            "diskon": float(d.diskon or 0),
        })

    # === Update #042 — sisa tagihan NILAI SO (J): default dp_pelunasan
    # invoice berikutnya & guard Σ nilai-tagih ≤ grand_total SO. ===
    sisa_nilai = svc._so_sisa_nilai(db, so_id)

    return {
        "sales_order_id": so.id,
        "pelanggan_id": so.pelanggan_id,
        "syarat_bayar_id": so.syarat_bayar_id,
        "no_pesanan": so.no_pesanan,
        "alamat_pengiriman": so.alamat_pengiriman,
        "sisa_nilai": float(sisa_nilai),
        "details": details,
    }


@router.put("/sales-order/{so_id}", response_model=SalesOrderResponse)
def update_sales_order(
    so_id: UUID,
    data_in: SalesOrderUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Sales Order (header only).

    Field schema yang tidak didukung service (details/customer_po_*/currency)
    diabaikan agar tidak memicu HTTP 500 — update bersifat header-only.
    """
    item = svc.get_sales_order_by_id(db, so_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Order tidak ditemukan")

    update_data = _service_kwargs(svc.update_sales_order, data_in.model_dump(exclude_unset=True))
    try:
        return svc.update_sales_order(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sales-order/{so_id}/cancel")
def cancel_sales_order(
    so_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Sales Order (hard delete).

    Dokumen + rincian + dokumen terkait dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'sales_order', so_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# Update #031 — TERMIN PEMBAYARAN SALES ORDER
# ==========================================

@router.get("/sales-order/{so_id}/termin", response_model=list[SoTerminResponse])
def get_sales_order_termin(
    so_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar termin pembayaran SO (urut) + status bayar tiap baris."""
    so = svc.get_sales_order_by_id(db, so_id)
    if not so:
        raise HTTPException(status_code=404, detail="Sales Order tidak ditemukan")
    termins = svc.get_sales_order_termins(db, so_id)
    hasil = []
    for t in termins:
        hasil.append(
            {
                "id": t.id,
                "sales_order_id": t.sales_order_id,
                "urutan": t.urutan,
                "nominal": t.nominal,
                "penerimaan_kas_id": t.penerimaan_kas_id,
                "dibayar_pada": t.dibayar_pada,
                "no_bukti_penerimaan": t.penerimaan.no_bukti if t.penerimaan else None,
                "status_penerimaan": (
                    getattr(t.penerimaan.status, "value", t.penerimaan.status)
                    if t.penerimaan else None
                ),
            }
        )
    return hasil


@router.post(
    "/sales-order/{so_id}/terima-pembayaran",
    response_model=PenerimaanKasResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sales_order_terima_pembayaran(
    so_id: UUID,
    data_in: TerimaPembayaranTerminRequest,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Terima pembayaran SATU termin SO (jenis Penerimaan TERMIN_SO) — #031.

    - Satu SO bisa dipanggil BERKALI-KALI (tiap penerimaan = satu termin).
    - Jurnal (saat diposting): D Kas/Bank, K akun lawan. Update #036 — akun
      lawan default kontekstual: SO belum difakturkan → Uang Muka Penjualan
      (Setting Akun UANG_MUKA_PENJUALAN; diterapkan otomatis ke Piutang saat
      invoice SO diposting); SO sudah punya invoice aktif → akun Piutang
      invoice (langsung melunasi + alokasi pembayaran invoice). Pilihan
      akun eksplisit tetap bisa diganti per transaksi.
    - Pembatalan penerimaan melepas termin otomatis (bisa dibayar ulang).
    """
    try:
        penerimaan, _termin = svc.terima_pembayaran_termin(
            db=db,
            so_id=so_id,
            termin_id=data_in.termin_id,
            kas_bank_id=data_in.kas_bank_id,
            tanggal=data_in.tanggal,
            created_by=current_user.id,
            akun_perkiraan_id=data_in.akun_perkiraan_id,
            no_nukti=data_in.no_nukti,
            catatan=data_in.catatan,
        )
        # Mirror endpoint POST /kas-bank/penerimaan: administrator langsung
        # final (posting jurnal); user biasa menunggu approval workflow.
        workflow_service.direct_complete(db, current_user, 'penerimaan_kas', penerimaan.id)
        db.refresh(penerimaan)
        return penerimaan
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


# ==========================================
# SALES INVOICE
# ==========================================

@router.get("/sales-invoice", response_model=PaginatedResponse[SalesInvoiceResponse])
def get_sales_invoice_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no invoice, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    pelanggan_id: Optional[UUID] = Query(None, description="Filter pelanggan"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Sales Invoice dengan filter dan pagination."""
    data, total = svc.get_sales_invoice_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, pelanggan_id=pelanggan_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/sales-invoice", response_model=SalesInvoiceResponse, status_code=status.HTTP_201_CREATED)
def create_sales_invoice(
    data_in: SalesInvoiceCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Sales Invoice baru (auto-generate no invoice + auto-post jurnal)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        biaya_data = [b.model_dump() for b in data_in.biaya_tambahan]
        obj = svc.create_sales_invoice(
            db=db,
            tanggal_jatuh_tempo=data_in.tanggal_jatuh_tempo,
            tanggal=data_in.tanggal,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            biaya_data=biaya_data,
            syarat_bayar_id=data_in.syarat_bayar_id,
            sales_order_id=data_in.sales_order_id,
            ekspedisi=data_in.ekspedisi,
            tanggal_pengiriman=data_in.tanggal_pengiriman,
            alamat_pengiriman=data_in.alamat_pengiriman,
            mata_uang=data_in.mata_uang,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            # === Update ASAHI — pilihan PPh23/PPN saat input SI ===
            ppn_applicable=data_in.ppn_applicable,
            pph23_applicable=data_in.pph23_applicable,
            pph23=data_in.pph23,
            keterangan=data_in.keterangan,
            auto_post_jurnal=False,  # Posting requires approved workflow.
            created_by=current_user.id,
            # === Update #042 — DP/Pelunasan header: nilai yang ditagih
            # invoice ini (None = auto-isi sisa tagihan SO bila ter-link). ===
            dp_pelunasan=data_in.dp_pelunasan,
        )
        # Update #043 — langsung final tanpa langkah approval untuk SEMUA user
        # (workflow maker-checker di modul penjualan/pembelian di-takedown).
        workflow_service.direct_complete(db, current_user, 'sales_invoice', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/sales-invoice/{inv_id}", response_model=SalesInvoiceResponse)
def get_sales_invoice_detail(
    inv_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Sales Invoice."""
    item = svc.get_sales_invoice_by_id(db, inv_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Invoice tidak ditemukan")
    return item


@router.put("/sales-invoice/{inv_id}", response_model=SalesInvoiceResponse)
def update_sales_invoice(
    inv_id: UUID,
    data_in: SalesInvoiceUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Sales Invoice (header only)."""
    item = svc.get_sales_invoice_by_id(db, inv_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Invoice tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_sales_invoice(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sales-invoice/{inv_id}/cancel")
def cancel_sales_invoice(
    inv_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Sales Invoice (hard delete).

    Dokumen + rincian + jurnal terkait dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'sales_invoice', inv_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# SALES RETUR
# ==========================================

@router.get("/sales-retur", response_model=PaginatedResponse[SalesReturResponse])
def get_sales_retur_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no retur, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    pelanggan_id: Optional[UUID] = Query(None, description="Filter pelanggan"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Sales Retur dengan filter dan pagination."""
    data, total = svc.get_sales_retur_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, pelanggan_id=pelanggan_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/sales-retur", response_model=SalesReturResponse, status_code=status.HTTP_201_CREATED)
def create_sales_retur(
    data_in: SalesReturCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Sales Retur baru (auto-generate no retur + auto-post jurnal)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        obj = svc.create_sales_retur(
            db=db,
            pengiriman_id=data_in.pengiriman_id,
            gudang_id=data_in.gudang_id,
            tanggal=data_in.tanggal,
            sales_invoice_id=data_in.sales_invoice_id,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            alamat_pengembalian=data_in.alamat_pengembalian,
            no_pengembalian=data_in.no_pengembalian,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=False,  # Posting requires approved workflow.
            created_by=current_user.id,
        )
        # Update #043 — langsung final tanpa langkah approval untuk SEMUA user
        # (workflow maker-checker di modul penjualan/pembelian di-takedown).
        workflow_service.direct_complete(db, current_user, 'sales_retur', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/sales-retur/{retur_id}", response_model=SalesReturResponse)
def get_sales_retur_detail(
    retur_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Sales Retur."""
    item = svc.get_sales_retur_by_id(db, retur_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Retur tidak ditemukan")
    return item


@router.put("/sales-retur/{retur_id}", response_model=SalesReturResponse)
def update_sales_retur(
    retur_id: UUID,
    data_in: SalesReturUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Sales Retur (header only)."""
    item = svc.get_sales_retur_by_id(db, retur_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Retur tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_sales_retur(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sales-retur/{retur_id}/cancel")
def cancel_sales_retur(
    retur_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Sales Retur (hard delete).

    Dokumen + rincian + jurnal terkait dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'sales_retur', retur_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PENGIRIMAN BARANG
# ==========================================

@router.get("/pengiriman", response_model=PaginatedResponse[PengirimanBarangResponse])
def get_pengiriman_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no surat jalan, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    pelanggan_id: Optional[UUID] = Query(None, description="Filter pelanggan"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Pengiriman Barang dengan filter dan pagination."""
    data, total = svc.get_pengiriman_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, pelanggan_id=pelanggan_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/pengiriman", response_model=PengirimanBarangResponse, status_code=status.HTTP_201_CREATED)
def create_pengiriman(
    data_in: PengirimanBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Pengiriman Barang baru (auto-generate no surat jalan)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        obj = svc.create_pengiriman(
            db=db,
            gudang_id=data_in.gudang_id,
            tanggal=data_in.tanggal,
            sales_order_id=data_in.sales_order_id,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            ekspedisi=data_in.ekspedisi,
            alamat_pengiriman=data_in.alamat_pengiriman,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
        )
        # Update #043 — langsung final tanpa langkah approval untuk SEMUA user
        # (workflow maker-checker di modul penjualan/pembelian di-takedown).
        workflow_service.direct_complete(db, current_user, 'pengiriman_barang', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/pengiriman/{pengiriman_id}", response_model=PengirimanBarangResponse)
def get_pengiriman_detail(
    pengiriman_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Pengiriman Barang."""
    item = svc.get_pengiriman_by_id(db, pengiriman_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pengiriman Barang tidak ditemukan")
    return item


@router.get("/pengiriman/{pengiriman_id}/sisa", response_model=PengirimanSisaResponse)
def get_pengiriman_sisa(
    pengiriman_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Sisa qty per baris DO yang belum difakturkan (fitur tarik No DO — Update #004).

    Dipakai form Invoice Penjualan saat user memilih No DO (mirror pola tarik
    No SO lewat /sales-order/{id}/sisa):
    - qtyTerfaktur = SUM qty invoice detail berstatus DIPROSES/SELESAI yang
      ter-link ke baris DO via delivery_detail_id.
    - sisaFaktur = qtyKirim - qtyTerfaktur (min 0); form hanya menarik baris
      dengan sisa > 0.
    - harga/diskon ditarik dari line SO asal bila baris DO ter-link
      (sales_order_detail_id); tanpa link → 0 (isi manual).
    """
    pb = svc.get_pengiriman_by_id(db, pengiriman_id)
    if not pb:
        raise HTTPException(status_code=404, detail="Pengiriman Barang tidak ditemukan")

    details = []
    for d in pb.details:
        qty_terfaktur = get_qty_invoiced_so_far(db, d.id)
        so_line = d.sales_order_detail
        details.append({
            "delivery_detail_id": d.id,
            "sales_order_detail_id": d.sales_order_detail_id,
            # === Update #041 — barang_id bisa None (baris DO ITEM BEBAS — SO
            # manufaktur/jasa/custom); nama/satuan fallback ke item bebas. ===
            "barang_id": d.barang_id,
            "item_nama": d.item_nama,
            "item_satuan": d.item_satuan,
            "kode_barang": d.barang.kode if d.barang else None,
            "nama_barang": d.barang.nama if d.barang else d.item_nama,
            "satuan_id": d.satuan_id,
            "satuan_nama": d.satuan.nama if d.satuan else d.item_satuan,
            "qty_kirim": d.qty,
            "qty_terfaktur": qty_terfaktur,
            "sisa_faktur": max(d.qty - qty_terfaktur, 0),
            "harga": float(so_line.harga or 0) if so_line is not None else 0.0,
            "diskon": float(so_line.diskon or 0) if so_line is not None else 0.0,
            # === Update #042 — dp baris SO dihapus (nilai tagih kini header
            # dpPelunasan invoice; distribusi proporsional otomatis). ===
        })

    return {
        "pengiriman_id": pb.id,
        "no_surat_jalan": pb.no_surat_jalan,
        "tanggal": pb.tanggal,
        "sales_order_id": pb.sales_order_id,
        "no_pesanan": pb.sales_order.no_pesanan if pb.sales_order else None,
        "pelanggan_id": pb.pelanggan_id,
        "pelanggan_nama": pb.pelanggan.nama if pb.pelanggan else None,
        "alamat_pengiriman": pb.alamat_pengiriman,
        "gudang_id": pb.gudang_id,
        "details": details,
    }


@router.put("/pengiriman/{pengiriman_id}", response_model=PengirimanBarangResponse)
def update_pengiriman(
    pengiriman_id: UUID,
    data_in: PengirimanBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Pengiriman Barang (header only).

    Catatan: service belum mendukung penggantian detail — field `details`
    pada schema diabaikan (bukan diteruskan sebagai details_data yang tidak
    dikenal service dan memicu HTTP 500).
    """
    item = svc.get_pengiriman_by_id(db, pengiriman_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pengiriman Barang tidak ditemukan")

    update_data = _service_kwargs(svc.update_pengiriman, data_in.model_dump(exclude_unset=True))
    try:
        return svc.update_pengiriman(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pengiriman/{pengiriman_id}/cancel")
def cancel_pengiriman(
    pengiriman_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Pengiriman Barang (hard delete).

    Dokumen + rincian dihapus dari database (guard: stok sudah bergerak → tolak);
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'pengiriman_barang', pengiriman_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pengiriman/{pengiriman_id}/reverse", response_model=PengirimanBarangResponse)
def reverse_pengiriman(
    pengiriman_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
    reason: Optional[str] = Query(None, description="Alasan reversal (optional)"),
):
    """Reverse Pengiriman Barang yang sudah di-finish (status SELESAI).

    Phase 4 — Master Roadmap §13: "Controlled reversal/return flow".

    Berbeda dengan cancel (yang hanya untuk status DRAFT/DIPROSES), reverse
    bisa untuk pengiriman yang sudah SELESAI — stok sudah berkurang & HPP
    journal sudah posted.

    Reverse akan:
    1. Reverse HPP journal (Dr Persediaan / Cr HPP — pembalik dari saat finish)
    2. Reverse stock movement (call reverse_stock_movement untuk setiap detail)
       - Restore exact layer FIFO/FEFO (re-create layer dengan cost asli)
       - Update StockBalance (qty + nilai)
       - Recalculate master barang.stok & harga_pokok
    3. Set status PengirimanBarang ke DIBATALKAN
    4. Catat reversal_of_id di setiap StokMutasi reversal (audit trail)

    Tidak bisa reverse kalau:
    - Sudah ada invoice yang memakai delivery ini (POSTED/DIPROSES)
      → batalkan/reverse invoice terlebih dahulu
    - Stok gudang sudah terpakai transaksi lain (mis. sudah terjual)
      → akan return error dengan detail stok yang kurang
    """
    from app.services.inventory_reversal_service import reverse_delivery

    try:
        result = reverse_delivery(
            db, pengiriman_id, current_user.id,
            reason=reason or "Reversal pengiriman"
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# INVOICE BELUM BAYAR (untuk Pembayaran Kas)
# ==========================================

@router.get("/invoice-belum-bayar/{pelanggan_id}")
def get_invoice_belum_bayar(
    pelanggan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """List invoice penjualan yang belum dibayar penuh untuk suatu pelanggan.

    Digunakan di form Pembayaran Kas untuk cascade dropdown:
    Pilih Piutang -> Pilih Pelanggan -> muncul list invoice.

    Return list SalesInvoiceResponse sederhana dengan field tambahan `sisaTagihan`.
    """
    from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
    from decimal import Decimal

    # Validasi pelanggan
    from app.models.master.pelanggan import Pelanggan
    pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
    if not pelanggan:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")

    # Ambil semua invoice aktif (bukan BATAL) untuk pelanggan ini
    invoices = (
        db.query(SalesInvoice)
        .filter(
            SalesInvoice.pelanggan_id == pelanggan_id,
            SalesInvoice.status != StatusPenjualan.DIBATALKAN,
        )
        .order_by(SalesInvoice.tanggal.desc())
        .all()
    )

    result = []
    for inv in invoices:
        # Hitung total yang sudah dibayar dari jurnal penerimaan kas.
        # Catatan: RefModule PENERIMAAN (legacy) dan AR_SETTLEMENT (canonical)
        # keduanya harus di-include supaya data historis & transaksi baru
        # sama-sama terbaca di AR aging.
        total_bayar = Decimal("0")
        if inv.jurnal_umum_id:
            # Cari jurnal penerimaan yang merujuk invoice ini
            bayar_rows = (
                db.query(sa_func.coalesce(sa_func.sum(JurnalUmum.total_kredit), 0))
                .filter(
                    JurnalUmum.ref_module.in_([
                        RefModule.PENERIMAAN,      # legacy generic receipt
                        RefModule.AR_SETTLEMENT,    # canonical AR settlement
                    ]),
                    JurnalUmum.status == "POSTED",
                )
                .scalar()
            )
            # NOTE: Untuk tracking per-invoice yang lebih akurat, perlu
            # relasi langsung antara pembayaran dan invoice (Phase 4).
            # Untuk sekarang, sisaTagihan = grand_total.

        sisa = inv.grand_total - total_bayar
        result.append({
            "id": inv.id,
            "no_invoice": inv.no_invoice,
            "tanggal": inv.tanggal,
            "grand_total": inv.grand_total,
            "total_ppn": inv.total_ppn,
            "sisa_tagihan": inv.grand_total,  # grand_total karena tracking pembayaran per-invoice belum ada
            "status": inv.status.value if inv.status else "DRAFT",
        })

    return result


# ==========================================
# PENAWARAN (quotation — Update #3, revisi No Quotation #031)
# ==========================================

# === Update #031 — endpoint literal dideklarasikan SEBELUM /penawaran/{id}
# supaya path tidak tertelan param UUID. ===


@router.get("/penawaran/next-nomor", response_model=NomorQuotationPreviewResponse)
def get_penawaran_next_nomor(
    pelanggan_id: UUID = Query(..., description="Pelanggan — penentu singkatan & sequence"),
    bulan: int = Query(..., ge=1, le=12, description="Bulan nomor Quotation (1-12)"),
    tahun: int = Query(..., ge=2000, le=2100, description="Tahun nomor Quotation"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Preview No Quotation BERIKUTNYA (indikatif, tanpa create) — Update #031.

    Format: QUOT ASI/001/MJE/X/2026 — urut per pelanggan reset tahunan,
    singkatan dari master pelanggan, bulan romawi + tahun dari input user.
    Dipakai form Penawaran (pola "No. Dokumen" form Penerimaan #16); nomor
    final di-generate ulang saat create.
    """
    try:
        return {"nomor": penawaran_service.peek_nomor_penawaran(db, pelanggan_id, bulan, tahun)}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/penawaran/katalog", response_model=list[PenawaranKatalogItemResponse])
def get_penawaran_katalog(
    search: str | None = Query(None, description="Cari nama item bebas"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Katalog item BEBAS penawaran (Update #031) — item yang pernah diketik
    manual di penawaran (tidak masuk master data), urut paling sering dipakai.
    Dipakai form sebagai saran cepat untuk item berulang.
    """
    return penawaran_service.get_katalog_items(db, search=search, limit=limit)


# === Update #032 — dropdown penawaran utk form SO (dideklarasikan SEBELUM
# /penawaran/{penawaran_id} supaya path literal tidak tertelan param UUID). ===
@router.get("/penawaran/untuk-so", response_model=list[PenawaranUntukSoItem])
def get_penawaran_untuk_so(
    search: str | None = Query(None, description="Cari no penawaran / nama pelanggan"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar penawaran yang bisa dipilih saat membuat Sales Order — #032.

    Hanya DRAFT / SELESAI (SELESAI tetap bisa — REPEAT ORDER: satu penawaran
    boleh dipakai beberapa SO). DIREVISI & DIBATALKAN tidak ditawarkan.
    Form SO memakai ini untuk auto-isi pelanggan + item (barang master &
    item bebas) + harga saat "Pilih Penawaran".
    """
    return [
        {
            "id": p.id,
            "no_penawaran": p.no_penawaran,
            "revisi_ke": p.revisi_ke or 1,
            "status": p.status,
            "tanggal": p.tanggal,
            "pelanggan_id": p.pelanggan_id,
            "pelanggan_nama": p.pelanggan.nama if p.pelanggan else None,
            "grand_total": p.grand_total or 0,
            "digunakan_count": p.digunakan_count,
            # Update #039 — sisa penawaran utk subtitle dropdown (patokan
            # nilai SO berikutnya: DP bertahap dari penawaran yang sama).
            "sisa_penawaran": p.sisa_penawaran,
            "jumlah_item": len(p.details or []),
        }
        for p in penawaran_service.get_penawaran_untuk_so(db, search=search, limit=limit)
    ]


@router.get("/penawaran", response_model=PaginatedResponse[PenawaranResponse])
def get_penawaran_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: str | None = Query(None, description="Cari berdasarkan no penawaran, nama pelanggan"),
    status_filter: str | None = Query(None, alias="status", description="Filter status"),
    pelanggan_id: UUID | None = Query(None, description="Filter pelanggan"),
    tanggal_from: date | None = Query(None, description="Filter tanggal mulai"),
    tanggal_to: date | None = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Penawaran dengan filter dan pagination."""
    data, total = penawaran_service.get_penawaran_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, pelanggan_id=pelanggan_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/penawaran", response_model=PenawaranResponse, status_code=status.HTTP_201_CREATED)
def create_penawaran(
    data_in: PenawaranCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Penawaran baru (tanpa jurnal, tanpa workflow — status DRAFT)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        biaya_data = [b.model_dump() for b in data_in.biaya_tambahan]
        return penawaran_service.create_penawaran(
            db=db,
            tanggal=data_in.tanggal,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            biaya_data=biaya_data,
            syarat_bayar_id=data_in.syarat_bayar_id,
            alamat_pengiriman=data_in.alamat_pengiriman,
            keterangan=data_in.keterangan,
            # Update ASAHI #003 — dibawa ke SO saat konversi
            customer_po_number=data_in.customer_po_number,
            customer_code=data_in.customer_code,
            mata_uang=data_in.mata_uang,
            diskon_global=data_in.diskon_global,
            # === Update #042 — PPN dihapus dari Penawaran (resolve/pass ppn
            # dihapus; pajak hanya dihitung di INVOICE). ===
            berlaku_hingga=data_in.berlaku_hingga,
            created_by=current_user.id,
            # === Update #031 — No Quotation: user hanya input bulan & tahun ===
            bulan=data_in.bulan,
            tahun=data_in.tahun,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/penawaran/{penawaran_id}", response_model=PenawaranResponse)
def get_penawaran_detail(
    penawaran_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Penawaran."""
    item = penawaran_service.get_penawaran_by_id(db, penawaran_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan")
    return item


@router.put("/penawaran/{penawaran_id}", response_model=PenawaranResponse)
def update_penawaran(
    penawaran_id: UUID,
    data_in: PenawaranUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update Penawaran (hanya status DRAFT; header + replace details + biaya)."""
    item = penawaran_service.get_penawaran_by_id(db, penawaran_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan")

    update_data = _service_kwargs(penawaran_service.update_penawaran, data_in.model_dump(exclude_unset=True))
    try:
        return penawaran_service.update_penawaran(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/penawaran/{penawaran_id}/cancel")
def cancel_penawaran(
    penawaran_id: UUID,
    payload: HardDeleteRequest | None = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Penawaran (hard delete).

    Dokumen + rincian + biaya tambahan dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'penawaran', penawaran_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# === Update #032 — REVISI PENAWARAN: dokumen baru nomor sama (Rev. N). ===
@router.post("/penawaran/{penawaran_id}/revisi", response_model=PenawaranResponse, status_code=status.HTTP_201_CREATED)
def revisi_penawaran(
    penawaran_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat dokumen REVISI penawaran — Update #032.

    - Guard: hanya penawaran berstatus SELESAI (sudah dipakai/final).
      DRAFT cukup diedit langsung; DIREVISI/DIBATALKAN ditolak.
    - Dokumen BARU dibuat: No Quotation SAMA + "Rev. N" (revisi_ke+1),
      status DRAFT (bisa diedit), isi disalin utuh dari dokumen asal.
    - Dokumen asal berubah jadi DIREVISI (terkunci — tidak bisa dipakai
      buat SO / diedit; jejak harga lama utuh untuk audit).
    """
    try:
        return penawaran_service.create_revisi(db, penawaran_id, current_user)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/penawaran/{penawaran_id}/to-sales-order")
def convert_penawaran_to_sales_order(
    penawaran_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Konversi Penawaran menjadi Sales Order (Update #3, revisi #032).

    - Guard: penawaran berstatus DRAFT/DIPROSES/**SELESAI** — Update #032
      REPEAT ORDER: SELESAI tetap bisa dikonversi LAGI (SO baru tiap kali;
      penawaran tidak "hangus"). DIREVISI/DIBATALKAN → 400.
    - Item BEBAS diteruskan ke SO sebagai item bebas (tanpa membuat master
      barang — Update #032; perilaku #031 yang auto-create DIHAPUS).
    - SO dibuat + difinalisasi (langsung APPROVED tanpa approval), dan
      SO di-link ke penawaran (penawaran_id).
    - Return: {"salesOrderId": str, "noPesanan": str}.
    """
    try:
        so = penawaran_service.convert_to_so(db, penawaran_id, current_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"salesOrderId": str(so.id), "noPesanan": so.no_pesanan}


# ==========================================
# TUKAR FAKTUR (proof of receipt — Update #4)
# ==========================================

@router.get("/tukar-faktur", response_model=PaginatedResponse[TukarFakturResponse])
def get_tukar_faktur_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: str | None = Query(None, description="Cari berdasarkan no tukar faktur, nama pelanggan"),
    status_filter: str | None = Query(None, alias="status", description="Filter status"),
    pelanggan_id: UUID | None = Query(None, description="Filter pelanggan"),
    tanggal_from: date | None = Query(None, description="Filter tanggal mulai"),
    tanggal_to: date | None = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Tukar Faktur dengan filter dan pagination."""
    data, total = tukar_faktur_service.get_tukar_faktur_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, pelanggan_id=pelanggan_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/tukar-faktur", response_model=TukarFakturResponse, status_code=status.HTTP_201_CREATED)
def create_tukar_faktur(
    data_in: TukarFakturCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Tukar Faktur baru dari Sales Invoice.

    Tanpa jurnal, tanpa stok, tanpa workflow (tidak ada direct_complete) —
    status DRAFT; snapshot header + copy detail diambil dari invoice.
    """
    try:
        return tukar_faktur_service.create_tukar_faktur(
            db=db,
            tanggal=data_in.tanggal,
            sales_invoice_id=data_in.sales_invoice_id,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/tukar-faktur/{tf_id}", response_model=TukarFakturResponse)
def get_tukar_faktur_detail(
    tf_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Tukar Faktur."""
    item = tukar_faktur_service.get_tukar_faktur_by_id(db, tf_id)
    if not item:
        raise HTTPException(status_code=404, detail="Tukar Faktur tidak ditemukan")
    return item


@router.put("/tukar-faktur/{tf_id}", response_model=TukarFakturResponse)
def update_tukar_faktur(
    tf_id: UUID,
    data_in: TukarFakturUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update Tukar Faktur (hanya status DRAFT; header-only tanggal/keterangan)."""
    item = tukar_faktur_service.get_tukar_faktur_by_id(db, tf_id)
    if not item:
        raise HTTPException(status_code=404, detail="Tukar Faktur tidak ditemukan")

    update_data = _service_kwargs(
        tukar_faktur_service.update_tukar_faktur, data_in.model_dump(exclude_unset=True)
    )
    try:
        return tukar_faktur_service.update_tukar_faktur(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/tukar-faktur/{tf_id}/selesaikan")
def selesaikan_tukar_faktur(
    tf_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Tandai Tukar Faktur SELESAI (manual, tanpa workflow).

    Guard: hanya DRAFT (panggil kedua kali → 400).
    Return: {"id": str, "noTukarFaktur": str}.
    """
    item = tukar_faktur_service.get_tukar_faktur_by_id(db, tf_id)
    if not item:
        raise HTTPException(status_code=404, detail="Tukar Faktur tidak ditemukan")
    try:
        tf = tukar_faktur_service.selesaikan_tukar_faktur(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": str(tf.id), "noTukarFaktur": tf.no_tukar_faktur}


@router.post("/tukar-faktur/{tf_id}/cancel")
def cancel_tukar_faktur(
    tf_id: UUID,
    payload: HardDeleteRequest | None = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Tukar Faktur (hard delete).

    Dokumen + rincian dihapus dari database (tanpa jurnal/stok — tidak ada
    efek samping); jejak lengkap tersimpan di log dokumen terhapus (Histori).
    """
    try:
        return cancel_document(
            db, 'tukar_faktur', tf_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
