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
    PenawaranCreate, PenawaranUpdate, PenawaranResponse,
)
from app.schemas.tukar_faktur import (
    TukarFakturCreate, TukarFakturUpdate, TukarFakturResponse,
)
from app.services import penjualan_service as svc
from app.services import penawaran_service
from app.services import tukar_faktur_service
from app.services import workflow_service
from app.services.hard_delete_service import hard_delete_document
from app.services.sales_validation import (
    get_qty_delivered_so_far,
    get_qty_invoiced_for_so_detail,
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
    """Buat Sales Order baru."""
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
            ekspedisi=data_in.ekspedisi,
            tanggal_pengiriman=data_in.tanggal_pengiriman,
            penjual=data_in.penjual,
            alamat_pengiriman=data_in.alamat_pengiriman,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=False,
            created_by=current_user.id,
            customer_po_number=data_in.customer_po_number,
            customer_po_date=data_in.customer_po_date,
            currency=data_in.currency,
        )
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'sales_order', obj.id)
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
    - qtyTerfaktur = SUM qty invoice berstatus DIPROSES/SELESAI lewat dua
      jalur link: sales_order_detail_id langsung ATAU delivery_detail_id dari
      pengiriman baris SO tersebut.
    - sisaKirim = qtyPesanan - qtyTerkirim (min 0);
      sisaFaktur = qtyPesanan - qtyTerfaktur (min 0).
    """
    so = svc.get_sales_order_by_id(db, so_id)
    if not so:
        raise HTTPException(status_code=404, detail="Sales Order tidak ditemukan")

    details = []
    for d in so.details:
        qty_terkirim = get_qty_delivered_so_far(db, d.id)
        qty_terfaktur = get_qty_invoiced_for_so_detail(db, d.id)
        details.append({
            "sales_order_detail_id": d.id,
            "barang_id": d.barang_id,
            "kode_barang": d.barang.kode if d.barang else None,
            "nama_barang": d.barang.nama if d.barang else None,
            "satuan_id": d.satuan_id,
            "satuan_nama": d.satuan.nama if d.satuan else None,
            "qty_pesanan": d.qty,
            "qty_terkirim": qty_terkirim,
            "qty_terfaktur": qty_terfaktur,
            "sisa_kirim": max(d.qty - qty_terkirim, 0),
            "sisa_faktur": max(d.qty - qty_terfaktur, 0),
            "harga": float(d.harga or 0),
            "diskon": float(d.diskon or 0),
        })

    return {
        "sales_order_id": so.id,
        "pelanggan_id": so.pelanggan_id,
        "syarat_bayar_id": so.syarat_bayar_id,
        "no_pesanan": so.no_pesanan,
        "alamat_pengiriman": so.alamat_pengiriman,
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
        return hard_delete_document(
            db, 'sales_order', so_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
        )
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'sales_invoice', obj.id)
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
        return hard_delete_document(
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
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'sales_retur', obj.id)
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
        return hard_delete_document(
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
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        workflow_service.direct_complete(db, current_user, 'pengiriman_barang', obj.id)
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
        return hard_delete_document(
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
# PENAWARAN (quotation — Update #3)
# ==========================================

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
            mata_uang=data_in.mata_uang,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            berlaku_hingga=data_in.berlaku_hingga,
            created_by=current_user.id,
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
        return hard_delete_document(
            db, 'penawaran', penawaran_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/penawaran/{penawaran_id}/to-sales-order")
def convert_penawaran_to_sales_order(
    penawaran_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Konversi Penawaran menjadi Sales Order (Update #3).

    - Guard: hanya penawaran berstatus DRAFT/DIPROSES (panggil kedua kali → 400).
    - SO dibuat + difinalisasi (admin: langsung APPROVED), penawaran jadi SELESAI.
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
        return hard_delete_document(
            db, 'tukar_faktur', tf_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
