"""
penjualan_service.py

Service layer untuk modul Penjualan.
Menghandle CRUD + auto-posting jurnal untuk:
- SalesOrder (+ SalesOrderDetail + TransaksiBiaya)
- SalesInvoice (+ SalesInvoiceDetail + TransaksiBiaya)
- SalesRetur (+ SalesReturDetail)
- PengirimanBarang (+ PengirimanBarangDetail)
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.transaksi.penjualan.sales_order import SalesOrder, StatusPenjualan
from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
from app.models.transaksi.penjualan.sales_retur import SalesRetur
from app.models.transaksi.penjualan.pengiriman_barang import PengirimanBarang
from app.models.detail.sales_order_detail import SalesOrderDetail
from app.models.detail.sales_invoice_detail import SalesInvoiceDetail
from app.models.detail.sales_retur_detail import SalesReturDetail
from app.models.detail.pengiriman_barang_detail import PengirimanBarangDetail
from app.models.transaksi.transaksi_biaya import TransaksiBiaya
from app.models.master.pelanggan import Pelanggan
from app.models.transaksi.jurnal import RefModule
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.utils.nomor_dokumen import get_nomor_dokumen


# ==========================================
# HELPER: Hitung total dari detail
# ==========================================
def _hitung_total_detail(details_data: list) -> Decimal:
    """Hitung sub_total dari list detail (harga * qty - diskon)."""
    return sum(Decimal(str(d.get("sub_total", d.get("harga", 0)) * Decimal(str(d.get("qty", 0))))) for d in details_data)


def _hitung_total_biaya(biaya_data: list) -> Decimal:
    """Hitung total biaya tambahan."""
    return sum(Decimal(str(b.get("jumlah", 0))) for b in biaya_data)


def _hitung_grand_total(
    sub_total: Decimal,
    total_diskon: Decimal,
    ppn_pct: Decimal,
    total_biaya_tambahan: Decimal,
) -> Tuple[Decimal, Decimal, Decimal]:
    """Hitung total_ppn dan grand_total.
    ppn = (sub_total - total_diskon) * ppn_pct / 100
    grand_total = sub_total - total_diskon + ppn + total_biaya_tambahan
    """
    dasar_pajak = sub_total - total_diskon
    total_ppn = dasar_pajak * ppn_pct / Decimal("100")
    grand_total = dasar_pajak + total_ppn + total_biaya_tambahan
    return total_ppn, grand_total


def _create_biaya_tambahan(db: Session, model_obj, biaya_data: list, fk_field: str):
    """Buat TransaksiBiaya rows untuk SO atau SINV."""
    for b in biaya_data:
        tb = TransaksiBiaya(
            nama=b["nama"],
            jumlah=Decimal(str(b["jumlah"])),
            **{fk_field: model_obj.id},
        )
        db.add(tb)


# ==========================================
# SALES ORDER
# ==========================================

def get_sales_order_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[SalesOrder], int]:
    """Ambil daftar sales order dengan filter & pagination."""
    query = db.query(SalesOrder).options(
        joinedload(SalesOrder.pelanggan),
        joinedload(SalesOrder.syarat_bayar),
        joinedload(SalesOrder.creator),
        joinedload(SalesOrder.details).joinedload(SalesOrderDetail.barang),
        joinedload(SalesOrder.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            SalesOrder.no_pesanan.ilike(pattern)
            | SalesOrder.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(SalesOrder.status == status)
    if pelanggan_id:
        query = query.filter(SalesOrder.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(SalesOrder.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(SalesOrder.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(SalesOrder.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_sales_order_by_id(db: Session, so_id: UUID) -> Optional[SalesOrder]:
    """Ambil 1 sales order berdasarkan ID dengan detail."""
    return (
        db.query(SalesOrder)
        .options(
            joinedload(SalesOrder.pelanggan),
            joinedload(SalesOrder.syarat_bayar),
            joinedload(SalesOrder.creator),
            joinedload(SalesOrder.jurnal),
            joinedload(SalesOrder.details).joinedload(SalesOrderDetail.barang),
            joinedload(SalesOrder.biaya_tambahan),
        )
        .filter(SalesOrder.id == so_id)
        .first()
    )


def create_sales_order(
    db: Session,
    tanggal: datetime,
    pelanggan_id: UUID,
    details_data: list,
    biaya_data: Optional[list] = None,
    syarat_bayar_id: Optional[UUID] = None,
    fob: Optional[str] = None,
    ekspedisi: Optional[str] = None,
    tanggal_pengiriman: Optional[datetime] = None,
    penjual: Optional[str] = None,
    alamat_pengiriman: Optional[str] = None,
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
) -> SalesOrder:
    """Buat SalesOrder baru beserta detail + biaya tambahan.
    - Generate no_pesanan otomatis
    - Hitung sub_total, total_diskon, ppn, grand_total dari detail
    - Auto-post jurnal jika auto_post_jurnal=True
    """
    try:
        biaya_data = biaya_data or []

        # Validasi pelanggan
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # Hitung sub_total dan total_diskon dari detail
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
            d["sub_total"] = line_total - diskon_nilai  # Update sub_total per line

        total_biaya_tambahan = _hitung_total_biaya(biaya_data)
        total_ppn, grand_total = _hitung_grand_total(
            sub_total, total_diskon, ppn, total_biaya_tambahan
        )

        # Generate nomor pesanan
        no_pesanan = get_nomor_dokumen(
            db, SalesOrder, prefix="SO",
            no_column="no_pesanan", tanggal=tanggal.date()
        )

        # Buat header
        so = SalesOrder(
            no_pesanan=no_pesanan,
            tanggal=tanggal,
            pelanggan_id=pelanggan_id,
            syarat_bayar_id=syarat_bayar_id,
            fob=fob,
            ekspedisi=ekspedisi,
            tanggal_pengiriman=tanggal_pengiriman,
            penjual=penjual,
            alamat_pengiriman=alamat_pengiriman,
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
        db.add(so)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = SalesOrderDetail(
                sales_order_id=so.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=Decimal(str(d.get("diskon", 0))),
                sub_total=Decimal(str(d["sub_total"])),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, so, biaya_data, "sales_order_id")

        # Auto-post jurnal (piutang dagang D, pendapatan penjualan K)
        if auto_post_jurnal and grand_total > 0:
            dasar_pajak = sub_total - total_diskon
            entries = [
                # Debit: Piutang Dagang (akun piutang pelanggan)
                JurnalEntryItem(
                    akun_perkiraan_id=pelanggan.akun_piutang_id,
                    debit=grand_total,
                    keterangan=f"SO {no_pesanan} - {pelanggan.nama}",
                ),
                # Kredit: Pendapatan Penjualan
                JurnalEntryItem(
                    akun_perkiraan_id=pelanggan.akun_piutang_id,  # TODO: ambil akun penjualan dari config
                    kredit=dasar_pajak,
                    keterangan=f"Pendapatan SO {no_pesanan}",
                ),
            ]
            # Kredit: PPN Keluaran (jika ada)
            if total_ppn > 0:
                entries.append(
                    JurnalEntryItem(
                        akun_perkiraan_id=pelanggan.akun_piutang_id,  # TODO: ambil akun PPN dari config
                        kredit=total_ppn,
                        keterangan=f"PPN SO {no_pesanan}",
                    )
                )

            try:
                jurnal = auto_posting_jurnal(
                    db=db,
                    ref_module=RefModule.SALES_ORDER,
                    ref_no=no_pesanan,
                    entries=entries,
                    keterangan=f"Sales Order {no_pesanan}",
                    ref_id=so.id,
                    tanggal=tanggal,
                    created_by=created_by,
                )
                so.jurnal_umum_id = jurnal.id
            except Exception as e:
                logger.warning(f"Jurnal SO gagal diposting (non-fatal): {e}")

        db.commit()
        db.refresh(so)
        logger.info(f"SalesOrder created: {no_pesanan} | grand_total={grand_total}")
        return so

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating SalesOrder: {e}")
        raise


def update_sales_order(
    db: Session,
    db_obj: SalesOrder,
    tanggal: Optional[datetime] = None,
    pelanggan_id: Optional[UUID] = None,
    syarat_bayar_id: Optional[UUID] = None,
    fob: Optional[str] = None,
    ekspedisi: Optional[str] = None,
    tanggal_pengiriman: Optional[datetime] = None,
    penjual: Optional[str] = None,
    alamat_pengiriman: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> SalesOrder:
    """Update data sales order (hanya field header, tidak re-calculate detail)."""
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Sales Order dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if pelanggan_id is not None:
        db_obj.pelanggan_id = pelanggan_id
    if syarat_bayar_id is not None:
        db_obj.syarat_bayar_id = syarat_bayar_id
    if fob is not None:
        db_obj.fob = fob
    if ekspedisi is not None:
        db_obj.ekspedisi = ekspedisi
    if tanggal_pengiriman is not None:
        db_obj.tanggal_pengiriman = tanggal_pengiriman
    if penjual is not None:
        db_obj.penjual = penjual
    if alamat_pengiriman is not None:
        db_obj.alamat_pengiriman = alamat_pengiriman
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


def cancel_sales_order(db: Session, db_obj: SalesOrder) -> SalesOrder:
    """Batalkan sales order."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Sales Order sudah dibatalkan")
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"SalesOrder cancelled: {db_obj.no_pesanan}")
    return db_obj


