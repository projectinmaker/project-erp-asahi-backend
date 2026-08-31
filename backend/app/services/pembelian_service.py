"""
pembelian_service.py

Service layer untuk modul Pembelian.
Menghandle CRUD + auto-posting jurnal untuk:
- PurchaseOrder (+ PurchaseOrderDetail + TransaksiBiaya)
- PurchaseInvoice (+ PurchaseInvoiceDetail + TransaksiBiaya)
- PurchaseRetur (+ PurchaseReturDetail)
- PenerimaanBarang (+ PenerimaanBarangDetail)
"""

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
        harga = Decimal(str(d.get("harga", 0)))
        qty = int(d.get("qty", 0))
        diskon = Decimal(str(d.get("diskon", 0)))
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
        harga = Decimal(str(d.get("harga", 0)))
        qty = int(d.get("qty", 0))
        line_total = harga * qty
        sub_total += line_total
        d["sub_total"] = line_total
    return sub_total


def _hitung_total_biaya(biaya_data: list) -> Decimal:
    """Hitung total biaya tambahan."""
    return sum(Decimal(str(b.get("jumlah", 0))) for b in biaya_data)


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
    - Auto-post jurnal jika auto_post_jurnal=True
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
            auto_post_jurnal=auto_post_jurnal,
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
                diskon=Decimal(str(d.get("diskon", 0))),
                sub_total=Decimal(str(d["sub_total"])),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, po, biaya_data, "purchase_order_id")

        # Auto-post jurnal (D: Persediaan, K: Utang Dagang)
        # Guard: skip jika supplier belum punya akun hutang (Phase 3 akan ganti mekanisme COA)
        if auto_post_jurnal and grand_total > 0 and supplier.akun_hutang_id:
            dasar_pajak = sub_total - total_diskon
            entries = [
                # Debit: Persediaan / Pembelian
                JurnalEntryItem(
                    akun_perkiraan_id=supplier.akun_hutang_id,  # TODO: ambil akun pembelian dari config
                    debit=dasar_pajak,
                    keterangan=f"Pembelian PO {no_pesanan} - {supplier.nama}",
                ),
                # Kredit: Utang Dagang
                JurnalEntryItem(
                    akun_perkiraan_id=supplier.akun_hutang_id,
                    kredit=grand_total,
                    keterangan=f"Utang PO {no_pesanan}",
                ),
            ]
            if total_ppn > 0:
                entries.insert(
                    1,
                    JurnalEntryItem(
                        akun_perkiraan_id=supplier.akun_hutang_id,  # TODO: ambil akun PPN Masukan dari config
                        debit=total_ppn,
                        keterangan=f"PPN Masukan PO {no_pesanan}",
                    ),
                )

            try:
                jurnal = auto_posting_jurnal(
                    db=db,
                    ref_module=RefModule.PURCHASE_ORDER,
                    ref_no=no_pesanan,
                    entries=entries,
                    keterangan=f"Purchase Order {no_pesanan}",
                    ref_id=po.id,
                    tanggal=tanggal,
                    created_by=created_by,
                )
                po.jurnal_umum_id = jurnal.id
            except Exception as e:
                logger.warning(f"Jurnal PO gagal diposting (non-fatal): {e}")

        db.commit()
        db.refresh(po)
        logger.info(f"PurchaseOrder created: {no_pesanan} | grand_total={grand_total}")
        return po

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseOrder: {e}")
        raise


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


