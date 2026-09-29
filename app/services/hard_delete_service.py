"""Hard delete dokumen transaksi + histori snapshot — Task 27-a.

Pembatalan dokumen (semua endpoint /cancel) kini berupa HARD DELETE:
dokumen + seluruh baris anaknya dihapus permanen dari database, dan jurnal
terkait (asli + seluruh jurnal pembaliknya) ikut dihapus agar general ledger
"seolah tidak pernah terjadi". Jejak audit lengkap disimpan sebagai snapshot
JSON di tabel ``deleted_document_log`` (dapat dilihat lewat modul Histori).

Guard yang dipertahankan dari cancel_* lama (tanpa set status BATAL):
- periode ditutup -> tolak (penutupan_periode_service.validate_periode_not_closed)
- dokumen stok yang sudah mengubah stok (SELESAI/DISETUJUI) -> tolak
- invoice dengan alokasi pelunasan / retur / penerimaan aktif -> tolak
- order yang masih direferensikan dokumen lanjutan -> tolak
- transaksi aset POSTED -> hanya boleh yang terakhir; state aset dipulihkan
- IntegrityError umum saat delete -> rollback + pesan ramah
"""
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AsetTetap, PaymentAllocation, PenerimaanBarang, PengirimanBarang, PurchaseInvoice,
    PurchaseOrder, PurchaseRetur, SalesInvoice, SalesOrder, SalesRetur,
)
from app.models.organization import DocumentOrganization
from app.models.transaksi.asset_event import AssetEvent
from app.models.transaksi.deleted_document_log import DeletedDocumentLog
from app.models.transaksi.jurnal import JurnalUmum
from app.models.transaksi.workflow import DocumentWorkflow, WorkflowEvent
from app.services.accounting_control import accounting_lock
from app.services.workflow_service import MODELS, find_workflow

# Dokumen yang menggerakkan stok — tidak boleh dihapus setelah eksekusi/approval
# (pola require_no_stock_movement; retur ikut dijaga seperti cancel lama).
STOCK_DOCUMENTS = {
    'pengiriman_barang', 'penerimaan_barang', 'penyesuaian_stok',
    'pemindahan_barang', 'sales_retur', 'purchase_retur',
}

# Pola nama kolom nomor dokumen dari workflow_service.describe().
NUMBER_FIELDS = (
    'no_invoice', 'no_form', 'no_retur', 'no_bukti', 'no_transfer', 'no_pesanan',
    'no_jurnal', 'no_surat_jalan', 'no_adj', 'no_pemindahan', 'no_permintaan',
)


# ============================================================
# SERIALIZATION HELPERS
# ============================================================

