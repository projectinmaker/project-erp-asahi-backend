"""
pembelian_service.py

Service layer untuk modul Pembelian.
Menghandle CRUD + auto-posting jurnal untuk:
- PurchaseOrder (+ PurchaseOrderDetail + TransaksiBiaya)
- PurchaseInvoice (+ PurchaseInvoiceDetail + TransaksiBiaya)
- PurchaseRetur (+ PurchaseReturDetail)
- PenerimaanBarang (+ PenerimaanBarangDetail)
"""

from app.services.accounting_control import atomic_accounting_write, require_unposted, require_no_stock_movement
from app.services.posting_service import reverse_journal

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.transaksi.pembelian.purchase_order import PurchaseOrder
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.models.transaksi.pembelian.purchase_retur import PurchaseRetur
from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
from app.models.detail.purchase_order_detail import PurchaseOrderDetail
from app.models.detail.purchase_invoice_detail import PurchaseInvoiceDetail
from app.models.detail.purchase_retur_detail import PurchaseReturDetail
from app.models.detail.penerimaan_barang_detail import PenerimaanBarangDetail
from app.models.transaksi.transaksi_biaya import TransaksiBiaya
from app.models.master.supplier import Supplier
from app.models.transaksi.penjualan.sales_order import StatusPenjualan
from app.models.transaksi.jurnal import RefModule
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.services.stok_service import update_stok_barang
from app.services.decimal_utils import safe_decimal, safe_int
from app.services.setting_akun_service import (
    get_akun_id_or_raise,
    KEY_PEMBELIAN,
    KEY_PPN_MASUKAN,
    KEY_RETUR_PEMBELIAN,
    KEY_BEBAN_ANGKUT_PEMBELIAN,
)
from app.utils.nomor_dokumen import get_nomor_dokumen


# ==========================================
# HELPER: Hitung total dari detail
# ==========================================
def _hitung_total_detail_with_diskon(details_data: list) -> Tuple[Decimal, Decimal]:
    """Hitung sub_total dan total_diskon dari list detail (harga * qty, diskon per line).
    Mengembalikan (sub_total_bruto, total_diskon_nilai).
    """
    sub_total = Decimal("0")
    total_diskon = Decimal("0")
    for d in details_data:
        harga = safe_decimal(d.get("harga"))
        qty = safe_int(d.get("qty"))
        diskon = safe_decimal(d.get("diskon"))
        line_total = harga * qty
        diskon_nilai = line_total * diskon / Decimal("100")
        sub_total += line_total
        total_diskon += diskon_nilai
        d["sub_total"] = line_total - diskon_nilai
    return sub_total, total_diskon


def _hitung_total_detail_no_diskon(details_data: list) -> Decimal:
    """Hitung sub_total dari list detail tanpa diskon (harga * qty).
    Digunakan untuk PurchaseRetur.
    """
    sub_total = Decimal("0")
    for d in details_data:
        harga = safe_decimal(d.get("harga"))
        qty = safe_int(d.get("qty"))
        line_total = harga * qty
        sub_total += line_total
        d["sub_total"] = line_total
    return sub_total


def _hitung_total_biaya(biaya_data: list) -> Decimal:
    """Hitung total biaya tambahan."""
    return sum((safe_decimal(b.get("jumlah")) for b in biaya_data), Decimal("0"))


def _hitung_grand_total(
    sub_total: Decimal,
    total_diskon: Decimal,
    ppn_pct: Decimal,
    total_biaya_tambahan: Decimal,
) -> Tuple[Decimal, Decimal]:
    """Hitung total_ppn dan grand_total."""
    dasar_pajak = sub_total - total_diskon
    total_ppn = dasar_pajak * ppn_pct / Decimal("100")
    grand_total = dasar_pajak + total_ppn + total_biaya_tambahan
    return total_ppn, grand_total


def _create_biaya_tambahan(db: Session, model_obj, biaya_data: list, fk_field: str):
    """Buat TransaksiBiaya rows untuk PO atau PINV."""
    for b in biaya_data:
        tb = TransaksiBiaya(
            nama=b["nama"],
            jumlah=Decimal(str(b["jumlah"])),
            **{fk_field: model_obj.id},
        )
        db.add(tb)


# ==========================================
# PURCHASE ORDER
# ==========================================

