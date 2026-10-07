"""tukar_faktur_service.py

Service layer untuk modul Tukar Faktur (proof of receipt) — Update #4.

Mirror pola penawaran_service: TANPA jurnal, TANPA pergerakan stok,
TANPA workflow approval:
- create_tukar_faktur: validasi invoice (ada & tidak DIBATALKAN), snapshot
  header (pelanggan/no_so/no_po_customer/no_surat_jalan/total) + copy
  baris detail invoice, nomor otomatis TF-YYYY-MM-NNN, status DRAFT.
- update_tukar_faktur: hanya status DRAFT; header-only (tanggal/keterangan).
- selesaikan_tukar_faktur: DRAFT -> SELESAI (manual, tanpa workflow).

Pembatalan = hard delete lewat endpoint cancel (hard_delete_service),
bukan perubahan status.
"""

from datetime import date, datetime
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.detail.pengiriman_barang_detail import PengirimanBarangDetail
from app.models.detail.sales_invoice_detail import SalesInvoiceDetail
from app.models.detail.tukar_faktur_detail import TukarFakturDetail
from app.models.master.pelanggan import Pelanggan
from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
from app.models.transaksi.penjualan.tukar_faktur import TukarFaktur
from app.services.accounting_control import atomic_accounting_write
from app.utils.nomor_dokumen import get_nomor_dokumen, get_nomor_dokumen_tahunan


def _load_invoice(db: Session, sales_invoice_id: UUID) -> SalesInvoice | None:
    """Ambil invoice + relasi yang dibutuhkan untuk snapshot Tukar Faktur."""
    return (
        db.query(SalesInvoice)
        .options(
            joinedload(SalesInvoice.sales_order),
            joinedload(SalesInvoice.details).joinedload(
                SalesInvoiceDetail.delivery_detail
            ).joinedload(PengirimanBarangDetail.pengiriman),
            joinedload(SalesInvoice.details).joinedload(
                SalesInvoiceDetail.sales_order_detail
            ),
        )
        .filter(SalesInvoice.id == sales_invoice_id)
        .first()
    )


def _snapshot_no_surat_jalan(invoice: SalesInvoice) -> str | None:
    """Distinct no_surat_jalan pengiriman yang ter-link via detail invoice (join koma)."""
    seen: list[str] = []
    for d in invoice.details:
        dd = getattr(d, "delivery_detail", None)
        peng = getattr(dd, "pengiriman", None) if dd is not None else None
        no = getattr(peng, "no_surat_jalan", None) if peng is not None else None
        if no and no not in seen:
            seen.append(no)
    return ", ".join(seen) if seen else None


def _detail_satuan_id(detail: SalesInvoiceDetail) -> UUID | None:
    """Satuan baris TF: dari delivery_detail bila ada, else sales_order_detail, else None."""
    dd = getattr(detail, "delivery_detail", None)
    if dd is not None and getattr(dd, "satuan_id", None):
        return dd.satuan_id
    sod = getattr(detail, "sales_order_detail", None)
    if sod is not None and getattr(sod, "satuan_id", None):
        return sod.satuan_id
    return None


