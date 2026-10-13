"""Service modul LPPB — Laporan Pemeriksaan dan Pengujian Barang (update ASAHI #005).

Dokumen RECORD QC barang masuk dari vendor (non-finansial: tidak posting
jurnal / tidak menggerakkan stok — Penerimaan Barang tetap pencatat stok).

Workflow (mirror pola ROP #003 — tanpa upacara persetujuan):
form diisi → Simpan (DRAFT) → tombol "Selesaikan" (final, SELESAI, tidak
bisa diedit lagi). Hapus (draft) / Batalkan (final) = hard delete + snapshot
ke deleted_document_log (konvensi ERP Asahi).

Kuantitas per baris (kontrak data):
- qty_po, qty_penerimaan, qty_baik, qty_tidak_baik → dari input user
  (prefill frontend via GET /purchase-order/{id}/sisa).
- qty_sudah_diterima = akumulasi penerimaan PO (DIPROSES/SELESAI) SEBELUM
  LPPB ini + qty_penerimaan baris ini — dihitung SERVER saat simpan.
- qty_belum_diterima = max(qty_po − qty_sudah_diterima, 0).
"""

from datetime import datetime, timezone
from typing import Optional, List, Tuple, Any, Dict
from uuid import UUID

from loguru import logger
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.detail.lppb_detail import LppbDetail
from app.models.detail.penerimaan_barang_detail import PenerimaanBarangDetail
from app.models.master.supplier import Supplier
from app.models.transaksi.pembelian.lppb import Lppb
from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
from app.models.transaksi.pembelian.purchase_order import PurchaseOrder
from app.models.transaksi.penjualan.sales_order import StatusPenjualan
from app.models.transaksi.deleted_document_log import DeletedDocumentLog
from app.services.accounting_control import atomic_accounting_write
from app.utils.nomor_dokumen import get_nomor_dokumen_tahunan