def get_purchase_order_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PurchaseOrder], int]:
    """Ambil daftar purchase order dengan filter & pagination."""
    query = db.query(PurchaseOrder).options(
        joinedload(PurchaseOrder.supplier),
        joinedload(PurchaseOrder.creator),
        joinedload(PurchaseOrder.details).joinedload(PurchaseOrderDetail.barang),
        joinedload(PurchaseOrder.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PurchaseOrder.no_pesanan.ilike(pattern)
            | PurchaseOrder.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PurchaseOrder.status == status)
    if supplier_id:
        query = query.filter(PurchaseOrder.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(PurchaseOrder.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PurchaseOrder.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PurchaseOrder.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_purchase_order_by_id(db: Session, po_id: UUID) -> Optional[PurchaseOrder]:
    """Ambil 1 purchase order berdasarkan ID dengan detail."""
    return (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.supplier),
            joinedload(PurchaseOrder.creator),
            joinedload(PurchaseOrder.jurnal),
            joinedload(PurchaseOrder.details).joinedload(PurchaseOrderDetail.barang),
            joinedload(PurchaseOrder.biaya_tambahan),
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )


@atomic_accounting_write
def create_purchase_order(
    db: Session,
    tanggal: datetime,
    supplier_id: UUID,
    details_data: list,
    biaya_data: Optional[list] = None,
    tanggal_kirim: Optional[datetime] = None,
    alamat: Optional[str] = None,
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
) -> PurchaseOrder:
    """Buat PurchaseOrder baru beserta detail + biaya tambahan.
    - Generate no_pesanan otomatis (PO-YYYY-MM-NNN)
    - Hitung sub_total, total_diskon, ppn, grand_total dari detail
    - Order tidak melakukan posting jurnal; pencatatan dilakukan pada invoice.
    """
    try:
        biaya_data = biaya_data or []

        # Validasi supplier
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

        # Hitung sub_total dan total_diskon dari detail
        sub_total, total_diskon = _hitung_total_detail_with_diskon(details_data)

        total_biaya_tambahan = _hitung_total_biaya(biaya_data)
        total_ppn, grand_total = _hitung_grand_total(
            sub_total, total_diskon, ppn, total_biaya_tambahan
        )

        # Generate nomor pesanan
        no_pesanan = get_nomor_dokumen(
            db, PurchaseOrder, prefix="PO",
            no_column="no_pesanan", tanggal=tanggal.date()
        )

        # Buat header
        po = PurchaseOrder(
            no_pesanan=no_pesanan,
            tanggal=tanggal,
            supplier_id=supplier_id,
            tanggal_kirim=tanggal_kirim,
            alamat=alamat,
            diskon_global=diskon_global,
            ppn=ppn,
            sub_total=sub_total,
            total_diskon=total_diskon,
            total_ppn=total_ppn,
            total_biaya_tambahan=total_biaya_tambahan,
            grand_total=grand_total,
            auto_post_jurnal=False,
            status=StatusPenjualan.DRAFT,
            keterangan=keterangan,
            created_by=created_by,
        )
        db.add(po)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PurchaseOrderDetail(
                purchase_order_id=po.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=Decimal(str(d["sub_total"])),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, po, biaya_data, "purchase_order_id")

        # Order tidak mengakui pendapatan/piutang atau pembelian/utang.
        db.commit()
        db.refresh(po)
        logger.info(f"PurchaseOrder created: {no_pesanan} | grand_total={grand_total}")
        return po

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseOrder: {e}")
        raise


@atomic_accounting_write
def update_purchase_order(
    db: Session,
    db_obj: PurchaseOrder,
    tanggal: Optional[datetime] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_kirim: Optional[datetime] = None,
    alamat: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> PurchaseOrder:
    """Update data purchase order (hanya field header, tidak re-calculate detail)."""
    require_unposted(db_obj)
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Purchase Order dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if supplier_id is not None:
        db_obj.supplier_id = supplier_id
    if tanggal_kirim is not None:
        db_obj.tanggal_kirim = tanggal_kirim
    if alamat is not None:
        db_obj.alamat = alamat
    if diskon_global is not None:
        db_obj.diskon_global = diskon_global
    if ppn is not None:
        db_obj.ppn = ppn
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_purchase_order(db: Session, db_obj: PurchaseOrder, user_id: Optional[UUID] = None) -> PurchaseOrder:
    """Batalkan purchase order."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Order sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseOrder cancelled: {db_obj.no_pesanan}")
    return db_obj


# ==========================================
# PURCHASE INVOICE
# ==========================================

def get_purchase_invoice_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PurchaseInvoice], int]:
    """Ambil daftar purchase invoice dengan filter & pagination."""
    query = db.query(PurchaseInvoice).options(
        joinedload(PurchaseInvoice.supplier),
        joinedload(PurchaseInvoice.creator),
        joinedload(PurchaseInvoice.details).joinedload(PurchaseInvoiceDetail.barang),
        joinedload(PurchaseInvoice.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PurchaseInvoice.no_form.ilike(pattern)
            | PurchaseInvoice.no_faktur.ilike(pattern)
            | PurchaseInvoice.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PurchaseInvoice.status == status)
    if supplier_id:
        query = query.filter(PurchaseInvoice.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(PurchaseInvoice.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PurchaseInvoice.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PurchaseInvoice.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_purchase_invoice_by_id(db: Session, inv_id: UUID) -> Optional[PurchaseInvoice]:
    """Ambil 1 purchase invoice berdasarkan ID dengan detail."""
    return (
        db.query(PurchaseInvoice)
        .options(
            joinedload(PurchaseInvoice.supplier),
            joinedload(PurchaseInvoice.creator),
            joinedload(PurchaseInvoice.jurnal),
            joinedload(PurchaseInvoice.details).joinedload(PurchaseInvoiceDetail.barang),
            joinedload(PurchaseInvoice.biaya_tambahan),
        )
        .filter(PurchaseInvoice.id == inv_id)
        .first()
    )


@atomic_accounting_write
def create_purchase_invoice(
    db: Session,
    tanggal: datetime,
    supplier_id: UUID,
    no_faktur: str,
    details_data: list,
    biaya_data: Optional[list] = None,
    alamat: Optional[str] = None,
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
    tanggal_jatuh_tempo=None,
    syarat_bayar_id=None,
) -> PurchaseInvoice:
    """Buat PurchaseInvoice baru beserta detail + biaya tambahan."""
    try:
        biaya_data = biaya_data or []

        # Validasi supplier
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

        # Hitung sub_total dan total_diskon dari detail
        sub_total, total_diskon = _hitung_total_detail_with_diskon(details_data)

        total_biaya_tambahan = _hitung_total_biaya(biaya_data)
        total_ppn, grand_total = _hitung_grand_total(
            sub_total, total_diskon, ppn, total_biaya_tambahan
        )

        # Generate nomor form
        no_form = get_nomor_dokumen(
            db, PurchaseInvoice, prefix="PINV",
            no_column="no_form", tanggal=tanggal.date()
        )

        # Buat header
        inv = PurchaseInvoice(
            no_form=no_form,
            no_faktur=no_faktur,
            tanggal=tanggal,
            supplier_id=supplier_id,
            alamat=alamat,
            diskon_global=diskon_global,
            ppn=ppn,
            sub_total=sub_total,
            total_diskon=total_diskon,
            total_ppn=total_ppn,
            total_biaya_tambahan=total_biaya_tambahan,
            grand_total=grand_total,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusPenjualan.DRAFT,
            keterangan=keterangan,
            created_by=created_by,
        )
        db.add(inv)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PurchaseInvoiceDetail(
                purchase_invoice_id=inv.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=Decimal(str(d["sub_total"])),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, inv, biaya_data, "purchase_invoice_id")

        # Auto-post jurnal (D: Persediaan + PPN Masukan, K: Utang Dagang)
        # Guard: skip jika supplier belum punya akun hutang (Phase 3 akan ganti mekanisme COA)
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(inv)
        if auto_post_jurnal:
            post_purchase_invoice(db, inv, created_by)

        from app.services.invoice_terms import set_due_date
        if syarat_bayar_id is not None:
            inv.syarat_bayar_id = syarat_bayar_id
        set_due_date(db, inv, tanggal_jatuh_tempo)
        db.commit()
        db.refresh(inv)
        logger.info(f"PurchaseInvoice created: {no_form} | faktur={no_faktur} | grand_total={grand_total}")
        return inv

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseInvoice: {e}")
        raise


@atomic_accounting_write
def update_purchase_invoice(
    db: Session,
    db_obj: PurchaseInvoice,
    tanggal: Optional[datetime] = None,
    supplier_id: Optional[UUID] = None,
    no_faktur: Optional[str] = None,
    alamat: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    tanggal_jatuh_tempo=None,
    syarat_bayar_id=None,
) -> PurchaseInvoice:
    """Update data purchase invoice (hanya field header)."""
    require_unposted(db_obj)
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Purchase Invoice dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if supplier_id is not None:
        db_obj.supplier_id = supplier_id
    if no_faktur is not None:
        db_obj.no_faktur = no_faktur
    if alamat is not None:
        db_obj.alamat = alamat
    if diskon_global is not None:
        db_obj.diskon_global = diskon_global
    if ppn is not None:
        db_obj.ppn = ppn
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    from app.services.invoice_terms import set_due_date
    if syarat_bayar_id is not None:
        db_obj.syarat_bayar_id = syarat_bayar_id
    if tanggal_jatuh_tempo is not None or tanggal is not None or syarat_bayar_id is not None:
        set_due_date(db, db_obj, tanggal_jatuh_tempo)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_purchase_invoice(db: Session, db_obj: PurchaseInvoice, user_id: Optional[UUID] = None) -> PurchaseInvoice:
    """Batalkan purchase invoice."""
    from app.services.settlement_service import require_no_settlements
    require_no_settlements(db, db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Invoice sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseInvoice cancelled: {db_obj.no_form}")
    return db_obj


# ==========================================
# PURCHASE RETUR
# ==========================================

def get_purchase_retur_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PurchaseRetur], int]:
    """Ambil daftar purchase retur dengan filter & pagination."""
    query = db.query(PurchaseRetur).options(
        joinedload(PurchaseRetur.purchase_order),
        joinedload(PurchaseRetur.supplier),
        joinedload(PurchaseRetur.creator),
        joinedload(PurchaseRetur.details).joinedload(PurchaseReturDetail.barang),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PurchaseRetur.no_retur.ilike(pattern)
            | PurchaseRetur.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PurchaseRetur.status == status)
    if supplier_id:
        query = query.filter(PurchaseRetur.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(PurchaseRetur.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PurchaseRetur.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PurchaseRetur.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_purchase_retur_by_id(db: Session, retur_id: UUID) -> Optional[PurchaseRetur]:
    """Ambil 1 purchase retur berdasarkan ID dengan detail."""
    return (
        db.query(PurchaseRetur)
        .options(
            joinedload(PurchaseRetur.purchase_order),
            joinedload(PurchaseRetur.supplier),
            joinedload(PurchaseRetur.creator),
            joinedload(PurchaseRetur.jurnal),
            joinedload(PurchaseRetur.details).joinedload(PurchaseReturDetail.barang),
        )
        .filter(PurchaseRetur.id == retur_id)
        .first()
    )


@atomic_accounting_write
def create_purchase_retur(
    db: Session,
    tanggal: datetime,
    purchase_order_id: UUID,
    supplier_id: UUID,
    details_data: list,
    alamat: Optional[str] = None,
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
    purchase_invoice_id=None,
    gudang_id: Optional[UUID] = None,
) -> PurchaseRetur:
    """Buat PurchaseRetur baru beserta detail.
    Jurnal: D - Utang Dagang, K - Retur Pembelian / Persediaan
    """
    try:
        # Validasi supplier
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

        # Hitung sub_total dari detail (tanpa diskon)
        sub_total = _hitung_total_detail_no_diskon(details_data)

        total_ppn, grand_total = _hitung_grand_total(
            sub_total, Decimal("0"), ppn, Decimal("0")
        )

        # Generate nomor retur
        no_retur = get_nomor_dokumen(
            db, PurchaseRetur, prefix="RET-B",
            no_column="no_retur", tanggal=tanggal.date()
        )

        # Buat header
        retur = PurchaseRetur(
            gudang_id=gudang_id,
            no_retur=no_retur,
            tanggal=tanggal,
            purchase_order_id=purchase_order_id,
            supplier_id=supplier_id,
            alamat=alamat,
            ppn=ppn,
            sub_total=sub_total,
            total_ppn=total_ppn,
            grand_total=grand_total,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusPenjualan.DRAFT,
            keterangan=keterangan,
            created_by=created_by,
        )
        db.add(retur)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PurchaseReturDetail(
                purchase_retur_id=retur.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                sub_total=Decimal(str(d["sub_total"])),
            )
            db.add(detail)

        # Auto-post jurnal (D: Utang Dagang, K: Retur Pembelian)
        # Guard: skip jika supplier belum punya akun hutang (Phase 3 akan ganti mekanisme COA)
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(retur)
        if purchase_invoice_id is not None:
            retur.purchase_invoice_id = purchase_invoice_id
            from app.services.settlement_service import validate_return
            validate_return(db, retur)
        if auto_post_jurnal:
            post_purchase_retur(db, retur, created_by)

        if purchase_invoice_id is not None:
            retur.purchase_invoice_id = purchase_invoice_id
        db.commit()
        db.refresh(retur)
        logger.info(f"PurchaseRetur created: {no_retur} | grand_total={grand_total}")
        return retur

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseRetur: {e}")
        raise


@atomic_accounting_write
def update_purchase_retur(
    db: Session,
    db_obj: PurchaseRetur,
    tanggal: Optional[datetime] = None,
    purchase_order_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    alamat: Optional[str] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    purchase_invoice_id=None,
    gudang_id: Optional[UUID] = None,
) -> PurchaseRetur:
    """Update data purchase retur (hanya field header)."""
    require_unposted(db_obj)
    if gudang_id is not None:
        db_obj.gudang_id = gudang_id
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Purchase Retur dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if purchase_order_id is not None:
        db_obj.purchase_order_id = purchase_order_id
    if supplier_id is not None:
        db_obj.supplier_id = supplier_id
    if alamat is not None:
        db_obj.alamat = alamat
    if ppn is not None:
        db_obj.ppn = ppn
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    if purchase_invoice_id is not None:
        db_obj.purchase_invoice_id = purchase_invoice_id
    if db_obj.purchase_invoice_id:
        from app.services.settlement_service import validate_return
        validate_return(db, db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_purchase_retur(db: Session, db_obj: PurchaseRetur, user_id: Optional[UUID] = None) -> PurchaseRetur:
    """Batalkan purchase retur."""
    require_no_stock_movement(db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Retur sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseRetur cancelled: {db_obj.no_retur}")
    return db_obj


# ==========================================
# PENERIMAAN BARANG
# ==========================================

def get_penerimaan_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PenerimaanBarang], int]:
    """Ambil daftar penerimaan barang dengan filter & pagination."""
    query = db.query(PenerimaanBarang).options(
        joinedload(PenerimaanBarang.purchase_order),
        joinedload(PenerimaanBarang.supplier),
        joinedload(PenerimaanBarang.creator),
        joinedload(PenerimaanBarang.details).joinedload(PenerimaanBarangDetail.barang),
        joinedload(PenerimaanBarang.details).joinedload(PenerimaanBarangDetail.satuan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PenerimaanBarang.no_form.ilike(pattern)
            | PenerimaanBarang.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PenerimaanBarang.status == status)
    if supplier_id:
        query = query.filter(PenerimaanBarang.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(PenerimaanBarang.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PenerimaanBarang.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PenerimaanBarang.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_penerimaan_by_id(db: Session, pb_id: UUID) -> Optional[PenerimaanBarang]:
    """Ambil 1 penerimaan barang berdasarkan ID dengan detail."""
    return (
        db.query(PenerimaanBarang)
        .options(
            joinedload(PenerimaanBarang.purchase_order),
            joinedload(PenerimaanBarang.supplier),
            joinedload(PenerimaanBarang.creator),
            joinedload(PenerimaanBarang.details).joinedload(PenerimaanBarangDetail.barang),
            joinedload(PenerimaanBarang.details).joinedload(PenerimaanBarangDetail.satuan),
        )
        .filter(PenerimaanBarang.id == pb_id)
        .first()
    )


@atomic_accounting_write
def create_penerimaan(
    db: Session,
    tanggal: datetime,
    purchase_order_id: UUID,
    supplier_id: UUID,
    details_data: list,
    alamat: Optional[str] = None,
    keterangan: Optional[str] = None,
    created_by: Optional[UUID] = None,
    gudang_id: Optional[UUID] = None,
    purchase_invoice_id: Optional[UUID] = None,
) -> PenerimaanBarang:
    """Buat PenerimaanBarang baru beserta detail.

    Tahap 2: `purchase_invoice_id` opsional untuk anti double-record Persediaan.
    Jika diisi DAN invoice terkait sudah di-POST, saat invoice dipost, sistem
    akan D: PENERIMAAN_DALAM_PROSES (clearing) alih-alih D: Pembelian.

    Tidak ada jurnal posting saat create; jurnal di-post saat `finish_penerimaan`.
    """
    try:
        # Validasi supplier
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

        # Generate nomor form penerimaan
        no_form = get_nomor_dokumen(
            db, PenerimaanBarang, prefix="PB",
            no_column="no_form", tanggal=tanggal.date()
        )

        # Buat header
        pb = PenerimaanBarang(
            gudang_id=gudang_id,
            no_form=no_form,
            tanggal=tanggal,
            purchase_order_id=purchase_order_id,
            supplier_id=supplier_id,
            alamat=alamat,
            keterangan=keterangan,
            purchase_invoice_id=purchase_invoice_id,
            status=StatusPenjualan.DIPROSES,
            created_by=created_by,
        )
        db.add(pb)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PenerimaanBarangDetail(
                penerimaan_barang_id=pb.id,
                harga_perolehan=d.get("harga_perolehan"),
                tanggal_kedaluwarsa=d.get("tanggal_kedaluwarsa"),
                barang_id=d["barang_id"],
                qty=int(d["qty"]),
                satuan_id=d["satuan_id"],
            )
            db.add(detail)

        db.commit()
        db.refresh(pb)
        logger.info(f"PenerimaanBarang created: {no_form}")
        return pb

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PenerimaanBarang: {e}")
        raise


@atomic_accounting_write
def update_penerimaan(
    db: Session,
    db_obj: PenerimaanBarang,
    tanggal: Optional[datetime] = None,
    purchase_order_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    alamat: Optional[str] = None,
    keterangan: Optional[str] = None,
    gudang_id: Optional[UUID] = None,
    purchase_invoice_id: Optional[UUID] = None,
) -> PenerimaanBarang:
    """Update data penerimaan barang (hanya field header).

    Tahap 2: `purchase_invoice_id` bisa diisi untuk link ke invoice (clearing).
    Kirim `None` eksplisit untuk mengosongkan link.
    """
    require_unposted(db_obj)
    if gudang_id is not None:
        db_obj.gudang_id = gudang_id
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Penerimaan Barang dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if purchase_order_id is not None:
        db_obj.purchase_order_id = purchase_order_id
    if supplier_id is not None:
        db_obj.supplier_id = supplier_id
    if alamat is not None:
        db_obj.alamat = alamat
    if keterangan is not None:
        db_obj.keterangan = keterangan
    # Tahap 2: link ke purchase_invoice (gunakan sentinel untuk distinguish
    # "tidak diubah" vs "dikosongkan" — None artinya tidak diubah, sedangkan
    # eksplisit None hanya bisa terjadi kalau caller pakai kata kunci khusus).
    # Karena Optional[UUID] = None juga berarti "tidak diubah", kita pakai
    # parameter eksplisit via schema (exclude_unset=True di endpoint).
    if purchase_invoice_id is not None:
        db_obj.purchase_invoice_id = purchase_invoice_id

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_penerimaan(db: Session, db_obj: PenerimaanBarang, user_id: Optional[UUID] = None) -> PenerimaanBarang:
    """Batalkan penerimaan barang."""
    require_no_stock_movement(db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Penerimaan Barang sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenerimaanBarang cancelled: {db_obj.no_form}")
    return db_obj


@atomic_accounting_write
def finish_penerimaan(db: Session, db_obj: PenerimaanBarang) -> PenerimaanBarang:
    """Finalisasi penerimaan barang — status SELESAI + tambah stok barang +
    (Tahap 2) auto-post jurnal Persediaan jika akun perantara Penerimaan Dalam
    Proses sudah di-configure di setting_akun.

    Jurnal Tahap 2 (jika PENERIMAAN_DALAM_PROSES ada di setting_akun):
        D: Persediaan (per-barang, dari mapping barang atau fallback kategori)
        K: Penerimaan Dalam Proses (GRNI)

    Anti double-record:
    - Saat `post_purchase_invoice` dijalankan dan invoice terkait punya
      penerimaan yang sudah di-posting (link via `purchase_invoice_id`),
      invoice akan D: Penerimaan Dalam Proses (clearing) alih-alih D: Pembelian.
    - Jika akun perantara belum di-configure: penerimaan TIDAK mempost jurnal
      (legacy), invoice tetap D: Pembelian, K: Utang Dagang (cara lama).

    Riwayat jurnal POSTED tetap dipertahankan; tidak ada rewrite transaksi lama.
    """
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Penerimaan yang sudah dibatalkan tidak bisa difinalisasi")
    if db_obj.status == StatusPenjualan.SELESAI:
        raise ValueError("Penerimaan sudah selesai")

    # Cek apakah akun perantara Penerimaan Dalam Proses tersedia (opsional)
    from app.services.persediaan_service import (
        _get_akun_persediaan_id,
        _get_akun_penerimaan_dalam_proses_id,
    )
    grni_account_id = _get_akun_penerimaan_dalam_proses_id(db)
    hpp_account_id = get_akun_id_or_raise(db, KEY_PEMBELIAN, context=db_obj.no_form)

    # Tambah stok untuk setiap detail barang yang diterima
    inventory_entries = []
    for detail in db_obj.details:
        cost = detail.harga_perolehan
        if cost is None:
            lines = [r for r in db_obj.purchase_order.details if r.barang_id == detail.barang_id]
            if len(lines) != 1:
                raise ValueError('Isi hargaPerolehan; harga dari PO tidak dapat ditentukan secara unik')
            cost = lines[0].harga * (Decimal(100) - lines[0].diskon) / Decimal(100)
        if cost < 0:
            raise ValueError('Harga perolehan tidak boleh negatif')
        detail.harga_perolehan = cost
        movement = update_stok_barang(
            db=db,
            barang_id=detail.barang_id,
            qty_change=detail.qty,
            mode="TAMBAH",
            deskripsi=f"Penerimaan Barang {db_obj.no_form}",
            ref_module=RefModule.PURCHASE_INVOICE,
            ref_no=db_obj.no_form,
            ref_id=db_obj.id,
            gudang_id=db_obj.gudang_id,
            harga_satuan=cost,
            tanggal_kedaluwarsa=detail.tanggal_kedaluwarsa,
        )

        # Tahap 2: snapshot akun Persediaan di StokMutasi (untuk retur nanti)
        if grni_account_id:
            inventory_account_id = _get_akun_persediaan_id(db, detail.barang)
            movement['mutasi'].inventory_account_id = inventory_account_id
            movement['mutasi'].expense_account_id = grni_account_id  # akun lawan (perantara)
            line_value = movement['total_nilai']
            if line_value:
                inventory_entries.append((inventory_account_id, line_value, detail.barang.nama))

    # Posting jurnal penerimaan (hanya jika akun perantara tersedia)
    if grni_account_id and inventory_entries:
        entries = []
        for inventory_account_id, line_value, barang_nama in inventory_entries:
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=inventory_account_id,
                debit=line_value,
                keterangan=f"Penerimaan {db_obj.no_form} - {barang_nama}",
            ))
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=grni_account_id,
                kredit=line_value,
                keterangan=f"GRNI {db_obj.no_form} - {barang_nama}",
            ))
        try:
            jurnal = auto_posting_jurnal(
                db=db,
                ref_module=RefModule.PURCHASE_INVOICE,  # konsisten dgn ref_module StokMutasi
                ref_no=db_obj.no_form,
                entries=entries,
                keterangan=f"Penerimaan Barang {db_obj.no_form} (GRNI)",
                ref_id=db_obj.id,
                tanggal=db_obj.tanggal,
                created_by=db_obj.created_by,
                tipe_transaksi="PENERIMAAN_GRNI",
            )
            db_obj.jurnal_umum_id = jurnal.id
            logger.info(
                f"Penerimaan jurnal posted: {jurnal.no_jurnal} | "
                f"D: Persediaan, K: GRNI | total={sum(v for _, v, _ in inventory_entries)}"
            )
        except Exception as e:
            raise ValueError(f"Jurnal penerimaan gagal diposting: {e}") from e
    elif grni_account_id is None:
        logger.info(
            f"Penerimaan {db_obj.no_form}: akun perantara PENERIMAAN_DALAM_PROSES belum "
            f"di-configure; jurnal Persediaan tidak diposting (legacy mode)."
        )

    db_obj.status = StatusPenjualan.SELESAI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenerimaanBarang finished: {db_obj.no_form} | stok ditambahkan")
    return db_obj


@atomic_accounting_write
def finish_purchase_retur(db: Session, db_obj: PurchaseRetur) -> PurchaseRetur:
    """Finalisasi purchase retur — status SELESAI + kurangi stok barang."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Retur yang sudah dibatalkan tidak bisa difinalisasi")
    if db_obj.status == StatusPenjualan.SELESAI:
        raise ValueError("Purchase Retur sudah selesai")

    if not db_obj.purchase_invoice_id:
        raise ValueError('Retur stok memerlukan purchaseInvoiceId')
    prior_returns = db.query(PurchaseRetur).filter_by(purchase_invoice_id=db_obj.purchase_invoice_id, status=StatusPenjualan.SELESAI).all()
    quantities = {}
    for row in db_obj.details:
        quantities[row.barang_id] = quantities.get(row.barang_id, 0) + row.qty
    for item_id, quantity in quantities.items():
        purchased = sum(row.qty for row in db_obj.purchase_invoice.details if row.barang_id == item_id)
        returned = sum(row.qty for retur in prior_returns for row in retur.details if row.barang_id == item_id)
        if quantity <= 0 or quantity + returned > purchased:
            raise ValueError('Qty retur melebihi qty invoice pembelian yang belum diretur')

    # Kurangi stok untuk setiap detail barang yang dikembalikan ke supplier
    stock_entries = []
    for detail in db_obj.details:
        movement = update_stok_barang(
            db=db,
            barang_id=detail.barang_id,
            qty_change=detail.qty,
            mode="KURANGI",
            deskripsi=f"Retur Pembelian {db_obj.no_retur}",
            ref_module=RefModule.PURCHASE_RETUR,
            ref_no=db_obj.no_retur,
            ref_id=db_obj.id,
            gudang_id=db_obj.gudang_id,
        )

        from app.services.persediaan_service import _get_akun_persediaan_id
        value = movement['total_nilai']
        if value:
            stock_entries += [JurnalEntryItem(get_akun_id_or_raise(db, KEY_RETUR_PEMBELIAN, context=db_obj.no_retur), debit=value),
                              JurnalEntryItem(_get_akun_persediaan_id(db, detail.barang), kredit=value)]
    if stock_entries:
        auto_posting_jurnal(db, RefModule.PURCHASE_RETUR, db_obj.no_retur, stock_entries,
            ref_id=db_obj.id, tanggal=db_obj.tanggal, created_by=db_obj.created_by, tipe_transaksi='RETUR_STOCK')

    db_obj.status = StatusPenjualan.SELESAI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseRetur finished: {db_obj.no_retur} | stok dikurangi")
    return db_obj

def post_purchase_invoice(db: Session, inv, created_by):
    """Post the existing document; caller owns commit/rollback and workflow checks.

    Tahap 2 — Anti double-record Persediaan:
    - Jika invoice ter-link ke penerimaan_barang yang sudah SELESAI (via
      `penerimaan_barang.purchase_invoice_id`) DAN akun perantara
      PENERIMAAN_DALAM_PROSES sudah di-configure: invoice mempost
      D: PENERIMAAN_DALAM_PROSES (clearing GRNI), bukan D: Pembelian.
    - Jika tidak ada penerimaan terkait atau akun perantara belum di-configure:
      tetap D: Pembelian (legacy/cara lama).

    Riwayat jurnal POSTED tetap dipertahankan.
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, inv)
    tanggal = inv.tanggal
    supplier = db.get(Supplier, inv.supplier_id)
    no_form = inv.no_form
    no_faktur = inv.no_faktur
    sub_total = inv.sub_total
    total_diskon = inv.total_diskon
    total_ppn = inv.total_ppn
    total_biaya_tambahan = inv.total_biaya_tambahan
    grand_total = inv.grand_total
    if not supplier.akun_hutang_id:
        raise ValueError("Mapping akun akun_hutang_id belum diisi; posting dibatalkan")
    dasar_pajak = sub_total - total_diskon

    # Tahap 2: Deteksi penerimaan terkait yang sudah di-posting (GRNI clearing)
    from app.services.persediaan_service import _get_akun_penerimaan_dalam_proses_id
    grni_account_id = _get_akun_penerimaan_dalam_proses_id(db)
    linked_penerimaan = None
    if grni_account_id:
        # Cari penerimaan yang sudah SELESAI dan ter-link ke invoice ini
        # (link dibuat saat penerimaan di-update dengan purchase_invoice_id,
        # atau saat invoice di-link ke penerimaan yang sudah ada).
        linked_penerimaan = (
            db.query(PenerimaanBarang)
            .filter(
                PenerimaanBarang.purchase_invoice_id == inv.id,
                PenerimaanBarang.status == StatusPenjualan.SELESAI,
                PenerimaanBarang.jurnal_umum_id.isnot(None),  # sudah punya jurnal GRNI
            )
            .first()
        )

    # Pilih akun debit utama
    if linked_penerimaan and grni_account_id:
        # Clearing mode: D: Penerimaan Dalam Proses (clear GRNI yang diposting saat penerimaan)
        debit_account_id = grni_account_id
        debit_keterangan = f"Clearing GRNI PINV {no_form} (Penerimaan {linked_penerimaan.no_form})"
        logger.info(
            f"PINV {no_form}: linked ke penerimaan {linked_penerimaan.no_form}; "
            f"D: PENERIMAAN_DALAM_PROSES (clearing), bukan D: Pembelian."
        )
    else:
        # Legacy mode: D: Pembelian
        debit_account_id = get_akun_id_or_raise(db, KEY_PEMBELIAN, context=f"PINV {no_form}")
        debit_keterangan = f"Pembelian PINV {no_form} - {supplier.nama}"

    entries = [
        # Debit: Persediaan / Pembelian / Penerimaan Dalam Proses
        JurnalEntryItem(
            akun_perkiraan_id=debit_account_id,
            debit=dasar_pajak,
            keterangan=debit_keterangan,
        ),
        # Kredit: Utang Dagang
        JurnalEntryItem(
            akun_perkiraan_id=supplier.akun_hutang_id,
            kredit=grand_total,
            keterangan=f"Utang PINV {no_form}",
        ),
    ]
    if total_ppn > 0:
        entries.insert(
            1,
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_PPN_MASUKAN, context=f"PINV {no_form}"
                ),
                debit=total_ppn,
                keterangan=f"PPN Masukan PINV {no_form}",
            ),
        )

    if total_biaya_tambahan > 0:
        # Debit: Beban Angkut Pembelian — mengimbangi grand_total (Kredit
        # Utang Dagang) yang sudah termasuk biaya tambahan, supaya jurnal balance.
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_BEBAN_ANGKUT_PEMBELIAN, context=f"PINV {no_form}"
                ),
                debit=total_biaya_tambahan,
                keterangan=f"Biaya tambahan PINV {no_form}",
            )
        )

    try:
        jurnal = auto_posting_jurnal(
            db=db,
            ref_module=RefModule.PURCHASE_INVOICE,
            ref_no=no_form,
            entries=entries,
            keterangan=f"Purchase Invoice {no_form} (Faktur: {no_faktur})",
            ref_id=inv.id,
            tanggal=tanggal,
            created_by=created_by,
        )
        inv.jurnal_umum_id = jurnal.id
        inv.akun_kontrol_id = supplier.akun_hutang_id
        inv.status = StatusPenjualan.SELESAI
    except Exception as e:
        raise ValueError(f"Jurnal PINV gagal diposting: {e}")
    return inv


def post_purchase_retur(db: Session, retur, created_by):
    """Post the existing document; caller owns commit/rollback and workflow checks."""
    from app.services.document_totals import validate_postable
    validate_postable(db, retur)
    from app.services.settlement_service import validate_return, control_account
    validate_return(db, retur)
    tanggal = retur.tanggal
    supplier = db.get(Supplier, retur.supplier_id)
    no_retur = retur.no_retur
    sub_total = retur.sub_total
    total_ppn = retur.total_ppn
    grand_total = retur.grand_total
    if not supplier.akun_hutang_id:
        raise ValueError("Mapping akun akun_hutang_id belum diisi; posting dibatalkan")
    entries = [
        # Debit: Utang Dagang
        JurnalEntryItem(
            akun_perkiraan_id=control_account(db, retur.purchase_invoice) if retur.purchase_invoice else supplier.akun_hutang_id,
            debit=grand_total,
            keterangan=f"Kurangi utang retur {no_retur}",
        ),
        # Kredit: Retur Pembelian
        JurnalEntryItem(
            akun_perkiraan_id=get_akun_id_or_raise(
                db, KEY_RETUR_PEMBELIAN, context=f"Retur {no_retur}"
            ),
            kredit=sub_total,
            keterangan=f"Retur Pembelian {no_retur}",
        ),
    ]
    if total_ppn > 0:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_PPN_MASUKAN, context=f"Retur {no_retur}"
                ),
                kredit=total_ppn,
                keterangan=f"PPN Retur {no_retur}",
            )
        )

    try:
        jurnal = auto_posting_jurnal(
            db=db,
            ref_module=RefModule.PURCHASE_RETUR,
            ref_no=no_retur,
            entries=entries,
            keterangan=f"Purchase Retur {no_retur}",
            ref_id=retur.id,
            tanggal=tanggal,
            created_by=created_by,
        )
        retur.jurnal_umum_id = jurnal.id
    except Exception as e:
        raise ValueError(f"Jurnal Purchase Retur gagal diposting: {e}")
    return retur
