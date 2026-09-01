"""
Penjualan Endpoints.
SalesOrder, SalesInvoice, SalesRetur, PengirimanBarang.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
)
from app.services import penjualan_service as svc

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
    """Buat Sales Order baru (auto-generate no pesanan + auto-post jurnal)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        biaya_data = [b.model_dump() for b in data_in.biaya_tambahan]
        return svc.create_sales_order(
            db=db,
            tanggal=data_in.tanggal,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            biaya_data=biaya_data,
            syarat_bayar_id=data_in.syarat_bayar_id,
            fob=data_in.fob,
            ekspedisi=data_in.ekspedisi,
            tanggal_pengiriman=data_in.tanggal_pengiriman,
            penjual=data_in.penjual,
            alamat_pengiriman=data_in.alamat_pengiriman,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
        )
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


@router.put("/sales-order/{so_id}", response_model=SalesOrderResponse)
def update_sales_order(
    so_id: UUID,
    data_in: SalesOrderUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Sales Order (header only)."""
    item = svc.get_sales_order_by_id(db, so_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Order tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_sales_order(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sales-order/{so_id}/cancel", response_model=SalesOrderResponse)
def cancel_sales_order(
    so_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Sales Order."""
    item = svc.get_sales_order_by_id(db, so_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Order tidak ditemukan")
    try:
        return svc.cancel_sales_order(db, db_obj=item)
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
        return svc.create_sales_invoice(
            db=db,
            tanggal=data_in.tanggal,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            biaya_data=biaya_data,
            syarat_bayar_id=data_in.syarat_bayar_id,
            sales_order_id=data_in.sales_order_id,
            fob=data_in.fob,
            ekspedisi=data_in.ekspedisi,
            tanggal_pengiriman=data_in.tanggal_pengiriman,
            alamat_pengiriman=data_in.alamat_pengiriman,
            mata_uang=data_in.mata_uang,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
        )
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


@router.post("/sales-invoice/{inv_id}/cancel", response_model=SalesInvoiceResponse)
def cancel_sales_invoice(
    inv_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Sales Invoice."""
    item = svc.get_sales_invoice_by_id(db, inv_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Invoice tidak ditemukan")
    try:
        return svc.cancel_sales_invoice(db, db_obj=item)
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
        return svc.create_sales_retur(
            db=db,
            tanggal=data_in.tanggal,
            sales_invoice_id=data_in.sales_invoice_id,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            alamat_pengembalian=data_in.alamat_pengembalian,
            no_pengembalian=data_in.no_pengembalian,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
        )
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


@router.post("/sales-retur/{retur_id}/cancel", response_model=SalesReturResponse)
def cancel_sales_retur(
    retur_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Sales Retur."""
    item = svc.get_sales_retur_by_id(db, retur_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sales Retur tidak ditemukan")
    try:
        return svc.cancel_sales_retur(db, db_obj=item)
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
        return svc.create_pengiriman(
            db=db,
            tanggal=data_in.tanggal,
            sales_order_id=data_in.sales_order_id,
            pelanggan_id=data_in.pelanggan_id,
            details_data=details_data,
            ekspedisi=data_in.ekspedisi,
            alamat_pengiriman=data_in.alamat_pengiriman,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
        )
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
    """Update data Pengiriman Barang (header only)."""
    item = svc.get_pengiriman_by_id(db, pengiriman_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pengiriman Barang tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_pengiriman(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pengiriman/{pengiriman_id}/cancel", response_model=PengirimanBarangResponse)
def cancel_pengiriman(
    pengiriman_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Pengiriman Barang."""
    item = svc.get_pengiriman_by_id(db, pengiriman_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pengiriman Barang tidak ditemukan")
    try:
        return svc.cancel_pengiriman(db, db_obj=item)
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
        # Hitung total yang sudah dibayar dari jurnal penerimaan kas
        total_bayar = Decimal("0")
        if inv.jurnal_umum_id:
            # Cari jurnal penerimaan yang merujuk invoice ini
            bayar_rows = (
                db.query(sa_func.coalesce(sa_func.sum(JurnalUmum.total_kredit), 0))
                .filter(
                    JurnalUmum.ref_module == RefModule.PENERIMAAN,
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
