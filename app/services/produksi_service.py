"""Service modul Produksi — Request Order Part (pre-PO dari Engineering).

Dokumen ringan non-finansial (tidak posting jurnal / tidak menggerakkan stok).
Workflow (update ASAHI #003): form diisi → Simpan (DRAFT) → tombol "Buat ROP"
(aksi buat = final, status DIBUAT, tidak bisa diedit lagi; menggantikan
konsep persetujuan). Cancel/Hapus = hard delete + snapshot ke
deleted_document_log (konvensi ERP Asahi).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Tuple, Any, Dict
from uuid import UUID

from loguru import logger
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.transaksi.penjualan.sales_order import SalesOrder
from app.models.transaksi.produksi.request_order_part import (
    RequestOrderPart, RequestOrderPartItem, StatusRequestOrderPart,
)
from app.models.transaksi.deleted_document_log import DeletedDocumentLog
from app.services.accounting_control import atomic_accounting_write
from app.utils.nomor_dokumen import get_nomor_dokumen_tahunan


# ==========================================
# HELPER
# ==========================================
def _resolve_so_pelanggan(db: Session, sales_order_id: UUID) -> SalesOrder:
    """Pastikan SO ada dan punya pelanggan (customer ROP ditarik dari SO)."""
    so = db.query(SalesOrder).filter(SalesOrder.id == sales_order_id).first()
    if not so:
        raise ValueError("Sales Order tidak ditemukan")
    if not so.pelanggan_id:
        raise ValueError("Sales Order tidak memiliki pelanggan (customer tidak bisa ditarik)")
    return so


def _validate_items(items: Optional[List[Dict[str, Any]]]) -> None:
    """Validasi ringan daftar item sebelum disimpan."""
    if items is None:
        return
    for idx, it in enumerate(items, start=1):
        qty = it.get("qty")
        if qty is not None and Decimal(str(qty)) < 0:
            raise ValueError(f"Qty baris {idx} tidak boleh negatif")
        # QS kini bertipe data seperti ID PART (kode) — update #003, cukup
        # batasi panjangnya.
        qs = it.get("qs")
        if qs is not None and len(str(qs)) > 100:
            raise ValueError(f"QS baris {idx} maksimal 100 karakter")


def _replace_items(db: Session, rop: RequestOrderPart, items: List[Dict[str, Any]]) -> None:
    """Ganti seluruh baris item (renumber urut mulai 1)."""
    _validate_items(items)
    rop.items = []  # cascade delete-orphan membersihkan baris lama
    db.flush()
    for idx, it in enumerate(items, start=1):
        rop.items.append(RequestOrderPartItem(
            no=idx,
            id_part=(it.get("id_part") or None),
            description=(it.get("description") or None),
            material=(it.get("material") or None),
            qty=Decimal(str(it.get("qty") or 0)),
            qs=(it.get("qs") or None),
            supplier_id=it.get("supplier_id"),
            keterangan_order=(it.get("keterangan_order") or None),
            keterangan_stok=(it.get("keterangan_stok") or None),
        ))


def _serialize_item(item: RequestOrderPartItem) -> Dict[str, Any]:
    return {
        "no": item.no,
        "id_part": item.id_part,
        "description": item.description,
        "material": item.material,
        "qty": str(item.qty) if item.qty is not None else None,
        "qs": item.qs,
        "supplier_id": str(item.supplier_id) if item.supplier_id else None,
        "keterangan_order": item.keterangan_order,
        "keterangan_stok": item.keterangan_stok,
    }


def _build_snapshot(rop: RequestOrderPart) -> Dict[str, Any]:
    """Snapshot JSON untuk deleted_document_log (jejak audit hard delete)."""
    return {
        "no_rop": rop.no_rop,
        "tanggal": rop.tanggal.isoformat() if rop.tanggal else None,
        "project_name": rop.project_name,
        "model": rop.model,
        "sales_order_id": str(rop.sales_order_id),
        "pelanggan_id": str(rop.pelanggan_id),
        "catatan": rop.catatan,
        "dibuat_oleh": rop.dibuat_oleh,
        "diperiksa_oleh": rop.diperiksa_oleh,
        "diketahui_oleh": rop.diketahui_oleh,
        "disetujui_oleh": rop.disetujui_oleh,
        "status": getattr(rop.status, "value", str(rop.status)),
        "created_by": str(rop.created_by),
        "items": [_serialize_item(i) for i in rop.items],
    }


# ==========================================
# QUERY
# ==========================================
def get_request_order_part_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sales_order_id: Optional[UUID] = None,
    tanggal_from: Optional[datetime] = None,
    tanggal_to: Optional[datetime] = None,
) -> Tuple[List[RequestOrderPart], int]:
    query = (
        db.query(RequestOrderPart)
        .join(SalesOrder, RequestOrderPart.sales_order_id == SalesOrder.id)
        .join(Pelanggan, RequestOrderPart.pelanggan_id == Pelanggan.id)
        .options(
            joinedload(RequestOrderPart.sales_order),
            joinedload(RequestOrderPart.pelanggan),
            joinedload(RequestOrderPart.creator),
        )
    )
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                RequestOrderPart.no_rop.ilike(like),
                RequestOrderPart.project_name.ilike(like),
                RequestOrderPart.model.ilike(like),
                SalesOrder.no_pesanan.ilike(like),
                Pelanggan.nama.ilike(like),
            )
        )
    if status:
        query = query.filter(RequestOrderPart.status == StatusRequestOrderPart(status))
    if sales_order_id:
        query = query.filter(RequestOrderPart.sales_order_id == sales_order_id)
    if tanggal_from:
        query = query.filter(RequestOrderPart.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(RequestOrderPart.tanggal <= tanggal_to)

    total = query.distinct().count()
    rows = (
        query.order_by(RequestOrderPart.tanggal.desc(), RequestOrderPart.created_at.desc())
        .offset(skip).limit(limit).all()
    )

    # total_item via satu query agregat (hindari N+1)
    if rows:
        counts = dict(
            db.query(RequestOrderPartItem.request_order_part_id, func.count())
            .filter(RequestOrderPartItem.request_order_part_id.in_([r.id for r in rows]))
            .group_by(RequestOrderPartItem.request_order_part_id)
            .all()
        )
        for r in rows:
            r.total_item = counts.get(r.id, 0)  # attr transien untuk response
    return rows, total


def get_request_order_part_by_id(db: Session, rop_id: UUID) -> Optional[RequestOrderPart]:
    return (
        db.query(RequestOrderPart)
        .options(
            joinedload(RequestOrderPart.items).joinedload(RequestOrderPartItem.supplier),
            joinedload(RequestOrderPart.sales_order),
            joinedload(RequestOrderPart.pelanggan),
            joinedload(RequestOrderPart.creator),
        )
        .filter(RequestOrderPart.id == rop_id)
        .first()
    )


# ==========================================
# WRITE
# ==========================================
@atomic_accounting_write
def create_request_order_part(
    db: Session,
    tanggal: datetime,
    project_name: str,
    sales_order_id: UUID,
    items: List[Dict[str, Any]],
    created_by: UUID,
    model: Optional[str] = None,
    catatan: Optional[str] = None,
    dibuat_oleh: Optional[str] = None,
    diperiksa_oleh: Optional[str] = None,
    diketahui_oleh: Optional[str] = None,
    disetujui_oleh: Optional[str] = None,
) -> RequestOrderPart:
    """Buat ROP baru — no_rop otomatis "ROP ASI/YYYY/NNN" (sequence tahunan).

    Customer (pelanggan_id) ditarik dari Sales Order yang dipilih.
    """
    if not project_name or not project_name.strip():
        raise ValueError("Project Name wajib diisi")
    so = _resolve_so_pelanggan(db, sales_order_id)
    _validate_items(items)
    if not items:
        raise ValueError("Minimal 1 baris part diperlukan")

    no_rop = get_nomor_dokumen_tahunan(
        db, RequestOrderPart, prefix="ROP", no_column="no_rop", tanggal=tanggal
    )

    rop = RequestOrderPart(
        no_rop=no_rop,
        tanggal=tanggal,
        project_name=project_name.strip(),
        model=(model or None),
        sales_order_id=so.id,
        pelanggan_id=so.pelanggan_id,
        catatan=catatan,
        dibuat_oleh=dibuat_oleh,
        diperiksa_oleh=diperiksa_oleh,
        diketahui_oleh=diketahui_oleh,
        disetujui_oleh=disetujui_oleh,
        status=StatusRequestOrderPart.DRAFT,
        created_by=created_by,
    )
    db.add(rop)
    db.flush()
    for idx, it in enumerate(items, start=1):
        rop.items.append(RequestOrderPartItem(
            no=idx,
            id_part=(it.get("id_part") or None),
            description=(it.get("description") or None),
            material=(it.get("material") or None),
            qty=Decimal(str(it.get("qty") or 0)),
            qs=(it.get("qs") or None),
            supplier_id=it.get("supplier_id"),
            keterangan_order=(it.get("keterangan_order") or None),
            keterangan_stok=(it.get("keterangan_stok") or None),
        ))
    db.commit()
    db.refresh(rop)
    logger.info(f"ROP dibuat: {no_rop} (project={project_name}, SO={so.no_pesanan})")
    return rop


@atomic_accounting_write
def update_request_order_part(
    db: Session,
    db_obj: RequestOrderPart,
    tanggal: Optional[datetime] = None,
    project_name: Optional[str] = None,
    model: Optional[str] = None,
    sales_order_id: Optional[UUID] = None,
    catatan: Optional[str] = None,
    dibuat_oleh: Optional[str] = None,
    diperiksa_oleh: Optional[str] = None,
    diketahui_oleh: Optional[str] = None,
    disetujui_oleh: Optional[str] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    **kwargs,
) -> RequestOrderPart:
    """Update ROP — hanya status DRAFT (guard final via require_unposted).

    Bila sales_order_id diganti, pelanggan_id ditarik ulang dari SO baru.
    Bila `items` dikirim, seluruh baris diganti (renumber mulai 1).
    Field signature (dibuat_oleh dst.) dikirim eksplisit agar bisa di-clear
    dengan null (pola exclude_unset di endpoint meneruskan hanya yang berubah).
    """
    if project_name is not None and not project_name.strip():
        raise ValueError("Project Name tidak boleh kosong")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if project_name is not None:
        db_obj.project_name = project_name.strip()
    if model is not None:
        db_obj.model = model or None
    if sales_order_id is not None:
        so = _resolve_so_pelanggan(db, sales_order_id)
        db_obj.sales_order_id = so.id
        db_obj.pelanggan_id = so.pelanggan_id
    if catatan is not None:
        db_obj.catatan = catatan
    if dibuat_oleh is not None:
        db_obj.dibuat_oleh = dibuat_oleh or None
    if diperiksa_oleh is not None:
        db_obj.diperiksa_oleh = diperiksa_oleh or None
    if diketahui_oleh is not None:
        db_obj.diketahui_oleh = diketahui_oleh or None
    if disetujui_oleh is not None:
        db_obj.disetujui_oleh = disetujui_oleh or None

    if items is not None:
        if not items:
            raise ValueError("Minimal 1 baris part diperlukan")
        _replace_items(db, db_obj, items)

    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def buat_request_order_part(db: Session, db_obj: RequestOrderPart) -> RequestOrderPart:
    """'Buat ROP' (finalisasi): DRAFT → DIBUAT (tidak bisa diedit lagi).

    Update ASAHI #003 — menggantikan approve_request_order_part:
    workflow tanpa persetujuan; setelah form beres diisi → Simpan (DRAFT),
    lalu tombol "Buat ROP" meng-finalisasi dokumen.
    """
    current = getattr(db_obj.status, "value", db_obj.status)
    if current != "DRAFT":
        raise ValueError("Hanya ROP berstatus DRAFT yang bisa dibuat (final)")
    db_obj.status = StatusRequestOrderPart.DIBUAT
    db.commit()
    db.refresh(db_obj)
    logger.info(f"ROP dibuat (final): {db_obj.no_rop}")
    return db_obj


def cancel_request_order_part(
    db: Session,
    db_obj: RequestOrderPart,
    user,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Hapus permanen ROP + baris item (hard delete).

    Jejak lengkap disimpan ke deleted_document_log (modul Histori) —
    konvensi cancel dokumen ERP Asahi.

    CATATAN: sengaja TANPA @atomic_accounting_write — fungsi ini mengembalikan
    dict (bukan ORM) sehingga blok idempotency decorator (result.__tablename__)
    akan crash; pola yang sama dipakai hard_delete_service (lock + commit
    manual di sini).
    """
    from sqlalchemy.exc import IntegrityError

    from app.services.accounting_control import accounting_lock
    from app.services.penutupan_periode_service import validate_periode_not_closed

    # Lock baris dokumen (pola hard_delete_service)
    rop = (
        db.query(RequestOrderPart)
        .filter(RequestOrderPart.id == db_obj.id)
        .populate_existing().with_for_update().first()
    )
    if rop is None:
        raise ValueError("Request Order Part tidak ditemukan")

    accounting_lock(db)
    validate_periode_not_closed(db, rop.tanggal, context="penghapusan dokumen")

    number = rop.no_rop
    status = getattr(rop.status, "value", str(rop.status))
    snapshot = _build_snapshot(rop)

    log = DeletedDocumentLog(
        document_type="request_order_part",
        document_id=rop.id,
        document_number=number,
        document_date=rop.tanggal,
        document_status=status,
        total_amount=None,
        snapshot=snapshot,
        deleted_by=getattr(user, "id", None),
        reason=reason,
    )
    try:
        db.delete(rop)  # items ikut terhapus (cascade delete-orphan)
        db.add(log)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Dokumen direferensikan dokumen lain, tidak dapat dihapus")

    logger.info(
        f"Hard delete request_order_part {number} ({rop.id}) oleh "
        f"{getattr(user, 'id', None)} — histori disimpan di deleted_document_log"
    )
    return {
        "success": True,
        "documentType": "request_order_part",
        "documentId": str(rop.id),
        "documentNumber": number,
        "deletedAt": datetime.now(timezone.utc).isoformat(),
        "message": f"Dokumen {number} dihapus permanen. Histori tersimpan di log dokumen terhapus.",
    }