def serialize_value(value):
    """Paksa nilai Python menjadi JSON-serializable (UUID/Decimal/datetime/enum)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return serialize_value(value.value)
    if isinstance(value, (list, tuple, set)):
        return [serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): serialize_value(v) for k, v in value.items()}
    return str(value)


def serialize_row(obj) -> dict | None:
    """Snapshot satu baris ORM: seluruh kolom skalar -> dict JSON-safe."""
    if obj is None:
        return None
    return {col.key: serialize_value(getattr(obj, col.key)) for col in obj.__table__.columns}


def document_number(obj) -> str:
    """Nomor dokumen — pola describe() di workflow_service (fallback str(id))."""
    for name in NUMBER_FIELDS:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value:
                return str(value)
    return str(obj.id)


def _total_amount(obj):
    """Total dokumen — pola describe(): grand_total > total_nilai > dst."""
    value = getattr(obj, 'grand_total', getattr(obj, 'total_nilai', getattr(
        obj, 'nilai_transfer', getattr(obj, 'total_debit', getattr(obj, 'total', 0)))))
    return value if isinstance(value, Decimal) else None


def _status_value(obj):
    return getattr(getattr(obj, 'status', None), 'value', getattr(obj, 'status', None))


# ============================================================
# GUARDS (tiru cancel_* lama, tanpa set status BATAL)
# ============================================================

def _apply_guards(db: Session, document_type: str, obj):
    status = _status_value(obj)

    # 1. Dokumen stok yang sudah mengubah stok -> tolak.
    if document_type in STOCK_DOCUMENTS and status in ('SELESAI', 'DISETUJUI'):
        raise ValueError(
            'Dokumen sudah mengubah stok — tidak dapat dihapus. Gunakan retur/penyesuaian stok.'
        )

    # 2. Invoice dengan referensi silang (alokasi pelunasan / retur / penerimaan).
    if document_type == 'sales_invoice':
        if db.query(PaymentAllocation.id).filter(PaymentAllocation.sales_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih memiliki alokasi pelunasan aktif. '
                'Hapus/batalkan dokumen pembayaran yang melunasinya terlebih dahulu.'
            )
        if db.query(SalesRetur.id).filter(SalesRetur.sales_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih direferensikan retur penjualan. Hapus retur tersebut terlebih dahulu.'
            )
    if document_type == 'purchase_invoice':
        if db.query(PaymentAllocation.id).filter(PaymentAllocation.purchase_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih memiliki alokasi pelunasan aktif. '
                'Hapus/batalkan dokumen pembayaran yang melunasinya terlebih dahulu.'
            )
        if db.query(PenerimaanBarang.id).filter(PenerimaanBarang.purchase_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih terhubung dengan penerimaan barang. Hapus penerimaan tersebut terlebih dahulu.'
            )
        if db.query(PurchaseRetur.id).filter(PurchaseRetur.purchase_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih direferensikan retur pembelian. Hapus retur tersebut terlebih dahulu.'
            )

    # 3. Order yang masih direferensikan dokumen lanjutan.
    if document_type == 'sales_order':
        if (db.query(PengirimanBarang.id).filter(PengirimanBarang.sales_order_id == obj.id).first()
                or db.query(SalesInvoice.id).filter(SalesInvoice.sales_order_id == obj.id).first()):
            raise ValueError(
                'Order masih direferensikan dokumen lanjutan (pengiriman/invoice). '
                'Hapus dokumen lanjutan terlebih dahulu.'
            )
    if document_type == 'purchase_order':
        if (db.query(PenerimaanBarang.id).filter(PenerimaanBarang.purchase_order_id == obj.id).first()
                or db.query(PurchaseInvoice.id).filter(PurchaseInvoice.purchase_order_id == obj.id).first()
                or db.query(PurchaseRetur.id).filter(PurchaseRetur.purchase_order_id == obj.id).first()):
            raise ValueError(
                'Order masih direferensikan dokumen lanjutan (penerimaan/invoice/retur). '
                'Hapus dokumen lanjutan terlebih dahulu.'
            )

    # 4. Transaksi aset POSTED -> hanya yang terakhir; pulihkan state aset (sebelum).
    if document_type == 'asset_event' and status == 'POSTED':
        _revert_asset_event(db, obj)


def _revert_asset_event(db: Session, obj):
    """Guard + pemulihan state aset dari snapshot `sebelum` (pola cancel_event lama)."""
    from app.models.transaksi.aset_tetap.aset_tetap import StatusAsetTetap
    asset = db.get(AsetTetap, obj.aset_id)
    if asset is None:
        return
    from app.services.asset_cycle_service import posted
    events = posted(db, asset)
    if events and events[-1].id != obj.id:
        raise ValueError('Batalkan/hapus transaksi aset berikutnya terlebih dahulu')
    if not obj.sebelum:
        return
    for key, value in obj.sebelum.items():
        if key in ('nilai_buku', 'akumulasi_penyusutan', 'nilai_sisa', 'penyusutan_per_bulan'):
            value = Decimal(value)
        elif key == 'umur_aset':
            value = int(value)
        elif key == 'status':
            value = StatusAsetTetap(value)
        elif key == 'lokasi' and value == 'None':
            value = None
        setattr(asset, key, value)


# ============================================================
# SNAPSHOT
# ============================================================

def _collect_reversal_journals(db: Session, journal_id: UUID):
    """Seluruh jurnal pembalik yang reversal_of_id-nya menunjuk journal_id (rekursif)."""
    chain, frontier, seen = [], [journal_id], {journal_id}
    while frontier:
        rows = db.query(JurnalUmum).filter(JurnalUmum.reversal_of_id.in_(frontier)).all()
        frontier = []
        for row in rows:
            if row.id not in seen:
                seen.add(row.id)
                chain.append(row)
                frontier.append(row.id)
    return chain


def _snapshot_jurnal(db: Session, journal_id: UUID):
    original = db.get(JurnalUmum, journal_id)
    if original is None:
        return None
    reversals = _collect_reversal_journals(db, journal_id)
    return {
        'header': serialize_row(original),
        'details': [serialize_row(d) for d in original.details],
        'reversals': [
            {'header': serialize_row(r), 'details': [serialize_row(d) for d in r.details]}
            for r in reversals
        ],
    }


def _build_snapshot(db: Session, document_type: str, obj) -> dict:
    snapshot = {'documentType': document_type, 'header': serialize_row(obj)}

    # Children: seluruh relationship list (rincian/alokasi/details/biaya tambahan/dokumen terkait).
    for rel in obj.__mapper__.relationships:
        if not rel.uselist or rel.key in snapshot:
            continue
        try:
            value = getattr(obj, rel.key)
        except Exception:  # pragma: no cover - relasi tak ter-load aman diabaikan
            continue
        try:
            items = list(value)
        except TypeError:
            continue
        if items and all(hasattr(item, '__table__') for item in items):
            snapshot[rel.key] = [serialize_row(item) for item in items]

    # Jurnal asli + seluruh jurnal pembaliknya.
    journal_id = obj.id if document_type == 'jurnal_umum' else getattr(obj, 'jurnal_umum_id', None)
    snapshot['jurnal'] = _snapshot_jurnal(db, journal_id) if journal_id else None

    # Workflow + events.
    wf = find_workflow(db, obj)
    snapshot['workflow'] = serialize_row(wf)
    events = []
    if wf is not None:
        events = (db.query(WorkflowEvent).filter_by(workflow_id=wf.id)
                  .order_by(WorkflowEvent.version).all())
    snapshot['events'] = [serialize_row(e) for e in events]

    # Dimensi organisasi dokumen.
    try:
        from app.services.organization_service import for_source
        snapshot['organization'] = serialize_value(for_source(db, obj.id))
    except Exception:  # pragma: no cover
        snapshot['organization'] = None

    # Paksa JSON-serializable penuh (JSONB menolak tipe Python mentah).
    return json.loads(json.dumps(snapshot, default=str))


# ============================================================
# DELETION
# ============================================================

def _delete_document(db: Session, document_type: str, obj, journal_id):
    """Hapus berurutan mengikuti FK: events -> workflow -> dokumen (+anak) -> jurnal.

    ``journal_id`` dan identitas dokumen dihitung SEBELUM delete agar tidak
    bergantung pada atribut objek yang sudah ter-flush terhapus.
    """
    doc_id = obj.id

    # 1. WorkflowEvent -> DocumentWorkflow.
    wf = find_workflow(db, obj)
    if wf is not None:
        db.query(WorkflowEvent).filter(WorkflowEvent.workflow_id == wf.id).delete(
            synchronize_session=False)
        db.delete(wf)
        db.flush()

    # 2. Dokumen + seluruh baris anaknya (ORM cascade delete-orphan).
    db.delete(obj)
    db.flush()

    # 3. Jurnal asli + seluruh jurnal pembalik (pembalik terdalam lebih dulu).
    if journal_id:
        original = db.get(JurnalUmum, journal_id)
        if original is not None:
            active_registration = db.query(AssetEvent).filter(
                AssetEvent.source_journal_id == journal_id,
                AssetEvent.status != 'BATAL',
                AssetEvent.id != doc_id,
            ).first()
            if active_registration:
                raise ValueError(
                    'Jurnal dokumen digunakan oleh registrasi aset. '
                    'Hapus transaksi aset terkait terlebih dahulu.'
                )
            for reversal in reversed(_collect_reversal_journals(db, journal_id)):
                db.delete(reversal)
                db.flush()
            db.delete(original)
            db.flush()

    # 4. Dimensi organisasi dokumen (tanpa FK — dibersihkan agar tidak yatim).
    db.query(DocumentOrganization).filter_by(document_id=doc_id).delete(
        synchronize_session=False)
    db.flush()


# ============================================================
# PUBLIC API
# ============================================================

def hard_delete_document(db: Session, document_type: str, document_id: UUID,
                         user, reason: str | None = None) -> dict:
    """Hapus permanen dokumen transaksi + seluruh data turunannya.

    Jejak lengkap disimpan ke deleted_document_log (snapshot JSON).
    Dokumen berstatus BATAL boleh dihapus — justru untuk membersihkan dokumen batal.
    """
    model = MODELS.get(document_type)
    if model is None:
        raise HTTPException(404, 'Jenis dokumen tidak didukung')

    obj = (db.query(model).filter(model.id == document_id)
           .populate_existing().with_for_update().first())
    if obj is None:
        raise HTTPException(404, 'Dokumen tidak ditemukan')

    accounting_lock(db)

    # Guard periode (sama dengan cancel/post lama).
    from app.services.penutupan_periode_service import validate_periode_not_closed
    validate_periode_not_closed(db, obj.tanggal, context='penghapusan dokumen')

    _apply_guards(db, document_type, obj)

    number = document_number(obj)
    status = _status_value(obj)
    snapshot = _build_snapshot(db, document_type, obj)
    journal_id = obj.id if document_type == 'jurnal_umum' else getattr(obj, 'jurnal_umum_id', None)

    log = DeletedDocumentLog(
        document_type=document_type,
        document_id=obj.id,
        document_number=number,
        document_date=obj.tanggal,
        document_status=str(status) if status is not None else None,
        total_amount=_total_amount(obj),
        snapshot=snapshot,
        deleted_by=getattr(user, 'id', None),
        reason=reason,
    )

    try:
        _delete_document(db, document_type, obj, journal_id)
        db.add(log)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError('Dokumen direferensikan dokumen lain, tidak dapat dihapus')

    logger.info(
        f"Hard delete {document_type} {number} ({document_id}) oleh "
        f"{getattr(user, 'id', None)} — histori disimpan di deleted_document_log"
    )

    deleted_at = datetime.now(timezone.utc).isoformat()
    return {
        'success': True,
        'documentType': document_type,
        'documentId': str(document_id),
        'documentNumber': number,
        'deletedAt': deleted_at,
        'message': f'Dokumen {number} dihapus permanen. Histori tersimpan di log dokumen terhapus.',
    }