def get_tukar_faktur_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
    status: str | None = None,
    pelanggan_id: UUID | None = None,
    tanggal_from: date | None = None,
    tanggal_to: date | None = None,
) -> tuple[list[TukarFaktur], int]:
    """Ambil daftar tukar faktur dengan filter & pagination (mirror list penawaran)."""
    query = db.query(TukarFaktur).options(
        joinedload(TukarFaktur.pelanggan),
        joinedload(TukarFaktur.sales_invoice),
        joinedload(TukarFaktur.creator),
        joinedload(TukarFaktur.details).joinedload(TukarFakturDetail.barang),
        joinedload(TukarFaktur.details).joinedload(TukarFakturDetail.satuan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.outerjoin(Pelanggan, Pelanggan.id == TukarFaktur.pelanggan_id).filter(
            TukarFaktur.no_tukar_faktur.ilike(pattern)
            | Pelanggan.nama.ilike(pattern)
        )
    if status:
        query = query.filter(TukarFaktur.status == status)
    if pelanggan_id:
        query = query.filter(TukarFaktur.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(TukarFaktur.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(TukarFaktur.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(TukarFaktur.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_tukar_faktur_by_id(db: Session, tf_id: UUID) -> TukarFaktur | None:
    """Ambil 1 tukar faktur berdasarkan ID (joinedload mirror list)."""
    return (
        db.query(TukarFaktur)
        .options(
            joinedload(TukarFaktur.pelanggan),
            joinedload(TukarFaktur.sales_invoice),
            joinedload(TukarFaktur.creator),
            joinedload(TukarFaktur.details).joinedload(TukarFakturDetail.barang),
            joinedload(TukarFaktur.details).joinedload(TukarFakturDetail.satuan),
        )
        .filter(TukarFaktur.id == tf_id)
        .first()
    )


@atomic_accounting_write
def create_tukar_faktur(
    db: Session,
    tanggal: datetime,
    sales_invoice_id: UUID,
    keterangan: str | None = None,
    created_by: UUID | None = None,
) -> TukarFaktur:
    """Buat Tukar Faktur baru dari Sales Invoice (snapshot header + copy detail).

    - Validasi: invoice harus ada & tidak berstatus DIBATALKAN.
    - Snapshot header: pelanggan_id, no_so, no_po_customer, no_surat_jalan
      (distinct pengiriman), total = grand_total invoice — immutable setelah create.
    - Detail = copy tiap baris invoice (barang_id, qty, satuan_id fallback
      delivery_detail -> sales_order_detail -> None).
    - Status DRAFT; TANPA jurnal, TANPA stok, TANPA workflow.
    """
    try:
        inv = _load_invoice(db, sales_invoice_id)
        if inv is None:
            raise ValueError("Invoice tidak ditemukan")
        inv_status = getattr(inv.status, "value", inv.status)
        if inv_status == "DIBATALKAN":
            raise ValueError("Invoice sudah dibatalkan")

        # Snapshot header dari invoice / SO sumber
        so = getattr(inv, "sales_order", None) if inv.sales_order_id else None
        no_so = getattr(so, "no_pesanan", None) if so is not None else None
        no_po_customer = getattr(so, "customer_po_number", None) if so is not None else None

        # Generate nomor tukar faktur
        # Update ASAHI: pola penomoran seragam "TF ASI/YYYY/NNN" (reset tahunan).
        no_tukar_faktur = get_nomor_dokumen_tahunan(
            db, TukarFaktur, prefix="TF",
            no_column="no_tukar_faktur", tanggal=tanggal.date(),
        )

        tf = TukarFaktur(
            no_tukar_faktur=no_tukar_faktur,
            tanggal=tanggal,
            sales_invoice_id=sales_invoice_id,
            pelanggan_id=inv.pelanggan_id,
            no_so=no_so,
            no_po_customer=no_po_customer,
            no_surat_jalan=_snapshot_no_surat_jalan(inv),
            total=inv.grand_total,
            keterangan=keterangan,
            status="DRAFT",
            created_by=created_by,
        )
        db.add(tf)
        db.flush()

        # Copy baris detail invoice
        for d in inv.details:
            db.add(TukarFakturDetail(
                tukar_faktur_id=tf.id,
                barang_id=d.barang_id,
                satuan_id=_detail_satuan_id(d),
                qty=d.qty,
            ))

        db.commit()
        db.refresh(tf)
        logger.info(
            f"TukarFaktur created: {no_tukar_faktur} | invoice_id={sales_invoice_id} "
            f"| total={tf.total}"
        )
        return tf

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating TukarFaktur: {e}")
        raise


@atomic_accounting_write
def update_tukar_faktur(
    db: Session,
    db_obj: TukarFaktur,
    tanggal: datetime | None = None,
    keterangan: str | None = None,
) -> TukarFaktur:
    """Update tukar faktur — hanya status DRAFT; header-only (mirror penawaran)."""
    if db_obj.status != "DRAFT":
        raise ValueError(f"Tukar Faktur dengan status {db_obj.status} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if keterangan is not None:
        db_obj.keterangan = keterangan

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def selesaikan_tukar_faktur(db: Session, db_obj: TukarFaktur) -> TukarFaktur:
    """Tandai tukar faktur SELESAI (manual, tanpa workflow).

    Guard: hanya DRAFT. Bila sudah SELESAI -> ValueError.
    """
    if db_obj.status == "SELESAI":
        raise ValueError("Tukar Faktur sudah selesai")
    if db_obj.status != "DRAFT":
        raise ValueError(f"Tukar Faktur dengan status {db_obj.status} tidak bisa diselesaikan")

    db_obj.status = "SELESAI"
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"TukarFaktur finished: {db_obj.no_tukar_faktur}")
    return db_obj
