"""purchase_validation.py

Helper untuk enforce Master Roadmap §17-§22 (Purchase Chain + GRNI):
- validate_active_supplier: cek Supplier AKTIF
- validate_duplicate_supplier_invoice: cek no_faktur tidak boleh duplikat per supplier
- validate_over_receipt: cek qty received <= qty ordered per PO detail
- validate_over_invoice: cek qty invoiced <= qty received per receipt detail
- validate_returnable_qty_purchase: cek return qty <= returnable qty (qty received - already returned)

Dipakai oleh:
- app/services/pembelian_service.py (create/update penerimaan, invoice, retur)
"""
from uuid import UUID
from typing import Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.master.supplier import Supplier
from app.models.detail.purchase_order_detail import PurchaseOrderDetail
from app.models.detail.penerimaan_barang_detail import PenerimaanBarangDetail
from app.models.detail.purchase_invoice_detail import PurchaseInvoiceDetail
from app.models.detail.purchase_retur_detail import PurchaseReturDetail
from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.models.transaksi.pembelian.purchase_retur import PurchaseRetur
from app.models.transaksi.pembelian.purchase_order import StatusPenjualan


# ==========================================
# Supplier validation
# ==========================================
def validate_active_supplier(
    db: Session,
    supplier_id: UUID,
    context: str = "Transaksi",
) -> Supplier:
    """Cek apakah Supplier masih AKTIF sebelum dipakai transaksi.

    Sesuai Master Roadmap §9 Rules:
        "Master inactive tidak boleh dipakai transaksi baru"
    """
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(
            status_code=404,
            detail=f"Supplier dengan ID {supplier_id} tidak ditemukan. {context} dibatalkan.",
        )
    if supplier.status != "AKTIF":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Supplier '{supplier.nama}' (kode: {supplier.kode}) "
                f"sudah {supplier.status} — tidak boleh dipakai transaksi baru. "
                f"{context} dibatalkan."
            ),
        )
    return supplier


def validate_duplicate_supplier_invoice(
    db: Session,
    supplier_id: UUID,
    no_faktur: str,
    exclude_invoice_id: Optional[UUID] = None,
    context: str = "Purchase Invoice",
) -> None:
    """Cek apakah no_faktur supplier tidak duplikat.

    Sesuai Master Roadmap §20:
        "Duplicate supplier invoice validation"

    Logic:
    - Cari PurchaseInvoice dengan same supplier_id + same no_faktur
    - Exclude status BATAL/DIBATALKAN (sudah dibatalkan tidak dihitung)
    - Exclude diri sendiri (kalau update)

    Raises:
        HTTPException 400 kalau duplikat ditemukan
    """
    query = (
        db.query(PurchaseInvoice)
        .filter(
            PurchaseInvoice.supplier_id == supplier_id,
            PurchaseInvoice.no_faktur == no_faktur,
            PurchaseInvoice.status != StatusPenjualan.DIBATALKAN,
        )
    )
    if exclude_invoice_id is not None:
        query = query.filter(PurchaseInvoice.id != exclude_invoice_id)

    existing = query.first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Duplicate supplier invoice: Supplier '{existing.supplier.nama if existing.supplier else 'unknown'}' "
                f"sudah punya invoice dengan no_faktur '{no_faktur}' "
                f"(id: {existing.id}, no_form: {existing.no_form}, status: {existing.status.value}). "
                f"{context} dibatalkan. "
                f"Gunakan no_faktur yang berbeda, atau batalkan invoice existing terlebih dahulu."
            ),
        )


# ==========================================
# Over-receipt / Over-invoice helpers
# ==========================================
def get_qty_received_so_far(
    db: Session,
    purchase_order_detail_id: UUID,
    exclude_penerimaan_detail_id: Optional[UUID] = None,
) -> int:
    """Hitung total qty yang sudah di-receive untuk PurchaseOrderDetail tertentu.

    Filter: hanya hitung PenerimaanBarangDetail yang status penerimaannya
    DIPROSES / SELESAI (POSTED). DRAFT & DIBATALKAN tidak dihitung.
    """
    query = (
        db.query(func.coalesce(func.sum(PenerimaanBarangDetail.qty), 0))
        .join(PenerimaanBarang, PenerimaanBarang.id == PenerimaanBarangDetail.penerimaan_barang_id)
        .filter(
            PenerimaanBarangDetail.purchase_order_detail_id == purchase_order_detail_id,
            PenerimaanBarang.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]),
        )
    )
    if exclude_penerimaan_detail_id is not None:
        query = query.filter(PenerimaanBarangDetail.id != exclude_penerimaan_detail_id)
    return int(query.scalar() or 0)


def get_qty_invoiced_purchase_so_far(
    db: Session,
    penerimaan_barang_detail_id: UUID,
    exclude_invoice_detail_id: Optional[UUID] = None,
) -> int:
    """Hitung total qty yang sudah di-invoice untuk PenerimaanBarangDetail tertentu.

    Filter: hanya hitung PurchaseInvoiceDetail yang status invoicenya
    DIPROSES / SELESAI (POSTED). DRAFT & DIBATALKAN tidak dihitung.
    """
    query = (
        db.query(func.coalesce(func.sum(PurchaseInvoiceDetail.qty), 0))
        .join(PurchaseInvoice, PurchaseInvoice.id == PurchaseInvoiceDetail.purchase_invoice_id)
        .filter(
            PurchaseInvoiceDetail.penerimaan_barang_detail_id == penerimaan_barang_detail_id,
            PurchaseInvoice.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]),
        )
    )
    if exclude_invoice_detail_id is not None:
        query = query.filter(PurchaseInvoiceDetail.id != exclude_invoice_detail_id)
    return int(query.scalar() or 0)


