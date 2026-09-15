"""sales_validation.py

Helper untuk enforce Master Roadmap §11-§16 (Sales Chain):
- validate_active_customer: cek Pelanggan AKTIF
- validate_over_delivery: cek qty delivered <= qty ordered per SO detail
- validate_over_invoice: cek qty invoiced <= qty delivered per delivery detail
- validate_returnable_qty: cek return qty <= returnable qty (qty invoiced - already returned)
- get_qty_delivered_so_far: helper query sum delivered for SO detail
- get_qty_invoiced_so_far: helper query sum invoiced for delivery detail
- get_qty_returned_so_far: helper query sum returned for invoice detail

Dipakai oleh:
- app/services/penjualan_service.py (create/update pengiriman, invoice, retur)
"""
from uuid import UUID
from typing import Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.master.pelanggan import Pelanggan
from app.models.master.barang import Barang, ItemTypeBarang
from app.models.detail.sales_order_detail import SalesOrderDetail
from app.models.detail.pengiriman_barang_detail import PengirimanBarangDetail
from app.models.detail.sales_invoice_detail import SalesInvoiceDetail
from app.models.detail.sales_retur_detail import SalesReturDetail
from app.models.transaksi.penjualan.pengiriman_barang import PengirimanBarang
from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
from app.models.transaksi.penjualan.sales_retur import SalesRetur
from app.models.transaksi.penjualan.sales_order import StatusPenjualan


# ==========================================
# Customer validation
# ==========================================
def validate_active_customer(
    db: Session,
    pelanggan_id: UUID,
    context: str = "Transaksi",
) -> Pelanggan:
    """Cek apakah Pelanggan masih AKTIF sebelum dipakai transaksi.

    Sesuai Master Roadmap §9 Rules:
        "Master inactive tidak boleh dipakai transaksi baru"

    Dipanggil di awal create Sales Order / Delivery / Invoice / Retur.
    """
    pelanggan = db.get(Pelanggan, pelanggan_id)
    if pelanggan is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan. {context} dibatalkan.",
        )
    if pelanggan.status != "AKTIF":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Pelanggan '{pelanggan.nama}' (kode: {pelanggan.kode}) "
                f"sudah {pelanggan.status} — tidak boleh dipakai transaksi baru. "
                f"{context} dibatalkan."
            ),
        )
    return pelanggan


# ==========================================
# Over-delivery / Over-invoice helpers
# ==========================================
def get_qty_delivered_so_far(
    db: Session,
    sales_order_detail_id: UUID,
    exclude_pengiriman_detail_id: Optional[UUID] = None,
) -> int:
    """Hitung total qty yang sudah dideliver untuk SalesOrderDetail tertentu.

    Filter: hanya hitung PengirimanBarangDetail yang status pengirimannya
    DIPROSES / SELESAI (sudah bergerak / akan bergerak). DRAFT & DIBATALKAN tidak dihitung.

    Parameter:
        db: SQLAlchemy Session
        sales_order_detail_id: UUID SalesOrderDetail
        exclude_pengiriman_detail_id: skip PengirimanBarangDetail tertentu
            (mis. kalau sedang update, exclude dirinya sendiri)

    Return:
        int total qty already delivered
    """
    query = (
        db.query(func.coalesce(func.sum(PengirimanBarangDetail.qty), 0))
        .join(PengirimanBarang, PengirimanBarang.id == PengirimanBarangDetail.pengiriman_id)
        .filter(
            PengirimanBarangDetail.sales_order_detail_id == sales_order_detail_id,
            PengirimanBarang.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]),
        )
    )
    if exclude_pengiriman_detail_id is not None:
        query = query.filter(PengirimanBarangDetail.id != exclude_pengiriman_detail_id)
    return int(query.scalar() or 0)


def get_qty_invoiced_so_far(
    db: Session,
    delivery_detail_id: UUID,
    exclude_invoice_detail_id: Optional[UUID] = None,
) -> int:
    """Hitung total qty yang sudah di-invoice untuk PengirimanBarangDetail tertentu.

    Filter: hanya hitung SalesInvoiceDetail yang status invoicenya
    DIPROSES / SELESAI (POSTED). DRAFT & DIBATALKAN tidak dihitung.

    Parameter:
        db: SQLAlchemy Session
        delivery_detail_id: UUID PengirimanBarangDetail
        exclude_invoice_detail_id: skip SalesInvoiceDetail tertentu
            (mis. kalau sedang update, exclude dirinya sendiri)

    Return:
        int total qty already invoiced
    """
    query = (
        db.query(func.coalesce(func.sum(SalesInvoiceDetail.qty), 0))
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceDetail.sales_invoice_id)
        .filter(
            SalesInvoiceDetail.delivery_detail_id == delivery_detail_id,
            SalesInvoice.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]),
        )
    )
    if exclude_invoice_detail_id is not None:
        query = query.filter(SalesInvoiceDetail.id != exclude_invoice_detail_id)
    return int(query.scalar() or 0)


