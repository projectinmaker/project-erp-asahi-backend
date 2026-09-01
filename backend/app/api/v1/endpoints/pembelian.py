 """
Pembelian Endpoints.
PurchaseOrder, PurchaseInvoice, PurchaseRetur, PenerimaanBarang.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.transaksi.penjualan.sales_order import StatusPenjualan
from app.schemas.base import PaginatedResponse
from app.schemas.pembelian import (
    PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrderResponse,
    PurchaseInvoiceCreate, PurchaseInvoiceUpdate, PurchaseInvoiceResponse,
    PurchaseReturCreate, PurchaseReturUpdate, PurchaseReturResponse,
    PenerimaanBarangCreate, PenerimaanBarangUpdate, PenerimaanBarangResponse,
)
from app.services import pembelian_service as svc

router = APIRouter()


# ==========================================
# PURCHASE ORDER
# ==========================================

@router.get("/purchase-order", response_model=PaginatedResponse[PurchaseOrderResponse])
def get_purchase_order_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no pesanan, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Purchase Order dengan filter dan pagination."""
    data, total = svc.get_purchase_order_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, supplier_id=supplier_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/purchase-order", response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED)
def create_purchase_order(
    data_in: PurchaseOrderCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Purchase Order baru (auto-generate no pesanan + auto-post jurnal)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        biaya_data = [b.model_dump() for b in data_in.biaya_tambahan]
        return svc.create_purchase_order(
            db=db,
            tanggal=data_in.tanggal,
            supplier_id=data_in.supplier_id,
            details_data=details_data,
            biaya_data=biaya_data,
            tanggal_kirim=data_in.tanggal_kirim,
            alamat=data_in.alamat,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/purchase-order/{po_id}", response_model=PurchaseOrderResponse)
def get_purchase_order_detail(
    po_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Purchase Order."""
    item = svc.get_purchase_order_by_id(db, po_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Order tidak ditemukan")
    return item


@router.put("/purchase-order/{po_id}", response_model=PurchaseOrderResponse)
def update_purchase_order(
    po_id: UUID,
    data_in: PurchaseOrderUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Purchase Order (header only)."""
    item = svc.get_purchase_order_by_id(db, po_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Order tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_purchase_order(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/purchase-order/{po_id}/cancel", response_model=PurchaseOrderResponse)
def cancel_purchase_order(
    po_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Purchase Order."""
    item = svc.get_purchase_order_by_id(db, po_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Order tidak ditemukan")
    try:
        return svc.cancel_purchase_order(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PURCHASE INVOICE
# ==========================================

@router.get("/purchase-invoice", response_model=PaginatedResponse[PurchaseInvoiceResponse])
def get_purchase_invoice_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no form, no faktur, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Purchase Invoice dengan filter dan pagination."""
    data, total = svc.get_purchase_invoice_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, supplier_id=supplier_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/purchase-invoice", response_model=PurchaseInvoiceResponse, status_code=status.HTTP_201_CREATED)
def create_purchase_invoice(
    data_in: PurchaseInvoiceCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Purchase Invoice baru (auto-generate no form + auto-post jurnal)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        biaya_data = [b.model_dump() for b in data_in.biaya_tambahan]
        return svc.create_purchase_invoice(
            db=db,
            tanggal=data_in.tanggal,
            supplier_id=data_in.supplier_id,
            no_faktur=data_in.no_faktur,
            details_data=details_data,
            biaya_data=biaya_data,
            alamat=data_in.alamat,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/purchase-invoice/{inv_id}", response_model=PurchaseInvoiceResponse)
def get_purchase_invoice_detail(
    inv_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Purchase Invoice."""
    item = svc.get_purchase_invoice_by_id(db, inv_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Invoice tidak ditemukan")
    return item


@router.put("/purchase-invoice/{inv_id}", response_model=PurchaseInvoiceResponse)
def update_purchase_invoice(
    inv_id: UUID,
    data_in: PurchaseInvoiceUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Purchase Invoice (header only)."""
    item = svc.get_purchase_invoice_by_id(db, inv_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Invoice tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_purchase_invoice(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/purchase-invoice/{inv_id}/cancel", response_model=PurchaseInvoiceResponse)
def cancel_purchase_invoice(
    inv_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Purchase Invoice."""
    item = svc.get_purchase_invoice_by_id(db, inv_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Invoice tidak ditemukan")
    try:
        return svc.cancel_purchase_invoice(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PURCHASE RETUR
# ==========================================

@router.get("/purchase-retur", response_model=PaginatedResponse[PurchaseReturResponse])
def get_purchase_retur_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no retur, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Purchase Retur dengan filter dan pagination."""
    data, total = svc.get_purchase_retur_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, supplier_id=supplier_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/purchase-retur", response_model=PurchaseReturResponse, status_code=status.HTTP_201_CREATED)
def create_purchase_retur(
    data_in: PurchaseReturCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Purchase Retur baru (auto-generate no retur + auto-post jurnal)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        return svc.create_purchase_retur(
            db=db,
            tanggal=data_in.tanggal,
            purchase_order_id=data_in.purchase_order_id,
            supplier_id=data_in.supplier_id,
            details_data=details_data,
            alamat=data_in.alamat,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/purchase-retur/{retur_id}", response_model=PurchaseReturResponse)
def get_purchase_retur_detail(
    retur_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Purchase Retur."""
    item = svc.get_purchase_retur_by_id(db, retur_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Retur tidak ditemukan")
    return item


@router.put("/purchase-retur/{retur_id}", response_model=PurchaseReturResponse)
def update_purchase_retur(
    retur_id: UUID,
    data_in: PurchaseReturUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Purchase Retur (header only)."""
    item = svc.get_purchase_retur_by_id(db, retur_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Retur tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_purchase_retur(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/purchase-retur/{retur_id}/cancel", response_model=PurchaseReturResponse)
def cancel_purchase_retur(
    retur_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Purchase Retur."""
    item = svc.get_purchase_retur_by_id(db, retur_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Retur tidak ditemukan")
    try:
        return svc.cancel_purchase_retur(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PENERIMAAN BARANG
# ==========================================

@router.get("/penerimaan", response_model=PaginatedResponse[PenerimaanBarangResponse])
def get_penerimaan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no form, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Penerimaan Barang dengan filter dan pagination."""
    data, total = svc.get_penerimaan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, supplier_id=supplier_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/penerimaan", response_model=PenerimaanBarangResponse, status_code=status.HTTP_201_CREATED)
def create_penerimaan(
    data_in: PenerimaanBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Penerimaan Barang baru (auto-generate no form)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        return svc.create_penerimaan(
            db=db,
            tanggal=data_in.tanggal,
            purchase_order_id=data_in.purchase_order_id,
            supplier_id=data_in.supplier_id,
            details_data=details_data,
            alamat=data_in.alamat,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/penerimaan/{pb_id}", response_model=PenerimaanBarangResponse)
def get_penerimaan_detail(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Penerimaan Barang."""
    item = svc.get_penerimaan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penerimaan Barang tidak ditemukan")
    return item


@router.put("/penerimaan/{pb_id}", response_model=PenerimaanBarangResponse)
def update_penerimaan(
    pb_id: UUID,
    data_in: PenerimaanBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Penerimaan Barang (header only)."""
    item = svc.get_penerimaan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penerimaan Barang tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_penerimaan(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/penerimaan/{pb_id}/cancel", response_model=PenerimaanBarangResponse)
def cancel_penerimaan(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Penerimaan Barang."""
    item = svc.get_penerimaan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penerimaan Barang tidak ditemukan")
    try:
        return svc.cancel_penerimaan(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# INVOICE BELUM BAYAR (untuk Pembayaran Kas)
# ==========================================

@router.get("/invoice-belum-bayar/{supplier_id}")
def get_invoice_belum_bayar(
    supplier_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """List invoice pembelian yang belum dibayar penuh untuk suatu supplier.

    Digunakan di form Pembayaran Kas untuk cascade dropdown:
    Pilih Hutang -> Pilih Supplier -> muncul list invoice.

    Return list sederhana dengan field tambahan `sisaTagihan`.
    """
    from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
    from app.models.master.supplier import Supplier
    from decimal import Decimal

    # Validasi supplier
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")

    # Ambil semua invoice aktif (bukan BATAL) untuk supplier ini
    invoices = (
        db.query(PurchaseInvoice)
        .filter(
            PurchaseInvoice.supplier_id == supplier_id,
            PurchaseInvoice.status != StatusPenjualan.DIBATALKAN,
        )
        .order_by(PurchaseInvoice.tanggal.desc())
        .all()
    )

    result = []
    for inv in invoices:
        # NOTE: Untuk tracking per-invoice yang lebih akurat, perlu
        # relasi langsung antara pembayaran dan invoice (Phase 4).
        # Untuk sekarang, sisaTagihan = grand_total.
        result.append({
            "id": inv.id,
            "no_form": inv.no_form,
            "no_faktur": inv.no_faktur,
            "tanggal": inv.tanggal,
            "grand_total": inv.grand_total,
            "total_ppn": inv.total_ppn,
            "sisa_tagihan": inv.grand_total,  # grand_total karena tracking pembayaran per-invoice belum ada
            "status": inv.status.value if inv.status else "DRAFT",
        })

    return result