def cancel_purchase_order(db: Session, db_obj: PurchaseOrder) -> PurchaseOrder:
    """Batalkan purchase order."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Order sudah dibatalkan")
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
                diskon=Decimal(str(d.get("diskon", 0))),
                sub_total=Decimal(str(d["sub_total"])),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, inv, biaya_data, "purchase_invoice_id")

        # Auto-post jurnal (D: Persediaan + PPN Masukan, K: Utang Dagang)
        # Guard: skip jika supplier belum punya akun hutang (Phase 3 akan ganti mekanisme COA)
        if auto_post_jurnal and grand_total > 0 and supplier.akun_hutang_id:
            dasar_pajak = sub_total - total_diskon
            entries = [
                # Debit: Persediaan / Pembelian
                JurnalEntryItem(
                    akun_perkiraan_id=supplier.akun_hutang_id,  # TODO: ambil akun pembelian dari config
                    debit=dasar_pajak,
                    keterangan=f"Pembelian PINV {no_form} - {supplier.nama}",
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
                        akun_perkiraan_id=supplier.akun_hutang_id,  # TODO: ambil akun PPN Masukan dari config
                        debit=total_ppn,
                        keterangan=f"PPN Masukan PINV {no_form}",
                    ),
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
            except Exception as e:
                logger.warning(f"Jurnal PINV gagal diposting (non-fatal): {e}")

        db.commit()
        db.refresh(inv)
        logger.info(f"PurchaseInvoice created: {no_form} | faktur={no_faktur} | grand_total={grand_total}")
        return inv

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseInvoice: {e}")
        raise


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
) -> PurchaseInvoice:
    """Update data purchase invoice (hanya field header)."""
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
    db.commit()
    db.refresh(db_obj)
    return db_obj


def cancel_purchase_invoice(db: Session, db_obj: PurchaseInvoice) -> PurchaseInvoice:
    """Batalkan purchase invoice."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Invoice sudah dibatalkan")
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
        if auto_post_jurnal and grand_total > 0 and supplier.akun_hutang_id:
            entries = [
                # Debit: Utang Dagang
                JurnalEntryItem(
                    akun_perkiraan_id=supplier.akun_hutang_id,
                    debit=grand_total,
                    keterangan=f"Kurangi utang retur {no_retur}",
                ),
                # Kredit: Retur Pembelian
                JurnalEntryItem(
                    akun_perkiraan_id=supplier.akun_hutang_id,  # TODO: akun retur pembelian dari config
                    kredit=sub_total,
                    keterangan=f"Retur Pembelian {no_retur}",
                ),
            ]
            if total_ppn > 0:
                entries.append(
                    JurnalEntryItem(
                        akun_perkiraan_id=supplier.akun_hutang_id,  # TODO: akun PPN Masukan dari config
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
                logger.warning(f"Jurnal Purchase Retur gagal diposting (non-fatal): {e}")

        db.commit()
        db.refresh(retur)
        logger.info(f"PurchaseRetur created: {no_retur} | grand_total={grand_total}")
        return retur

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseRetur: {e}")
        raise


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
) -> PurchaseRetur:
    """Update data purchase retur (hanya field header)."""
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
    db.commit()
    db.refresh(db_obj)
    return db_obj


def cancel_purchase_retur(db: Session, db_obj: PurchaseRetur) -> PurchaseRetur:
    """Batalkan purchase retur."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Retur sudah dibatalkan")
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


def create_penerimaan(
    db: Session,
    tanggal: datetime,
    purchase_order_id: UUID,
    supplier_id: UUID,
    details_data: list,
    alamat: Optional[str] = None,
    keterangan: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> PenerimaanBarang:
    """Buat PenerimaanBarang baru beserta detail.
    Tidak ada jurnal posting (penerimaan tidak mengubah keuangan langsung).
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
            no_form=no_form,
            tanggal=tanggal,
            purchase_order_id=purchase_order_id,
            supplier_id=supplier_id,
            alamat=alamat,
            keterangan=keterangan,
            status=StatusPenjualan.DIPROSES,
            created_by=created_by,
        )
        db.add(pb)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PenerimaanBarangDetail(
                penerimaan_barang_id=pb.id,
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


def update_penerimaan(
    db: Session,
    db_obj: PenerimaanBarang,
    tanggal: Optional[datetime] = None,
    purchase_order_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    alamat: Optional[str] = None,
    keterangan: Optional[str] = None,
) -> PenerimaanBarang:
    """Update data penerimaan barang (hanya field header)."""
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

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def cancel_penerimaan(db: Session, db_obj: PenerimaanBarang) -> PenerimaanBarang:
    """Batalkan penerimaan barang."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Penerimaan Barang sudah dibatalkan")
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenerimaanBarang cancelled: {db_obj.no_form}")
    return db_obj


def finish_penerimaan(db: Session, db_obj: PenerimaanBarang) -> PenerimaanBarang:
    """Finalisasi penerimaan barang — status SELESAI + tambah stok barang."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Penerimaan yang sudah dibatalkan tidak bisa difinalisasi")
    if db_obj.status == StatusPenjualan.SELESAI:
        raise ValueError("Penerimaan sudah selesai")

    # Tambah stok untuk setiap detail barang yang diterima
    for detail in db_obj.details:
        update_stok_barang(
            db=db,
            barang_id=detail.barang_id,
            qty_change=detail.qty,
            mode="TAMBAH",
            deskripsi=f"Penerimaan Barang {db_obj.no_form}",
            ref_module=RefModule.PURCHASE_INVOICE,
            ref_no=db_obj.no_form,
            ref_id=db_obj.id,
        )

    db_obj.status = StatusPenjualan.SELESAI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenerimaanBarang finished: {db_obj.no_form} | stok ditambahkan")
    return db_obj


def finish_purchase_retur(db: Session, db_obj: PurchaseRetur) -> PurchaseRetur:
    """Finalisasi purchase retur — status SELESAI + kurangi stok barang."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Retur yang sudah dibatalkan tidak bisa difinalisasi")
    if db_obj.status == StatusPenjualan.SELESAI:
        raise ValueError("Purchase Retur sudah selesai")

    # Kurangi stok untuk setiap detail barang yang dikembalikan ke supplier
    for detail in db_obj.details:
        update_stok_barang(
            db=db,
            barang_id=detail.barang_id,
            qty_change=detail.qty,
            mode="KURANGI",
            deskripsi=f"Retur Pembelian {db_obj.no_retur}",
            ref_module=RefModule.PURCHASE_RETUR,
            ref_no=db_obj.no_retur,
            ref_id=db_obj.id,
        )

    db_obj.status = StatusPenjualan.SELESAI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseRetur finished: {db_obj.no_retur} | stok dikurangi")
    return db_obj