def get_qty_returned_so_far(
    db: Session,
    invoice_detail_id: UUID,
    exclude_retur_detail_id: Optional[UUID] = None,
) -> int:
    """Hitung total qty yang sudah di-retur untuk SalesInvoiceDetail tertentu.

    Filter: hanya hitung SalesReturDetail yang status returnya
    DIPROSES / SELESAI (POSTED). DRAFT & DIBATALKAN tidak dihitung.
    """
    query = (
        db.query(func.coalesce(func.sum(SalesReturDetail.qty), 0))
        .join(SalesRetur, SalesRetur.id == SalesReturDetail.sales_retur_id)
        .filter(
            SalesReturDetail.sales_invoice_detail_id == invoice_detail_id,
            SalesRetur.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]),
        )
    )
    if exclude_retur_detail_id is not None:
        query = query.filter(SalesReturDetail.id != exclude_retur_detail_id)
    return int(query.scalar() or 0)


# ==========================================
# Validators (raise HTTPException on violation)
# ==========================================
def validate_over_delivery(
    db: Session,
    sales_order_detail_id: UUID,
    new_qty: int,
    exclude_pengiriman_detail_id: Optional[UUID] = None,
    context: str = "Delivery",
) -> None:
    """Cek apakah qty delivery baru tidak melebihi qty ordered di SO.

    Sesuai Master Roadmap §13:
        "Reject over-delivery"

    Logic:
        qty_already_delivered + new_qty <= qty_ordered

    Raises:
        HTTPException 400 kalau over-delivery
    """
    so_detail = db.get(SalesOrderDetail, sales_order_detail_id)
    if so_detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"SalesOrderDetail dengan ID {sales_order_detail_id} tidak ditemukan. {context} dibatalkan.",
        )

    qty_ordered = so_detail.qty
    qty_already_delivered = get_qty_delivered_so_far(
        db, sales_order_detail_id, exclude_pengiriman_detail_id
    )
    qty_after = qty_already_delivered + new_qty

    if qty_after > qty_ordered:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Over-delivery: qty yang akan dideliver ({new_qty}) + "
                f"qty sudah dideliver ({qty_already_delivered}) = {qty_after} "
                f"melebihi qty ordered SO ({qty_ordered}) untuk barang "
                f"'{so_detail.barang.kode if so_detail.barang else 'unknown'}'. "
                f"Maximum qty yang bisa dideliver: {qty_ordered - qty_already_delivered}. "
                f"{context} dibatalkan."
            ),
        )


def validate_over_invoice(
    db: Session,
    delivery_detail_id: UUID,
    new_qty: int,
    exclude_invoice_detail_id: Optional[UUID] = None,
    context: str = "Invoice",
) -> None:
    """Cek apakah qty invoice baru tidak melebihi qty delivered.

    Sesuai Master Roadmap §14:
        "Reject over-invoice"

    Logic:
        qty_already_invoiced + new_qty <= qty_delivered

    Raises:
        HTTPException 400 kalau over-invoice
    """
    delivery_detail = db.get(PengirimanBarangDetail, delivery_detail_id)
    if delivery_detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"PengirimanBarangDetail dengan ID {delivery_detail_id} tidak ditemukan. {context} dibatalkan.",
        )

    qty_delivered = delivery_detail.qty
    qty_already_invoiced = get_qty_invoiced_so_far(
        db, delivery_detail_id, exclude_invoice_detail_id
    )
    qty_after = qty_already_invoiced + new_qty

    if qty_after > qty_delivered:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Over-invoice: qty yang akan di-invoice ({new_qty}) + "
                f"qty sudah di-invoice ({qty_already_invoiced}) = {qty_after} "
                f"melebihi qty delivered ({qty_delivered}) untuk barang "
                f"'{delivery_detail.barang.kode if delivery_detail.barang else 'unknown'}'. "
                f"Maximum qty yang bisa di-invoice: {qty_delivered - qty_already_invoiced}. "
                f"{context} dibatalkan."
            ),
        )


def validate_returnable_qty(
    db: Session,
    invoice_detail_id: UUID,
    new_qty: int,
    exclude_retur_detail_id: Optional[UUID] = None,
    context: str = "Sales Return",
) -> None:
    """Cek apakah qty retur tidak melebihi returnable qty.

    Sesuai Master Roadmap §16:
        "Return qty <= returnable qty"

    Logic:
        qty_already_returned + new_qty <= qty_invoiced

    Raises:
        HTTPException 400 kalau over-return
    """
    invoice_detail = db.get(SalesInvoiceDetail, invoice_detail_id)
    if invoice_detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"SalesInvoiceDetail dengan ID {invoice_detail_id} tidak ditemukan. {context} dibatalkan.",
        )

    qty_invoiced = invoice_detail.qty
    qty_already_returned = get_qty_returned_so_far(
        db, invoice_detail_id, exclude_retur_detail_id
    )
    qty_after = qty_already_returned + new_qty

    if qty_after > qty_invoiced:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Over-return: qty yang akan di-retur ({new_qty}) + "
                f"qty sudah di-retur ({qty_already_returned}) = {qty_after} "
                f"melebihi qty invoiced ({qty_invoiced}) untuk barang "
                f"'{invoice_detail.barang.kode if invoice_detail.barang else 'unknown'}'. "
                f"Maximum qty yang bisa di-retur: {qty_invoiced - qty_already_returned}. "
                f"{context} dibatalkan."
            ),
        )