# ==========================================
# HELPER
# ==========================================
def _resolve_po(db: Session, purchase_order_id: UUID) -> PurchaseOrder:
    """Pastikan PO ada (supplier LPPB ditarik otomatis dari PO)."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == purchase_order_id).first()
    if not po:
        raise ValueError("Purchase Order tidak ditemukan")
    if not po.supplier_id:
        raise ValueError("Purchase Order tidak memiliki supplier (vendor tidak bisa ditarik)")
    return po


def _received_so_far_map(db: Session, purchase_order_id: UUID) -> Dict[UUID, int]:
    """Map purchase_order_detail_id → qty sudah diterima (penerimaan DIPROSES/SELESAI).

    Batch untuk semua baris PO sekaligus (hindari N+1) — mirror logika
    purchase_validation.get_qty_received_so_far.
    """
    rows = (
        db.query(PenerimaanBarangDetail.purchase_order_detail_id, func.coalesce(func.sum(PenerimaanBarangDetail.qty), 0))
        .join(PenerimaanBarang, PenerimaanBarang.id == PenerimaanBarangDetail.penerimaan_barang_id)
        .filter(
            PenerimaanBarangDetail.purchase_order_detail_id.isnot(None),
            PenerimaanBarang.purchase_order_id == purchase_order_id,
            PenerimaanBarang.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]),
        )
        .group_by(PenerimaanBarangDetail.purchase_order_detail_id)
        .all()
    )
    return {pod_id: int(qty or 0) for pod_id, qty in rows}


def _validate_details(details: Optional[List[Dict[str, Any]]]) -> None:
    if details is None:
        return
    for idx, d in enumerate(details, start=1):
        for field in ("qty_po", "qty_penerimaan", "qty_baik", "qty_tidak_baik"):
            v = d.get(field)
            if v is not None and int(v) < 0:
                raise ValueError(f"{field} baris {idx} tidak boleh negatif")
        baik = int(d.get("qty_baik") or 0)
        tidak = int(d.get("qty_tidak_baik") or 0)
        terima = int(d.get("qty_penerimaan") or 0)
        if baik + tidak > terima:
            raise ValueError(
                f"Baris {idx}: Baik ({baik}) + Tidak Baik ({tidak}) melebihi Kuantitas Penerimaan ({terima})"
            )


def _apply_details(db: Session, lppb: Lppb, details: List[Dict[str, Any]]) -> None:
    """(Re)bangun seluruh baris detail — urutan mengikuti input (baris jasa
    pengujian NDT berpasangan di bawah barangnya diatur dari frontend),
    renumber mulai 1; hitung sudah/belum diterima server-side."""
    _validate_details(details)
    received = _received_so_far_map(db, lppb.purchase_order_id)
    lppb.details = []  # cascade delete-orphan membersihkan baris lama
    db.flush()
    for idx, d in enumerate(details, start=1):
        pod_id = d.get("purchase_order_detail_id")
        qty_po = int(d.get("qty_po") or 0)
        qty_penerimaan = int(d.get("qty_penerimaan") or 0)
        prev = received.get(pod_id, 0) if pod_id else 0
        sudah = prev + qty_penerimaan
        lppb.details.append(LppbDetail(
            urutan=idx,
            barang_id=d.get("barang_id"),
            purchase_order_detail_id=pod_id,
            kode_material=(d.get("kode_material") or None),
            deskripsi_spesifikasi=(d.get("deskripsi_spesifikasi") or "").strip(),
            satuan=(d.get("satuan") or None),
            qty_po=qty_po,
            qty_penerimaan=qty_penerimaan,
            qty_baik=int(d.get("qty_baik") or 0),
            qty_tidak_baik=int(d.get("qty_tidak_baik") or 0),
            qty_sudah_diterima=sudah,
            qty_belum_diterima=max(qty_po - sudah, 0),
        ))


def _serialize_detail(item: LppbDetail) -> Dict[str, Any]:
    return {
        "id": str(item.id),
        "urutan": item.urutan,
        "barang_id": str(item.barang_id) if item.barang_id else None,
        "purchase_order_detail_id": str(item.purchase_order_detail_id) if item.purchase_order_detail_id else None,
        "kode_material": item.kode_material,
        "deskripsi_spesifikasi": item.deskripsi_spesifikasi,
        "satuan": item.satuan,
        "qty_po": item.qty_po,
        "qty_penerimaan": item.qty_penerimaan,
        "qty_baik": item.qty_baik,
        "qty_tidak_baik": item.qty_tidak_baik,
        "qty_sudah_diterima": item.qty_sudah_diterima,
        "qty_belum_diterima": item.qty_belum_diterima,
    }


def _build_snapshot(lppb: Lppb) -> Dict[str, Any]:
    """Snapshot JSON untuk deleted_document_log (jejak audit hard delete)."""
    return {
        "no_form": lppb.no_form,
        "tanggal": lppb.tanggal.isoformat() if lppb.tanggal else None,
        "tanggal_terima": lppb.tanggal_terima.isoformat() if lppb.tanggal_terima else None,
        "purchase_order_id": str(lppb.purchase_order_id),
        "supplier_id": str(lppb.supplier_id),
        "nomor_do": lppb.nomor_do,
        "nomor_ri": lppb.nomor_ri,
        "proyek": lppb.proyek,
        "revisi": lppb.revisi,
        "no_format": lppb.no_format,
        "keterangan": lppb.keterangan,
        "mengetahui_jabatan": lppb.mengetahui_jabatan,
        "mengetahui_nama": lppb.mengetahui_nama,
        "status": getattr(lppb.status, "value", str(lppb.status)),
        "created_by": str(lppb.created_by),
        "details": [_serialize_detail(d) for d in lppb.details],
    }


# ==========================================
# QUERY
# ==========================================
def get_lppb_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    purchase_order_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[datetime] = None,
    tanggal_to: Optional[datetime] = None,
) -> Tuple[List[Lppb], int]:
    query = (
        db.query(Lppb)
        .join(PurchaseOrder, Lppb.purchase_order_id == PurchaseOrder.id)
        .join(Supplier, Lppb.supplier_id == Supplier.id)
        .options(
            joinedload(Lppb.purchase_order),
            joinedload(Lppb.supplier),
            joinedload(Lppb.creator),
        )
    )
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Lppb.no_form.ilike(like),
                Lppb.proyek.ilike(like),
                Lppb.nomor_do.ilike(like),
                Lppb.nomor_ri.ilike(like),
                PurchaseOrder.no_pesanan.ilike(like),
                Supplier.nama.ilike(like),
            )
        )
    if status:
        query = query.filter(Lppb.status == StatusPenjualan(status))
    if purchase_order_id:
        query = query.filter(Lppb.purchase_order_id == purchase_order_id)
    if supplier_id:
        query = query.filter(Lppb.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(Lppb.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(Lppb.tanggal <= tanggal_to)

    total = query.distinct().count()
    rows = (
        query.order_by(Lppb.tanggal.desc(), Lppb.created_at.desc())
        .offset(skip).limit(limit).all()
    )

    # total_item via satu query agregat (hindari N+1)
    if rows:
        counts = dict(
            db.query(LppbDetail.lppb_id, func.count())
            .filter(LppbDetail.lppb_id.in_([r.id for r in rows]))
            .group_by(LppbDetail.lppb_id)
            .all()
        )
        for r in rows:
            r.total_item = counts.get(r.id, 0)  # attr transien untuk response
    return rows, total


def get_lppb_by_id(db: Session, lppb_id: UUID) -> Optional[Lppb]:
    return (
        db.query(Lppb)
        .options(
            joinedload(Lppb.details),
            joinedload(Lppb.purchase_order),
            joinedload(Lppb.supplier),
            joinedload(Lppb.creator),
        )
        .filter(Lppb.id == lppb_id)
        .first()
    )


# ==========================================
# WRITE
# ==========================================
@atomic_accounting_write
def create_lppb(
    db: Session,
    tanggal: datetime,
    purchase_order_id: UUID,
    details: List[Dict[str, Any]],
    created_by: UUID,
    tanggal_terima: Optional[datetime] = None,
    nomor_do: Optional[str] = None,
    nomor_ri: Optional[str] = None,
    proyek: Optional[str] = None,
    revisi: Optional[str] = None,
    no_format: Optional[str] = None,
    keterangan: Optional[str] = None,
    mengetahui_jabatan: Optional[str] = None,
    mengetahui_nama: Optional[str] = None,
) -> Lppb:
    """Buat LPPB baru — no_form otomatis "LPPB ASI/YYYY/NNN" (sequence tahunan).

    Supplier (Vendor) ditarik otomatis dari Purchase Order yang dipilih.
    """
    if not details:
        raise ValueError("Minimal 1 baris pemeriksaan diperlukan")
    _validate_details(details)
    po = _resolve_po(db, purchase_order_id)

    no_form = get_nomor_dokumen_tahunan(
        db, Lppb, prefix="LPPB", no_column="no_form", tanggal=tanggal
    )

    lppb = Lppb(
        no_form=no_form,
        tanggal=tanggal,
        tanggal_terima=tanggal_terima,
        purchase_order_id=po.id,
        supplier_id=po.supplier_id,
        nomor_do=(nomor_do or None),
        nomor_ri=(nomor_ri or None),
        proyek=(proyek or None),
        revisi=(revisi or None),
        no_format=(no_format or None),
        keterangan=keterangan,
        mengetahui_jabatan=(mengetahui_jabatan or None),
        mengetahui_nama=(mengetahui_nama or None),
        status=StatusPenjualan.DRAFT,
        created_by=created_by,
    )
    db.add(lppb)
    db.flush()
    _apply_details(db, lppb, details)
    db.commit()
    db.refresh(lppb)
    logger.info(f"LPPB dibuat: {no_form} (PO={po.no_pesanan}, {len(details)} baris)")
    return lppb


@atomic_accounting_write
def update_lppb(
    db: Session,
    db_obj: Lppb,
    tanggal: Optional[datetime] = None,
    tanggal_terima: Optional[datetime] = None,
    purchase_order_id: Optional[UUID] = None,
    nomor_do: Optional[str] = None,
    nomor_ri: Optional[str] = None,
    proyek: Optional[str] = None,
    revisi: Optional[str] = None,
    no_format: Optional[str] = None,
    keterangan: Optional[str] = None,
    mengetahui_jabatan: Optional[str] = None,
    mengetahui_nama: Optional[str] = None,
    details: Optional[List[Dict[str, Any]]] = None,
    **kwargs,
) -> Lppb:
    """Update LPPB — hanya status DRAFT.

    Bila purchase_order_id diganti, supplier ditarik ulang dari PO baru
    (dan seluruh baris wajib dikirim ulang karena snapshot PO berubah).
    Field teks dikirim eksplisit agar bisa di-clear dengan null (pola
    exclude_unset di endpoint meneruskan hanya yang berubah).
    """
    current = getattr(db_obj.status, "value", db_obj.status)
    if current != "DRAFT":
        raise ValueError("Hanya LPPB berstatus DRAFT yang bisa diubah")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if tanggal_terima is not None:
        db_obj.tanggal_terima = tanggal_terima or None
    if purchase_order_id is not None and purchase_order_id != db_obj.purchase_order_id:
        po = _resolve_po(db, purchase_order_id)
        db_obj.purchase_order_id = po.id
        db_obj.supplier_id = po.supplier_id
        if details is None:
            raise ValueError("Baris pemeriksaan wajib dikirim ulang saat PO diganti")
    if nomor_do is not None:
        db_obj.nomor_do = nomor_do or None
    if nomor_ri is not None:
        db_obj.nomor_ri = nomor_ri or None
    if proyek is not None:
        db_obj.proyek = proyek or None
    if revisi is not None:
        db_obj.revisi = revisi or None
    if no_format is not None:
        db_obj.no_format = no_format or None
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if mengetahui_jabatan is not None:
        db_obj.mengetahui_jabatan = mengetahui_jabatan or None
    if mengetahui_nama is not None:
        db_obj.mengetahui_nama = mengetahui_nama or None

    if details is not None:
        if not details:
            raise ValueError("Minimal 1 baris pemeriksaan diperlukan")
        _apply_details(db, db_obj, details)

    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def selesaikan_lppb(db: Session, db_obj: Lppb) -> Lppb:
    """"Selesaikan" (finalisasi): DRAFT → SELESAI (tidak bisa diedit lagi).

    Pola ROP #003 — workflow tanpa persetujuan: setelah form beres diisi →
    Simpan (DRAFT), tombol "Selesaikan" meng-finalisasi dokumen QC.
    """
    current = getattr(db_obj.status, "value", db_obj.status)
    if current != "DRAFT":
        raise ValueError("Hanya LPPB berstatus DRAFT yang bisa diselesaikan (final)")
    db_obj.status = StatusPenjualan.SELESAI
    db.commit()
    db.refresh(db_obj)
    logger.info(f"LPPB selesai (final): {db_obj.no_form}")
    return db_obj


def cancel_lppb(
    db: Session,
    db_obj: Lppb,
    user,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Hapus permanen LPPB + baris detail (hard delete).

    Jejak lengkap disimpan ke deleted_document_log (modul Histori) —
    konvensi cancel dokumen ERP Asahi.

    CATATAN: sengaja TANPA @atomic_accounting_write — fungsi ini mengembalikan
    dict (bukan ORM) sehingga blok idempotency decorator (result.__tablename__)
    akan crash; pola yang sama dipakai hard_delete_service & cancel ROP
    (lock + commit manual di sini).
    """
    from sqlalchemy.exc import IntegrityError

    from app.services.accounting_control import accounting_lock
    from app.services.penutupan_periode_service import validate_periode_not_closed

    # Lock baris dokumen (pola hard_delete_service)
    lppb = (
        db.query(Lppb)
        .filter(Lppb.id == db_obj.id)
        .populate_existing().with_for_update().first()
    )
    if lppb is None:
        raise ValueError("LPPB tidak ditemukan")

    accounting_lock(db)
    validate_periode_not_closed(db, lppb.tanggal, context="penghapusan dokumen")

    number = lppb.no_form
    status = getattr(lppb.status, "value", str(lppb.status))
    snapshot = _build_snapshot(lppb)

    log = DeletedDocumentLog(
        document_type="lppb",
        document_id=lppb.id,
        document_number=number,
        document_date=lppb.tanggal,
        document_status=status,
        total_amount=None,
        snapshot=snapshot,
        deleted_by=getattr(user, "id", None),
        reason=reason,
    )
    try:
        db.delete(lppb)  # details ikut terhapus (cascade delete-orphan)
        db.add(log)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Dokumen direferensikan dokumen lain, tidak dapat dihapus")

    logger.info(
        f"Hard delete lppb {number} ({lppb.id}) oleh "
        f"{getattr(user, 'id', None)} — histori disimpan di deleted_document_log"
    )
    return {
        "success": True,
        "documentType": "lppb",
        "documentId": str(lppb.id),
        "documentNumber": number,
        "deletedAt": datetime.now(timezone.utc).isoformat(),
        "message": f"Dokumen {number} dihapus permanen. Histori tersimpan di log dokumen terhapus.",
    }