def get_qty_returned_purchase_so_far(
    db: Session,
    penerimaan_barang_detail_id: UUID,
    exclude_retur_detail_id: Optional[UUID] = None,
) -> int:
    """Hitung total qty yang sudah di-retur untuk PenerimaanBarangDetail tertentu.

    Filter: hanya hitung PurchaseReturDetail yang status returnya
    DIPROSES / SELESAI (POSTED). DRAFT & DIBATALKAN tidak dihitung.

    Catatan: return qty dihitung per receipt detail (bukan per invoice detail),
    karena return fisik (stok keluar) berdasarkan qty received.
    """
    query = (
        db.query(func.coalesce(func.sum(PurchaseReturDetail.qty), 0))
        .join(PurchaseRetur, PurchaseRetur.id == PurchaseReturDetail.purchase_retur_id)
        .filter(
            PurchaseReturDetail.penerimaan_barang_detail_id == penerimaan_barang_detail_id,
            PurchaseRetur.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]),
        )
    )
    if exclude_retur_detail_id is not None:
        query = query.filter(PurchaseReturDetail.id != exclude_retur_detail_id)
    return int(query.scalar() or 0)


# ==========================================
# Validators (raise HTTPException on violation)
# ==========================================
def validate_over_receipt(
    db: Session,
    purchase_order_detail_id: UUID,
    new_qty: int,
    exclude_penerimaan_detail_id: Optional[UUID] = None,
    context: str = "Goods Receipt",
) -> None:
    """Cek apakah qty receipt baru tidak melebihi qty ordered di PO.

    Sesuai Master Roadmap §19:
        "Reject over-receipt"

    Logic:
        qty_already_received + new_qty <= qty_ordered
    """
    po_detail = db.get(PurchaseOrderDetail, purchase_order_detail_id)
    if po_detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"PurchaseOrderDetail dengan ID {purchase_order_detail_id} tidak ditemukan. {context} dibatalkan.",
        )

    qty_ordered = po_detail.qty
    qty_already_received = get_qty_received_so_far(
        db, purchase_order_detail_id, exclude_penerimaan_detail_id
    )
    qty_after = qty_already_received + new_qty

    if qty_after > qty_ordered:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Over-receipt: qty yang akan di-receive ({new_qty}) + "
                f"qty sudah di-receive ({qty_already_received}) = {qty_after} "
                f"melebihi qty ordered PO ({qty_ordered}) untuk barang "
                f"'{po_detail.barang.kode if po_detail.barang else 'unknown'}'. "
                f"Maximum qty yang bisa di-receive: {qty_ordered - qty_already_received}. "
                f"{context} dibatalkan."
            ),
        )


def validate_over_invoice_purchase(
    db: Session,
    penerimaan_barang_detail_id: UUID,
    new_qty: int,
    exclude_invoice_detail_id: Optional[UUID] = None,
    context: str = "Purchase Invoice",
) -> None:
    """Cek apakah qty invoice baru tidak melebihi qty received.

    Sesuai Master Roadmap §20:
        "Partial receipt/invoice supported" — tapi tidak boleh over-invoice

    Logic:
        qty_already_invoiced + new_qty <= qty_received

    Raises:
        HTTPException 400 kalau over-invoice
    """
    receipt_detail = db.get(PenerimaanBarangDetail, penerimaan_barang_detail_id)
    if receipt_detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"PenerimaanBarangDetail dengan ID {penerimaan_barang_detail_id} tidak ditemukan. {context} dibatalkan.",
        )

    qty_received = receipt_detail.qty
    qty_already_invoiced = get_qty_invoiced_purchase_so_far(
        db, penerimaan_barang_detail_id, exclude_invoice_detail_id
    )
    qty_after = qty_already_invoiced + new_qty

    if qty_after > qty_received:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Over-invoice: qty yang akan di-invoice ({new_qty}) + "
                f"qty sudah di-invoice ({qty_already_invoiced}) = {qty_after} "
                f"melebihi qty received ({qty_received}) untuk barang "
                f"'{receipt_detail.barang.kode if receipt_detail.barang else 'unknown'}'. "
                f"Maximum qty yang bisa di-invoice: {qty_received - qty_already_invoiced}. "
                f"{context} dibatalkan."
            ),
        )


def validate_returnable_qty_purchase(
    db: Session,
    penerimaan_barang_detail_id: UUID,
    new_qty: int,
    exclude_retur_detail_id: Optional[UUID] = None,
    context: str = "Purchase Return",
) -> None:
    """Cek apakah qty retur tidak melebihi returnable qty.

    Sesuai Master Roadmap §22:
        "Return qty based on received qty"

    Logic:
        qty_already_returned + new_qty <= qty_received

    Raises:
        HTTPException 400 kalau over-return
    """
    receipt_detail = db.get(PenerimaanBarangDetail, penerimaan_barang_detail_id)
    if receipt_detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"PenerimaanBarangDetail dengan ID {penerimaan_barang_detail_id} tidak ditemukan. {context} dibatalkan.",
        )

    qty_received = receipt_detail.qty
    qty_already_returned = get_qty_returned_purchase_so_far(
        db, penerimaan_barang_detail_id, exclude_retur_detail_id
    )
    qty_after = qty_already_returned + new_qty

    if qty_after > qty_received:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Over-return: qty yang akan di-retur ({new_qty}) + "
                f"qty sudah di-retur ({qty_already_returned}) = {qty_after} "
                f"melebihi qty received ({qty_received}) untuk barang "
                f"'{receipt_detail.barang.kode if receipt_detail.barang else 'unknown'}'. "
                f"Maximum qty yang bisa di-retur: {qty_received - qty_already_returned}. "
                f"{context} dibatalkan."
            ),
        )
