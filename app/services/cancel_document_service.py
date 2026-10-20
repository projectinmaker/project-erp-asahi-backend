"""Pembatalan dokumen non-destruktif — Update AUD-01.

Endpoint ``/cancel`` semua modul sebelumnya selalu memanggil
``hard_delete_document``: dokumen yang SUDAH diposting ikut dihapus dari
database bersama jurnal asli + seluruh jurnal pembaliknya, sehingga general
ledger "seolah tidak pernah terjadi". Ini melanggar prinsip non-destructive
accounting (koreksi via jurnal pembalik) dan merusak integritas GL serta
audit trail historis (temuan audit AUD-01).

Perilaku baru ``cancel_document``:

- Dokumen **tanpa jurnal** (DRAFT / belum diposting) → hard delete seperti
  lama — aman karena belum menyentuh GL.
- Dokumen **sudah diposting** (punya ``jurnal_umum_id``) → **jurnal
  pembalik** (``posting_service.reverse_journal``) + status dokumen
  ``BATAL``/``DIBATALKAN``. Dokumen, jurnal asli, dan jurnal pembalik
  dipertahankan — audit trail utuh.

Efek turunannya otomatis tanpa perubahan modul lain:

- Outstanding piutang/hutang: ``settlement_service.active_documents`` sudah
  menghormati jurnal pembalik (reversal exists → dokumen non-aktif), jadi
  SI/PI yang dibatalkan otomatis keluar dari daftar tagihan outstanding.
- Alokasi pelunasan yang menempel pada kas yang dibatalkan ikut non-aktif
  (pembayaran/penerimaan sumbernya dibalik) → outstanding pulih.
- Saldo kas/bank: ``auto_posting_jurnal`` menjalankan sync M-03 untuk akun
  yang tersentuh jurnal pembalik.
- Perhitungan laporan (neraca saldo/laba rugi/neraca) membaca GL — jurnal
  pembalik otomatis mengoreksi angka periode berjalan.

Guard yang dipertahankan (reuse ``hard_delete_service._apply_guards``):
periode ditutup → tolak; invoice dengan alokasi/retur/penerimaan aktif →
tolak; order yang direferensikan dokumen lanjutan → tolak; dokumen stok
SELESAI (retur/penyesuaian/pengiriman) → tolak; dokumen kas dengan riwayat
identifikasi aktif → tolak; transaksi aset hanya boleh yang terakhir +
state dipulihkan.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.orm import Session

from app.services.hard_delete_service import (
    _apply_guards,
    _status_value,
    document_number,
    hard_delete_document,
)
from app.services.posting_service import reverse_journal
from app.services.workflow_service import MODELS, find_workflow
from app.models.transaksi.workflow import WorkflowEvent
from app.services.accounting_control import accounting_lock

# Nilai status pembatalan per jenis dokumen (nilai enum yang sudah tersedia
# di masing-masing model — tidak ada perubahan skema).
CANCEL_STATUS = {
    'sales_order': 'DIBATALKAN',
    'sales_invoice': 'DIBATALKAN',
    'sales_retur': 'DIBATALKAN',
    'purchase_order': 'DIBATALKAN',
    'purchase_invoice': 'DIBATALKAN',
    'purchase_retur': 'DIBATALKAN',
    'penawaran': 'DIBATALKAN',
    'tukar_faktur': 'DIBATALKAN',
    'penerimaan_kas': 'BATAL',
    'pembayaran_kas': 'BATAL',
    'transfer_bank': 'BATAL',
    'asset_event': 'BATAL',
}

CANCELLED_STATES = {'BATAL', 'DIBATALKAN'}


def _journal_id_of(document_type: str, obj) -> UUID | None:
    if document_type == 'jurnal_umum':
        return obj.id
    return getattr(obj, 'jurnal_umum_id', None)


def _set_status_cancelled(obj, value: str):
    """Set kolom status ke nilai BATAL/DIBATALKAN pada enum maupun string."""
    current = getattr(obj, 'status', None)
    enum_cls = type(current)
    try:
        obj.status = enum_cls(value)
    except (TypeError, ValueError):
        obj.status = value


def _record_workflow_event(db: Session, obj, user, from_state: str, to_state: str, reason: str | None):
    """Catat event 'cancel' pada workflow dokumen (jejak siapa/kapan/mengapa)."""
    wf = find_workflow(db, obj)
    if wf is None:
        return
    last_version = (db.query(WorkflowEvent.version)
                    .filter(WorkflowEvent.workflow_id == wf.id)
                    .order_by(WorkflowEvent.version.desc()).first())
    next_version = (last_version[0] + 1) if last_version else 1
    db.add(WorkflowEvent(
        workflow_id=wf.id,
        version=next_version,
        action='cancel',
        from_state=from_state[:20],
        to_state=to_state[:20],
        actor_id=getattr(user, 'id'),
        reason=reason,
    ))


def _cancel_posted_document(db: Session, document_type: str, obj, user, reason: str | None) -> dict:
    """Pembatalan ber-jurnal-pembalik untuk dokumen yang sudah diposting."""
    journal_id = _journal_id_of(document_type, obj)
    number = document_number(obj)
    status_before = _status_value(obj)

    if status_before in CANCELLED_STATES:
        raise ValueError(f'Dokumen {number} sudah dibatalkan sebelumnya')

    # Guard periode: jurnal pembalik diposting pada TANGGAL ASLI dokumen
    # (pola reverse_journal), jadi cukup validasi periode tanggal dokumen.
    # Periode yang ditutup harus dibuka lewat workflow otorisasi terlebih dahulu.
    from app.services.penutupan_periode_service import validate_periode_not_closed
    validate_periode_not_closed(db, obj.tanggal, context='pembatalan dokumen')

    # Guard referensi silang / stok / aset (identik dengan jalan lama).
    _apply_guards(db, document_type, obj)

    accounting_lock(db)

    target = CANCEL_STATUS.get(document_type)
    if target is None:
        # Jenis dokumen tidak dipetakan (mis. dokumen stok) — tolak dengan jelas.
        raise ValueError(
            f'Jenis dokumen {document_type} tidak mendukung pembatalan ber-jurnal. '
            'Gunakan fitur pembalikan (reverse) atau retur/penyesuaian terkait.'
        )

    full_reason = f'Pembatalan {document_type} {number}' + (f': {reason}' if reason else '')

    # Jurnal pembalik — idempoten (bila pembalik sudah ada, dipakai ulang).
    reversal = reverse_journal(db, journal_id, getattr(user, 'id'), reason=full_reason)

    # Status dokumen → BATAL/DIBATALKAN (dokumen & jurnal asli dipertahankan).
    _set_status_cancelled(obj, target)
    _record_workflow_event(db, obj, user, str(status_before or ''), target, reason)

    db.commit()

    logger.info(
        f"Pembatalan REVERSAL {document_type} {number} oleh {getattr(user, 'id', None)} "
        f"— jurnal pembalik {reversal.no_jurnal}; dokumen berstatus {target}"
    )

    return {
        'success': True,
        'mode': 'REVERSAL',
        'documentType': document_type,
        'documentId': str(obj.id),
        'documentNumber': number,
        'status': target,
        'reversalJournalId': str(reversal.id),
        'reversalJournalNo': reversal.no_jurnal,
        'cancelledAt': datetime.now(timezone.utc).isoformat(),
        'message': (
            f'Dokumen {number} dibatalkan dengan jurnal pembalik {reversal.no_jurnal}. '
            f'Dokumen berstatus {target}; jurnal asli tetap tersimpan untuk audit.'
        ),
    }


def cancel_document(db: Session, document_type: str, document_id: UUID,
                    user, reason: str | None = None) -> dict:
    """Pembatalan dokumen — non-destruktif bila sudah diposting.

    - Tanpa jurnal (DRAFT) → hard delete (perilaku lama, snapshot ke log).
    - Sudah diposting → jurnal pembalik + status BATAL/DIBATALKAN.
    """
    if document_type == 'jurnal_umum':
        raise ValueError(
            'Pembatalan jurnal manual dilakukan lewat aksi workflow Batal '
            '(menghasilkan jurnal pembalik) — bukan endpoint dokumen ini.'
        )

    model = MODELS.get(document_type)
    if model is None:
        raise HTTPException(404, 'Jenis dokumen tidak didukung')

    obj = (db.query(model).filter(model.id == document_id)
           .populate_existing().with_for_update().first())
    if obj is None:
        raise HTTPException(404, 'Dokumen tidak ditemukan')

    journal_id = _journal_id_of(document_type, obj)
    if journal_id is None:
        # Belum diposting — belum menyentuh GL; hard delete tetap wajar.
        return hard_delete_document(db, document_type, document_id, user, reason=reason)

    return _cancel_posted_document(db, document_type, obj, user, reason)
