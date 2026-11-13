"""AUD-01 (update #05) — pembatalan dokumen REVERSAL-FIRST.

Semua endpoint /cancel bisnis sebelumnya memakai hard_delete_service: dokumen
+ jurnal POSTED dihapus permanen (jejak hanya snapshot JSON). Perilaku baru:

- Dokumen DENGAN jejak akuntansi (punya jurnal POSTED via ref_id dan/atau
  mutasi stok aktif) → REVERSAL:
    1. guard referensi (alokasi pelunasan / retur / penerimaan /
       dokumen lanjutan / event aset bukan terakhir → tolak),
    2. hapus PaymentAllocation milik dokumen kas settlement (outstanding
       piutang/hutang pulih),
    3. reverse setiap mutasi stok dokumen (exact-layer, MASUK dulu),
    4. reverse setiap jurnal dokumen (posting_service.reverse_journal,
       idempotent via uq_jurnal_reversal, tanggal = tanggal jurnal asli),
    5. set status BATAL/DIBATALKAN + workflow event 'cancel' → CANCELLED,
    6. catat baris document_cancellation (siapa/kapan/alasan/daftar jurnal
       pembalik) — audit trail utuh, nomor dokumen tidak dipakai ulang.
- Dokumen TANPA jejak akuntansi (draft/order tanpa jurnal & mutasi) → tetap
  hard delete + snapshot deleted_document_log (perilaku lama).

Endpoint /workflow/{type}/{id}/cancel (jurnal manual), /reverse (transfer,
delivery, receipt, pemindahan), buka periode — tetap sebagaimana adanya
(sudah reversal-first).
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.orm import Session

from app.models import (
    PaymentAllocation, PenerimaanBarang, PengirimanBarang, PurchaseInvoice,
    PurchaseOrder, PurchaseRetur, SalesInvoice, SalesOrder, SalesRetur,
)
from app.models.transaksi.document_cancellation import DocumentCancellation
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal
from app.models.transaksi.stok_mutasi import StokMutasi, TipeMutasiStok
from app.models.transaksi.workflow import DocumentWorkflow
from app.services.accounting_control import atomic_accounting_write
from app.services.hard_delete_service import (
    _collect_reversal_journals, _revert_asset_event, _status_value, _total_amount,
    document_number,
)
from app.services.workflow_service import MODELS, append_event, find_workflow

# Dokumen stok yang bisa punya mutasi (dipakai menentukan urutan reverse).
_STOCK_KINDS = {
    'pengiriman_barang', 'penerimaan_barang', 'penyesuaian_stok',
    'pemindahan_barang', 'sales_retur', 'purchase_retur',
}


def _status_batal_value(obj):
    """Nilai status pembatalan yang tepat untuk enum/status kolom model."""
    current = getattr(obj, 'status', None)
    enum_cls = type(current)
    if current is None or enum_cls is str:
        return 'BATAL'
    if hasattr(enum_cls, 'DIBATALKAN'):
        return enum_cls.DIBATALKAN
    if hasattr(enum_cls, 'BATAL'):
        return enum_cls.BATAL
    raise ValueError(f"Tipe status {enum_cls.__name__} tidak mendukung pembatalan")


def _collect_journals(db: Session, obj) -> list:
    """Seluruh jurnal POSTED milik dokumen (primer + tambahan), tanpa REVERSAL."""
    ids = [j.id for j in db.query(JurnalUmum.id).filter(
        JurnalUmum.ref_id == obj.id,
        JurnalUmum.tipe_transaksi != 'REVERSAL',
        JurnalUmum.status == StatusJurnal.POSTED,
    ).all()]
    primary = getattr(obj, 'jurnal_umum_id', None)
    if primary and primary not in ids:
        row = db.get(JurnalUmum, primary)
        if row is not None and row.tipe_transaksi != 'REVERSAL':
            ids.append(primary)
    return [db.get(JurnalUmum, jid) for jid in ids]


def _collect_mutasi(db: Session, obj) -> list:
    """Seluruh mutasi stok aktif (belum di-reverse) milik dokumen."""
    return (db.query(StokMutasi)
            .filter(StokMutasi.ref_id == obj.id,
                    StokMutasi.reversal_of_id.is_(None))
            .order_by(StokMutasi.created_at)
            .all())


def _guard_cancel(db: Session, document_type: str, obj):
    """Guard referensi — dokumen yang dipakai dokumen lain TIDAK boleh dibatalkan.

    Identik dengan _apply_guards hard_delete_service KECUALI aturan penolakan
    dokumen stok SELESAI (justru kasus utama reversal-first) dan pemulihan
    state aset (dilakukan di jalur reversal, bukan guard).
    """
    # 1. Invoice dengan referensi silang (alokasi pelunasan / retur / penerimaan).
    if document_type == 'sales_invoice':
        if db.query(PaymentAllocation.id).filter(PaymentAllocation.sales_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih memiliki alokasi pelunasan aktif. '
                'Batalkan dokumen pembayaran yang melunasinya terlebih dahulu.'
            )
        if db.query(SalesRetur.id).filter(SalesRetur.sales_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih direferensikan retur penjualan. Batalkan retur tersebut terlebih dahulu.'
            )
    if document_type == 'purchase_invoice':
        if db.query(PaymentAllocation.id).filter(PaymentAllocation.purchase_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih memiliki alokasi pelunasan aktif. '
                'Batalkan dokumen pembayaran yang melunasinya terlebih dahulu.'
            )
        if db.query(PenerimaanBarang.id).filter(PenerimaanBarang.purchase_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih terhubung dengan penerimaan barang. Batalkan penerimaan tersebut terlebih dahulu.'
            )
        if db.query(PurchaseRetur.id).filter(PurchaseRetur.purchase_invoice_id == obj.id).first():
            raise ValueError(
                'Invoice masih direferensikan retur pembelian. Batalkan retur tersebut terlebih dahulu.'
            )

    # 2. Order yang masih direferensikan dokumen lanjutan.
    if document_type == 'sales_order':
        if (db.query(PengirimanBarang.id).filter(PengirimanBarang.sales_order_id == obj.id).first()
                or db.query(SalesInvoice.id).filter(SalesInvoice.sales_order_id == obj.id).first()):
            raise ValueError(
                'Order masih direferensikan dokumen lanjutan (pengiriman/invoice). '
                'Batalkan dokumen lanjutan terlebih dahulu.'
            )
    if document_type == 'purchase_order':
        if (db.query(PenerimaanBarang.id).filter(PenerimaanBarang.purchase_order_id == obj.id).first()
                or db.query(PurchaseInvoice.id).filter(PurchaseInvoice.purchase_order_id == obj.id).first()
                or db.query(PurchaseRetur.id).filter(PurchaseRetur.purchase_order_id == obj.id).first()):
            raise ValueError(
                'Order masih direferensikan dokumen lanjutan (penerimaan/invoice/retur). '
                'Batalkan dokumen lanjutan terlebih dahulu.'
            )


def _reverse_stock(db: Session, obj, user_id: UUID, reason: str, mutasi_list: list):
    """Reverse seluruh mutasi stok dokumen — MASUK-type lebih dulu (pola reverse_pemindahan)."""
    from app.services.inventory_reversal_service import reverse_stock_movement

    def order_key(m):
        tipe = m.tipe
        if tipe in (TipeMutasiStok.MASUK, TipeMutasiStok.PENYESUAIAN_TAMBAH,
                    TipeMutasiStok.PEMINDAHAN_MASUK):
            return 0
        return 1

    for m in sorted(mutasi_list, key=order_key):
        reverse_stock_movement(
            db, m.id, user_id,
            reason=f"Pembatalan {document_number(obj)}: {reason}",
        )


# ==========================================
# === Update #042 — CASCADE pembatalan invoice → SO sumber + sibling (G) ===
# ==========================================
def _cascade_cancel_sales_invoice_so(
    db: Session,
    invoice_id: UUID,
    invoice_number: str,
    so_id: UUID,
    user,
    reason: Optional[str],
):
    """Cascade pembatalan invoice: SO sumber ikut DIBATALKAN + invoice lain
    milik SO yang BELUM BAYAR ikut dibatalkan.

    Dipanggil dari _cancel_document_tx SETELAH dokumen utama (invoice) selesai
    dibatalkan/dihapus:
    1. SO sumber dibatalkan DULU (urutan penting — lihat bawah) + workflow
       event 'cancel' + baris audit DocumentCancellation mode REVERSAL
       (reversal list kosong: SO memang tidak pernah posting jurnal).
    2. Invoice lain (sibling) milik SO yang belum bayar — tanpa
       PaymentAllocation dan tanpa retur — dibatalkan satu per satu dengan
       mengulangi proses pembatalan (reversal jurnal masing-masing); yang
       sudah bayar/ber-retur DIBIARKAN; yang sudah DIBATALKAN dilewati.

    Idempotent & anti-loop: SO yang sudah DIBATALKAN → cascade dihentikan
    (invoice dibatalkan dari SO yang sudah batal tidak memicu apa pun);
    SO di-set DIBATALKAN DULU sebelum sibling diproses supaya cascade dari
    jalur sibling tidak memicu ulang (guard pertama langsung return).
    Panggilan rekursif _cancel_document_tx aman karena depth-guard dekorator
    atomic_accounting_write (db.info['accounting_depth']) — fungsi mentah
    dijalankan tanpa commit di tengah transaksi.
    """
    so = db.get(SalesOrder, so_id)
    if so is None:
        return None
    if _status_value(so) in ('BATAL', 'DIBATALKAN'):
        # Guard idempotent/anti-loop: cascade hanya berjalan bila SO belum
        # dibatalkan (cascade sibling tidak memicu ulang jalur ini).
        return None

    cascade_reason = f'Cascade pembatalan invoice {invoice_number}'
    if reason and str(reason).strip():
        cascade_reason += f': {reason}'

    # 1. SO sumber → DIBATALKAN + workflow event + audit row.
    batal = _status_batal_value(so)
    so.status = batal
    wf = find_workflow(db, so)
    if wf is None:
        wf = DocumentWorkflow(
            document_type='sales_order', document_id=so.id,
            state='CANCELLED', version=0,
        )
        db.add(wf)
        db.flush()
    append_event(db, wf, 'cancel', 'CANCELLED', user.id, cascade_reason)
    db.add(DocumentCancellation(
        document_type='sales_order',
        document_id=so.id,
        document_number=document_number(so),
        document_date=so.tanggal,
        document_status=str(getattr(batal, 'value', batal)),
        total_amount=_total_amount(so),
        reason=cascade_reason,
        mode='REVERSAL',
        # SO tidak pernah posting jurnal → daftar jurnal pembalik kosong.
        reversal_jurnal_ids=[],
        cancelled_by=user.id,
        cancelled_at=datetime.now(timezone.utc),
    ))
    db.flush()

    # 2. Sibling invoice milik SO yang BELUM BAYAR (tanpa PaymentAllocation &
    #    tanpa retur — retur apapun statusnya dihitung, konsisten dengan
    #    _guard_cancel/_apply_guards, supaya pembatalan sibling tidak
    #    menabrak guard dan menggugurkan transaksi utama).
    siblings = db.query(SalesInvoice).filter(
        SalesInvoice.sales_order_id == so_id,
        SalesInvoice.id != invoice_id,
    ).all()
    dibatalkan: list[str] = []
    for sibling in siblings:
        if _status_value(sibling) in ('BATAL', 'DIBATALKAN'):
            continue
        if db.query(PaymentAllocation.id).filter(
            PaymentAllocation.sales_invoice_id == sibling.id
        ).first():
            continue  # sudah (pernah) dibayar → dibiarkan
        if db.query(SalesRetur.id).filter(
            SalesRetur.sales_invoice_id == sibling.id
        ).first():
            continue  # ber-retur → dibiarkan
        # Ulangi proses pembatalan utk masing-masing sibling (guard +
        # reversal jurnal + workflow event + audit row). SO sudah
        # DIBATALKAN → cascade dari sibling langsung return (anti-loop).
        _cancel_document_tx(db=db, db_obj=sibling, user=user, reason=cascade_reason)
        dibatalkan.append(document_number(sibling))

    logger.info(
        f"Cascade pembatalan invoice {invoice_number}: SO {document_number(so)} "
        f"dibatalkan + {len(dibatalkan)} sibling invoice dibatalkan "
        f"({', '.join(dibatalkan) if dibatalkan else '-'})"
    )
    return so


@atomic_accounting_write
def _cancel_document_tx(db: Session, db_obj, user, reason: Optional[str]):
    """Satu transaksi atomik: guards → cleanup → reversal → status BATAL.

    Decorator menangani: row lock (db_obj), accounting_lock, guard periode
    (tanggal dokumen), idempotency replay, commit/rollback.
    Return: obj (mode REVERSAL) atau None (mode HARD_DELETE).
    """
    document_type = db_obj.__tablename__
    status = _status_value(db_obj)

    if status in ('BATAL', 'DIBATALKAN'):
        raise ValueError('Dokumen sudah dibatalkan sebelumnya')

    _guard_cancel(db, document_type, db_obj)

    # === Update #042 — data cascade invoice di-capture SEBELUM branch
    # (objek invoice bisa ter-hard-delete di branch bawah; cascade SO
    # dijalankan setelah dokumen utama selesai dibatalkan). ===
    cascade_so_id = getattr(db_obj, 'sales_order_id', None) if document_type == 'sales_invoice' else None
    cascade_inv_no = document_number(db_obj) if cascade_so_id is not None else None

    # === Update #046 — SO terkait dokumen kas/invoice utk sinkronisasi
    # status PASCA-pembatalan (revert SELESAI → DIPROSES bila tagihan tidak
    # lagi lengkap). Di-capture SEBELUM alokasi pelunasan dihapus di branch
    # REVERSAL supaya SO-nya tetap ketemu.
    # === Update #070 — PENGIRIMAN (DO) ikut dicapture: kriteria SELESAI SO
    # kini memperhitungkan kelengkapan pengiriman — batal DO SELESAI bisa
    # menurunkan SO ke DIPROSES. (DO DIPROSES via branch hard-delete tidak
    # berdampak — qty-nya memang tidak pernah dihitung sebagai terkirim.) ===
    sync_so_ids = set()
    if document_type == 'penerimaan_kas':
        sync_so_ids = {
            row[0] for row in db.query(SalesInvoice.sales_order_id)
            .join(PaymentAllocation, PaymentAllocation.sales_invoice_id == SalesInvoice.id)
            .filter(PaymentAllocation.penerimaan_id == db_obj.id,
                    SalesInvoice.sales_order_id.isnot(None))
            .all()
        }
    elif document_type in ('sales_invoice', 'pengiriman_barang'):
        so_sid = getattr(db_obj, 'sales_order_id', None)
        if so_sid is not None:
            sync_so_ids = {so_sid}

    # === Update #052 — PO terkait dokumen pembelian/kas utk sinkronisasi
    # status PASCA-pembatalan (revert SELESAI → DIPROSES bila rantai PO tidak
    # lagi tuntas). Di-capture SEBELUM alokasi/hard-delete supaya relasinya
    # masih ketemu. ===
    sync_po_ids = set()
    if document_type == 'pembayaran_kas':
        _pinv_rows = db.query(PurchaseInvoice).join(
            PaymentAllocation, PaymentAllocation.purchase_invoice_id == PurchaseInvoice.id
        ).filter(PaymentAllocation.pembayaran_id == db_obj.id).all()
        from app.services.pembelian_service import po_ids_of_invoice
        for _pinv in _pinv_rows:
            sync_po_ids |= po_ids_of_invoice(db, _pinv)
    elif document_type == 'purchase_invoice':
        from app.services.pembelian_service import po_ids_of_invoice
        sync_po_ids = po_ids_of_invoice(db, db_obj)
    elif document_type == 'penerimaan_barang':
        _po_sid = getattr(db_obj, 'purchase_order_id', None)
        if _po_sid is not None:
            sync_po_ids = {_po_sid}

    journals = _collect_journals(db, db_obj)
    mutasi = _collect_mutasi(db, db_obj) if document_type in _STOCK_KINDS else []
    has_accounting_trace = bool(journals) or bool(mutasi)

    if not has_accounting_trace:
        # === Branch HARD DELETE (dokumen tanpa jejak akuntansi) ===
        from app.services.hard_delete_service import hard_delete_document
        hard_delete_document(db, document_type, db_obj.id, user, reason=reason)
        # Audit trail pembatalan seragam (mode HARD_DELETE) — snapshot lengkap
        # tetap tersimpan di deleted_document_log oleh hard_delete_document.
        row = DocumentCancellation(
            document_type=document_type,
            document_id=db_obj.id,
            document_number=document_number(db_obj),
            document_date=db_obj.tanggal,
            document_status=str(status) if status is not None else None,
            total_amount=_total_amount(db_obj),
            reason=reason,
            mode='HARD_DELETE',
            reversal_jurnal_ids=[],
            cancelled_by=user.id,
            cancelled_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.flush()
        # === Update #042 — cascade (G): SO sumber + sibling invoice belum
        # bayar ikut dibatalkan (draft invoice dihapus → SO terkunci karena
        # invoice-nya pun ikut dibatalkan supaya bisa dibuat ulang). ===
        if cascade_so_id is not None:
            _cascade_cancel_sales_invoice_so(
                db, db_obj.id, cascade_inv_no, cascade_so_id, user, reason
            )
        return row

    # === Branch REVERSAL (dokumen POSTED / sudah menggerakkan stok) ===
    if not reason or not str(reason).strip():
        raise ValueError('Alasan pembatalan wajib diisi untuk dokumen yang sudah diposting')

    # 1. Hapus alokasi pelunasan milik dokumen kas settlement — sisa
    #    piutang/hutang invoice pulih.
    if document_type in ('penerimaan_kas', 'pembayaran_kas'):
        col = PaymentAllocation.penerimaan_id if document_type == 'penerimaan_kas' else PaymentAllocation.pembayaran_id
        deleted = (db.query(PaymentAllocation)
                   .filter(col == db_obj.id)
                   .delete(synchronize_session=False))
        if deleted:
            logger.info(f"Pembatalan {document_number(db_obj)}: {deleted} alokasi pelunasan dihapus")

    # 2. Asset event: pulihkan state aset dari snapshot `sebelum` (guard:
    #    hanya event POSTED terakhir) — sebelum reverse jurnal supaya guard
    #    registrasi aset di reverse_journal tidak melihat event aktif.
    if document_type == 'asset_event' and status == 'POSTED':
        _revert_asset_event(db, db_obj)

    # 3. Reverse mutasi stok (exact layer; stok kurang → ValueError → rollback).
    if mutasi:
        _reverse_stock(db, db_obj, user.id, str(reason), mutasi)

    # 4. Reverse setiap jurnal dokumen (idempotent — bila jurnal sudah punya
    #    pembalik, reverse_journal mengembalikan pembalik existing).
    from app.services.posting_service import reverse_journal
    reversals = []
    for jurnal in journals:
        rev = reverse_journal(
            db, jurnal.id, user.id,
            reason=f"Pembatalan {document_number(db_obj)}: {reason}",
        )
        if rev is not None and all(r.id != rev.id for r in reversals):
            reversals.append(rev)

    # 5. Status dokumen → BATAL/DIBATALKAN + workflow event.
    batal = _status_batal_value(db_obj)
    db_obj.status = batal
    wf = find_workflow(db, db_obj)
    if wf is None:
        wf = DocumentWorkflow(document_type=document_type, document_id=db_obj.id,
                              state='CANCELLED', version=0)
        db.add(wf)
        db.flush()
    append_event(db, wf, 'cancel', 'CANCELLED', user.id, reason)

    # 6. Audit trail pembatalan.
    db.add(DocumentCancellation(
        document_type=document_type,
        document_id=db_obj.id,
        document_number=document_number(db_obj),
        document_date=db_obj.tanggal,
        document_status=str(getattr(batal, 'value', batal)),
        total_amount=_total_amount(db_obj),
        reason=reason,
        mode='REVERSAL',
        reversal_jurnal_ids=[
            {'id': str(r.id), 'noJurnal': r.no_jurnal, 'reversalOf': str(r.reversal_of_id)}
            for r in reversals
        ],
        cancelled_by=user.id,
        cancelled_at=datetime.now(timezone.utc),
    ))

    logger.info(
        f"Dokumen {document_type} {document_number(db_obj)} dibatalkan (reversal-first) "
        f"oleh {user.id}: {len(journals)} jurnal dibalik, {len(mutasi)} mutasi stok dibalik, "
        f"status → {getattr(batal, 'value', batal)}"
    )
    # === Update #042 — cascade (G): SO sumber ikut DIBATALKAN + invoice lain
    # milik SO yang belum bayar ikut dibatalkan (reversal jurnal masing2).
    # Dijalankan SETELAH dokumen utama selesai dibatalkan; urutan internal
    # (SO batal dulu → sibling) menjaga idempotensi & anti-loop. ===
    if cascade_so_id is not None:
        _cascade_cancel_sales_invoice_so(
            db, db_obj.id, cascade_inv_no, cascade_so_id, user, reason
        )
    # === Update #046 — sinkronisasi status SO terkait (batal penerimaan
    # pelunasan / batal invoice → SO yang SELESAI kembali DIPROSES bila
    # tagihannya tidak lagi lengkap; SO yang ikut di-cascade DIBATALKAN
    # otomatis dilewati oleh sync). Tanpa commit — dekorator yang commit. ===
    if sync_so_ids:
        try:
            from app.services.penjualan_service import sync_so_completion
            sync_so_completion(db, sync_so_ids)
        except Exception as sync_exc:  # noqa: BLE001 — jangan gagalkan pembatalan
            logger.warning(
                f"Sinkronisasi status SO pasca-pembatalan {document_number(db_obj)} "
                f"dilewati: {sync_exc}"
            )
    # === Update #052 — sinkronisasi status PO terkait (batal pembayaran
    # hutang / batal invoice / batal penerimaan → PO SELESAI kembali
    # DIPROSES bila rantainya tidak lagi tuntas). ===
    if sync_po_ids:
        try:
            from app.services.pembelian_service import sync_po_completion
            sync_po_completion(db, sync_po_ids)
        except Exception as sync_exc:  # noqa: BLE001 — jangan gagalkan pembatalan
            logger.warning(
                f"Sinkronisasi status PO pasca-pembatalan {document_number(db_obj)} "
                f"dilewati: {sync_exc}"
            )
    return db_obj


def cancel_document(db: Session, document_type: str, document_id: UUID,
                    user, reason: Optional[str] = None) -> dict:
    """Batalkan dokumen — reversal-first bila ber-jurnal, hard delete bila draft.

    Dipanggil oleh seluruh endpoint POST /{module}/{doc}/{id}/cancel.
    """
    model = MODELS.get(document_type)
    if model is None:
        raise HTTPException(404, 'Jenis dokumen tidak didukung')
    obj = db.get(model, document_id)
    if obj is None:
        raise HTTPException(404, 'Dokumen tidak ditemukan')

    number = document_number(obj)
    # === Update #042 — capture SO sumber invoice SEBELUM transaksi (objek
    # bisa ter-hard-delete) utk keterangan cascade di respons. ===
    so_id_for_note = getattr(obj, 'sales_order_id', None) if document_type == 'sales_invoice' else None
    result = _cancel_document_tx(db=db, db_obj=obj, user=user, reason=reason)

    # === Update #042 — keterangan cascade pembatalan invoice → SO (#G) ===
    cascade_note = ''
    if so_id_for_note is not None:
        so_after = db.get(SalesOrder, so_id_for_note)
        if so_after is not None and _status_value(so_after) in ('BATAL', 'DIBATALKAN'):
            cascade_note = (
                ' SO sumber ikut dibatalkan beserta invoice lain milik SO '
                'yang belum dibayar (Update #042).'
            )

    if isinstance(result, DocumentCancellation):
        # HARD DELETE — dokumen + jurnal (jika ada) terhapus permanen;
        # snapshot JSON lengkap tersimpan di deleted_document_log.
        return {
            'success': True,
            'mode': 'HARD_DELETE',
            'documentType': document_type,
            'documentId': str(document_id),
            'documentNumber': number,
            'status': 'DELETED',
            'reversalJurnalIds': [],
            'cancelledAt': (result.cancelled_at.isoformat() if result.cancelled_at
                            else datetime.now(timezone.utc).isoformat()),
            'message': (
                f'Dokumen {number} dihapus permanen (tidak memiliki jurnal/stok). '
                f'Histori snapshot tersimpan di log dokumen terhapus.'
                f'{cascade_note}'
            ),
        }

    # REVERSAL — susun respons dari state terkini (aman untuk replay idempotent).
    row = (db.query(DocumentCancellation)
           .filter(DocumentCancellation.document_id == result.id,
                   DocumentCancellation.mode == 'REVERSAL')
           .order_by(DocumentCancellation.created_at.desc())
           .first())
    reversal_ids = (row.reversal_jurnal_ids if row else None) or []
    batal = _status_value(result)
    return {
        'success': True,
        'mode': 'REVERSAL',
        'documentType': document_type,
        'documentId': str(document_id),
        'documentNumber': number,
        'status': str(getattr(batal, 'value', batal)),
        'reversalJurnalIds': reversal_ids,
        'cancelledAt': (row.cancelled_at.isoformat() if row and row.cancelled_at
                        else datetime.now(timezone.utc).isoformat()),
        'message': (
            f'Dokumen {number} dibatalkan — jurnal pembalik dibuat, status menjadi '
            f'{getattr(batal, "value", batal)}. Dokumen & jurnal asli tetap tersimpan (audit trail utuh).'
            f'{cascade_note}'
        ),
    }


def get_cancellation_history(db: Session, document_type: Optional[str] = None,
                             limit: int = 100, offset: int = 0):
    """Daftar histori pembatalan dokumen (modul Histori — AUD-01)."""
    from app.models import Pengguna
    query = (db.query(DocumentCancellation, Pengguna.username)
             .outerjoin(Pengguna, Pengguna.id == DocumentCancellation.cancelled_by))
    if document_type:
        query = query.filter(DocumentCancellation.document_type == document_type)
    rows = query.order_by(DocumentCancellation.cancelled_at.desc()).limit(limit).offset(offset).all()
    return [{
        'id': str(row.id),
        'documentType': row.document_type,
        'documentId': str(row.document_id),
        'documentNumber': row.document_number,
        'documentDate': row.document_date.isoformat() if row.document_date else None,
        'documentStatus': row.document_status,
        'totalAmount': str(row.total_amount) if row.total_amount is not None else None,
        'reason': row.reason,
        'mode': row.mode,
        'reversalJurnalIds': row.reversal_jurnal_ids or [],
        'cancelledBy': username,
        'cancelledAt': row.cancelled_at.isoformat() if row.cancelled_at else None,
    } for row, username in rows]