# ==========================================
# SALES INVOICE
# ==========================================

def get_sales_invoice_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[SalesInvoice], int]:
    """Ambil daftar sales invoice dengan filter & pagination."""
    query = db.query(SalesInvoice).options(
        joinedload(SalesInvoice.pelanggan),
        joinedload(SalesInvoice.syarat_bayar),
        joinedload(SalesInvoice.sales_order),
        joinedload(SalesInvoice.creator),
        joinedload(SalesInvoice.details).joinedload(SalesInvoiceDetail.barang),
        joinedload(SalesInvoice.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            SalesInvoice.no_invoice.ilike(pattern)
            | SalesInvoice.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(SalesInvoice.status == status)
    if pelanggan_id:
        query = query.filter(SalesInvoice.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(SalesInvoice.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(SalesInvoice.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(SalesInvoice.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_sales_invoice_by_id(db: Session, inv_id: UUID) -> Optional[SalesInvoice]:
    """Ambil 1 sales invoice berdasarkan ID dengan detail."""
    return (
        db.query(SalesInvoice)
        .options(
            joinedload(SalesInvoice.pelanggan),
            joinedload(SalesInvoice.syarat_bayar),
            joinedload(SalesInvoice.sales_order),
            joinedload(SalesInvoice.creator),
            joinedload(SalesInvoice.jurnal),
            joinedload(SalesInvoice.details).joinedload(SalesInvoiceDetail.barang),
            joinedload(SalesInvoice.biaya_tambahan),
        )
        .filter(SalesInvoice.id == inv_id)
        .first()
    )


def create_sales_invoice(
    db: Session,
    tanggal: datetime,
    pelanggan_id: UUID,
    details_data: list,
    biaya_data: Optional[list] = None,
    syarat_bayar_id: Optional[UUID] = None,
    sales_order_id: Optional[UUID] = None,
    fob: Optional[str] = None,
    ekspedisi: Optional[str] = None,
    tanggal_pengiriman: Optional[datetime] = None,
    alamat_pengiriman: Optional[str] = None,
    mata_uang: str = "IDR",
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
) -> SalesInvoice:
    """Buat SalesInvoice baru beserta detail + biaya tambahan."""
    try:
        biaya_data = biaya_data or []

        # Validasi pelanggan
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # Hitung sub_total dan total_diskon dari detail
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

        total_biaya_tambahan = _hitung_total_biaya(biaya_data)
        total_ppn, grand_total = _hitung_grand_total(
            sub_total, total_diskon, ppn, total_biaya_tambahan
        )

        # Generate nomor invoice
        no_invoice = get_nomor_dokumen(
            db, SalesInvoice, prefix="INV",
            no_column="no_invoice", tanggal=tanggal.date()
        )

        # Buat header
        inv = SalesInvoice(
            no_invoice=no_invoice,
            tanggal=tanggal,
            pelanggan_id=pelanggan_id,
            syarat_bayar_id=syarat_bayar_id,
            sales_order_id=sales_order_id,
            fob=fob,
            ekspedisi=ekspedisi,
            tanggal_pengiriman=tanggal_pengiriman,
            alamat_pengiriman=alamat_pengiriman,
            mata_uang=mata_uang,
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
            detail = SalesInvoiceDetail(
                sales_invoice_id=inv.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=Decimal(str(d.get("diskon", 0))),
                sub_total=Decimal(str(d["sub_total"])),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, inv, biaya_data, "sales_invoice_id")

        # Auto-post jurnal (piutang dagang D, pendapatan penjualan K)
        if auto_post_jurnal and grand_total > 0:
            dasar_pajak = sub_total - total_diskon
            entries = [
                JurnalEntryItem(
                    akun_perkiraan_id=pelanggan.akun_piutang_id,
                    debit=grand_total,
                    keterangan=f"INV {no_invoice} - {pelanggan.nama}",
                ),
                JurnalEntryItem(
                    akun_perkiraan_id=pelanggan.akun_piutang_id,  # TODO: akun penjualan dari config
                    kredit=dasar_pajak,
                    keterangan=f"Pendapatan INV {no_invoice}",
                ),
            ]
            if total_ppn > 0:
                entries.append(
                    JurnalEntryItem(
                        akun_perkiraan_id=pelanggan.akun_piutang_id,  # TODO: akun PPN dari config
                        kredit=total_ppn,
                        keterangan=f"PPN INV {no_invoice}",
                    )
                )

            try:
                jurnal = auto_posting_jurnal(
                    db=db,
                    ref_module=RefModule.SALES_INVOICE,
                    ref_no=no_invoice,
                    entries=entries,
                    keterangan=f"Sales Invoice {no_invoice}",
                    ref_id=inv.id,
                    tanggal=tanggal,
                    created_by=created_by,
                )
                inv.jurnal_umum_id = jurnal.id
            except Exception as e:
                logger.warning(f"Jurnal INV gagal diposting (non-fatal): {e}")

        db.commit()
        db.refresh(inv)
        logger.info(f"SalesInvoice created: {no_invoice} | grand_total={grand_total}")
        return inv

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating SalesInvoice: {e}")
        raise


def update_sales_invoice(
    db: Session,
    db_obj: SalesInvoice,
    tanggal: Optional[datetime] = None,
    pelanggan_id: Optional[UUID] = None,
    syarat_bayar_id: Optional[UUID] = None,
    sales_order_id: Optional[UUID] = None,
    fob: Optional[str] = None,
    ekspedisi: Optional[str] = None,
    tanggal_pengiriman: Optional[datetime] = None,
    alamat_pengiriman: Optional[str] = None,
    mata_uang: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> SalesInvoice:
    """Update data sales invoice (hanya field header)."""
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Sales Invoice dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if pelanggan_id is not None:
        db_obj.pelanggan_id = pelanggan_id
    if syarat_bayar_id is not None:
        db_obj.syarat_bayar_id = syarat_bayar_id
    if sales_order_id is not None:
        db_obj.sales_order_id = sales_order_id
    if fob is not None:
        db_obj.fob = fob
    if ekspedisi is not None:
        db_obj.ekspedisi = ekspedisi
    if tanggal_pengiriman is not None:
        db_obj.tanggal_pengiriman = tanggal_pengiriman
    if alamat_pengiriman is not None:
        db_obj.alamat_pengiriman = alamat_pengiriman
    if mata_uang is not None:
        db_obj.mata_uang = mata_uang
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


def cancel_sales_invoice(db: Session, db_obj: SalesInvoice) -> SalesInvoice:
    """Batalkan sales invoice."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Sales Invoice sudah dibatalkan")
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"SalesInvoice cancelled: {db_obj.no_invoice}")
    return db_obj


# ==========================================
# SALES RETUR
# ==========================================

def get_sales_retur_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[SalesRetur], int]:
    """Ambil daftar sales retur dengan filter & pagination."""
    query = db.query(SalesRetur).options(
        joinedload(SalesRetur.sales_invoice),
        joinedload(SalesRetur.pelanggan),
        joinedload(SalesRetur.creator),
        joinedload(SalesRetur.details).joinedload(SalesReturDetail.barang),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            SalesRetur.no_retur.ilike(pattern)
            | SalesRetur.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(SalesRetur.status == status)
    if pelanggan_id:
        query = query.filter(SalesRetur.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(SalesRetur.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(SalesRetur.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(SalesRetur.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_sales_retur_by_id(db: Session, retur_id: UUID) -> Optional[SalesRetur]:
    """Ambil 1 sales retur berdasarkan ID dengan detail."""
    return (
        db.query(SalesRetur)
        .options(
            joinedload(SalesRetur.sales_invoice),
            joinedload(SalesRetur.pelanggan),
            joinedload(SalesRetur.creator),
            joinedload(SalesRetur.jurnal),
            joinedload(SalesRetur.details).joinedload(SalesReturDetail.barang),
        )
        .filter(SalesRetur.id == retur_id)
        .first()
    )


def create_sales_retur(
    db: Session,
    tanggal: datetime,
    sales_invoice_id: UUID,
    pelanggan_id: UUID,
    details_data: list,
    alamat_pengembalian: Optional[str] = None,
    no_pengembalian: Optional[str] = None,
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
) -> SalesRetur:
    """Buat SalesRetur baru beserta detail.
    Jurnal: D - Retur Penjualan / HPP, K - Piutang Dagang
    """
    try:
        # Validasi pelanggan
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # Hitung sub_total dari detail
        sub_total = Decimal("0")
        for d in details_data:
            harga = Decimal(str(d.get("harga", 0)))
            qty = int(d.get("qty", 0))
            line_total = harga * qty
            sub_total += line_total
            d["sub_total"] = line_total

        total_ppn, grand_total = _hitung_grand_total(
            sub_total, Decimal("0"), ppn, Decimal("0")
        )

        # Generate nomor retur
        no_retur = get_nomor_dokumen(
            db, SalesRetur, prefix="RET-J",
            no_column="no_retur", tanggal=tanggal.date()
        )

        # Buat header
        retur = SalesRetur(
            no_retur=no_retur,
            tanggal=tanggal,
            sales_invoice_id=sales_invoice_id,
            pelanggan_id=pelanggan_id,
            alamat_pengembalian=alamat_pengembalian,
            no_pengembalian=no_pengembalian,
            diskon_global=diskon_global,
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
            detail = SalesReturDetail(
                sales_retur_id=retur.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                sub_total=Decimal(str(d["sub_total"])),
            )
            db.add(detail)

        # Auto-post jurnal (D: Retur Penjualan, K: Piutang Dagang)
        if auto_post_jurnal and grand_total > 0:
            entries = [
                # Debit: Retur Penjualan
                JurnalEntryItem(
                    akun_perkiraan_id=pelanggan.akun_piutang_id,  # TODO: akun retur penjualan dari config
                    debit=sub_total,
                    keterangan=f"Retur Penjualan {no_retur}",
                ),
                # Kredit: Piutang Dagang
                JurnalEntryItem(
                    akun_perkiraan_id=pelanggan.akun_piutang_id,
                    kredit=grand_total,
                    keterangan=f"Kurangi piutang {no_retur}",
                ),
            ]
            if total_ppn > 0:
                entries.append(
                    JurnalEntryItem(
                        akun_perkiraan_id=pelanggan.akun_piutang_id,  # TODO: akun PPN dari config
                        kredit=total_ppn,
                        keterangan=f"PPN Retur {no_retur}",
                    )
                )

            try:
                jurnal = auto_posting_jurnal(
                    db=db,
                    ref_module=RefModule.SALES_RETUR,
                    ref_no=no_retur,
                    entries=entries,
                    keterangan=f"Sales Retur {no_retur}",
                    ref_id=retur.id,
                    tanggal=tanggal,
                    created_by=created_by,
                )
                retur.jurnal_umum_id = jurnal.id
            except Exception as e:
                logger.warning(f"Jurnal Retur gagal diposting (non-fatal): {e}")

        db.commit()
        db.refresh(retur)
        logger.info(f"SalesRetur created: {no_retur} | grand_total={grand_total}")
        return retur

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating SalesRetur: {e}")
        raise


def update_sales_retur(
    db: Session,
    db_obj: SalesRetur,
    tanggal: Optional[datetime] = None,
    sales_invoice_id: Optional[UUID] = None,
    pelanggan_id: Optional[UUID] = None,
    alamat_pengembalian: Optional[str] = None,
    no_pengembalian: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> SalesRetur:
    """Update data sales retur (hanya field header)."""
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Sales Retur dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if sales_invoice_id is not None:
        db_obj.sales_invoice_id = sales_invoice_id
    if pelanggan_id is not None:
        db_obj.pelanggan_id = pelanggan_id
    if alamat_pengembalian is not None:
        db_obj.alamat_pengembalian = alamat_pengembalian
    if no_pengembalian is not None:
        db_obj.no_pengembalian = no_pengembalian
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


def cancel_sales_retur(db: Session, db_obj: SalesRetur) -> SalesRetur:
    """Batalkan sales retur."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Sales Retur sudah dibatalkan")
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"SalesRetur cancelled: {db_obj.no_retur}")
    return db_obj


# ==========================================
# PENGIRIMAN BARANG
# ==========================================

def get_pengiriman_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PengirimanBarang], int]:
    """Ambil daftar pengiriman barang dengan filter & pagination."""
    query = db.query(PengirimanBarang).options(
        joinedload(PengirimanBarang.sales_order),
        joinedload(PengirimanBarang.pelanggan),
        joinedload(PengirimanBarang.creator),
        joinedload(PengirimanBarang.details).joinedload(PengirimanBarangDetail.barang),
        joinedload(PengirimanBarang.details).joinedload(PengirimanBarangDetail.satuan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PengirimanBarang.no_surat_jalan.ilike(pattern)
            | PengirimanBarang.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PengirimanBarang.status == status)
    if pelanggan_id:
        query = query.filter(PengirimanBarang.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(PengirimanBarang.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PengirimanBarang.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PengirimanBarang.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_pengiriman_by_id(db: Session, pengiriman_id: UUID) -> Optional[PengirimanBarang]:
    """Ambil 1 pengiriman berdasarkan ID dengan detail."""
    return (
        db.query(PengirimanBarang)
        .options(
            joinedload(PengirimanBarang.sales_order),
            joinedload(PengirimanBarang.pelanggan),
            joinedload(PengirimanBarang.creator),
            joinedload(PengirimanBarang.details).joinedload(PengirimanBarangDetail.barang),
            joinedload(PengirimanBarang.details).joinedload(PengirimanBarangDetail.satuan),
        )
        .filter(PengirimanBarang.id == pengiriman_id)
        .first()
    )


def create_pengiriman(
    db: Session,
    tanggal: datetime,
    sales_order_id: UUID,
    pelanggan_id: UUID,
    details_data: list,
    ekspedisi: Optional[str] = None,
    alamat_pengiriman: Optional[str] = None,
    keterangan: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> PengirimanBarang:
    """Buat PengirimanBarang baru beserta detail.
    Tidak ada jurnal posting (pengiriman tidak mengubah keuangan langsung).
    """
    try:
        # Validasi pelanggan
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # Generate nomor surat jalan
        no_surat_jalan = get_nomor_dokumen(
            db, PengirimanBarang, prefix="KB",
            no_column="no_surat_jalan", tanggal=tanggal.date()
        )

        # Buat header
        pengiriman = PengirimanBarang(
            no_surat_jalan=no_surat_jalan,
            tanggal=tanggal,
            sales_order_id=sales_order_id,
            pelanggan_id=pelanggan_id,
            ekspedisi=ekspedisi,
            alamat_pengiriman=alamat_pengiriman,
            keterangan=keterangan,
            status=StatusPenjualan.DIPROSES,
            created_by=created_by,
        )
        db.add(pengiriman)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PengirimanBarangDetail(
                pengiriman_id=pengiriman.id,
                barang_id=d["barang_id"],
                qty=int(d["qty"]),
                satuan_id=d["satuan_id"],
            )
            db.add(detail)

        db.commit()
        db.refresh(pengiriman)
        logger.info(f"PengirimanBarang created: {no_surat_jalan}")
        return pengiriman

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PengirimanBarang: {e}")
        raise


def update_pengiriman(
    db: Session,
    db_obj: PengirimanBarang,
    tanggal: Optional[datetime] = None,
    sales_order_id: Optional[UUID] = None,
    pelanggan_id: Optional[UUID] = None,
    ekspedisi: Optional[str] = None,
    alamat_pengiriman: Optional[str] = None,
    keterangan: Optional[str] = None,
) -> PengirimanBarang:
    """Update data pengiriman (hanya field header)."""
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Pengiriman dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if sales_order_id is not None:
        db_obj.sales_order_id = sales_order_id
    if pelanggan_id is not None:
        db_obj.pelanggan_id = pelanggan_id
    if ekspedisi is not None:
        db_obj.ekspedisi = ekspedisi
    if alamat_pengiriman is not None:
        db_obj.alamat_pengiriman = alamat_pengiriman
    if keterangan is not None:
        db_obj.keterangan = keterangan

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def cancel_pengiriman(db: Session, db_obj: PengirimanBarang) -> PengirimanBarang:
    """Batalkan pengiriman."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Pengiriman sudah dibatalkan")
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PengirimanBarang cancelled: {db_obj.no_surat_jalan}")
    return db_obj
