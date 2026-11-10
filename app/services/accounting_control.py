"""Transaction boundaries and guards shared by accounting writes."""

from sqlalchemy import text
from sqlalchemy.orm import object_session
from functools import wraps
from inspect import signature


def _extract_primary_model(result):
    """Ambil instance model ORM pertama dari hasil service (fix #042).

    atomic_accounting_write sebelumnya mengasumsikan service selalu
    mengembalikan SATU model (result.__tablename__) — padahal beberapa service
    mengembalikan tuple, mis. create_po_draft_from_rop_items → (po, info).
    Akses result.__tablename__ pada tuple memicu AttributeError → 500 pada
    POST /pembelian/rop-item/po-draft. Helper ini berjalan rekursif untuk
    tuple/list bersarang dan mengembalikan elemen model ORM pertama yang
    punya id; bila hasil bukan model (None/dict/dll) → None (disimpan sebagai
    '__none__' seperti sebelumnya).
    """
    if result is None:
        return None
    if isinstance(result, (tuple, list)):
        for item in result:
            found = _extract_primary_model(item)
            if found is not None:
                return found
        return None
    if hasattr(type(result), "__tablename__") and getattr(result, "id", None) is not None:
        return result
    return None


def accounting_lock(db):
    """Serialize accounting writes with period closing, until outer commit."""
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('asahi-accounting-write', 0))"))


# === Update #051 — fungsi update_* yang menangani dokumen posted secara
# EKSPLISIT dan aman sendiri (tidak memakai guard require_unposted bawaan
# decorator). Di-key per (modul, nama) — BUKAN nama saja — karena nama fungsi
# bisa sama antar service (mis. update_penerimaan juga ada di
# kas_bank_service untuk Penerimaan KAS, yang tetap memakai guard lama).
# Saat ini: pembelian_service.update_penerimaan — penerimaan barang final
# (SELESAI / sudah berjurnal GRNI) boleh diubah TERBATAS (link invoice,
# keterangan, alamat — murni relasi/informasi, tidak menyentuh
# stok/jurnal/periode); service-nya sendiri yang memvalidasi field mana yang
# boleh berubah dan menolak perubahan field berdampak stok/jurnal dengan
# pesan yang jelas. ===
_UPDATE_POSTED_SAFE = {('app.services.pembelian_service', 'update_penerimaan')}


def atomic_accounting_write(function):
    """Rollback failed operations, lock existing documents, check their period."""
    parameters = signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = parameters.bind(*args, **kwargs)
        db = bound.arguments["db"]
        if db.info.get('accounting_depth', 0):
            return function(*bound.args, **bound.kwargs)
        original_commit = db.commit
        try:
            accounting_lock(db)
            idem = db.info.get('idempotency')
            if idem:
                from app.models.transaksi.workflow import IdempotentOperation
                from app.database import BaseModel
                from fastapi import HTTPException
                prior = db.query(IdempotentOperation).filter_by(actor_id=idem[0], request_key=idem[1]).first()
                if prior:
                    if prior.fingerprint != idem[2]:
                        raise HTTPException(409, 'Idempotency-Key sudah dipakai untuk request yang berbeda')
                    if prior.result_type == '__none__':
                        original_commit()
                        return None
                    model = next((m.class_ for m in BaseModel.registry.mappers if m.class_.__tablename__ == prior.result_type), None)
                    result = db.get(model, prior.result_id) if model else None
                    if result is None:
                        raise HTTPException(409, 'Hasil request sebelumnya sudah tidak tersedia')
                    original_commit()
                    return result
            item = bound.arguments.get("db_obj")
            if item is not None:
                item = (db.query(type(item)).filter(type(item).id == item.id)
                        .populate_existing().with_for_update().one())
                bound.arguments["db_obj"] = item
            from app.services.penutupan_periode_service import validate_periode_not_closed
            # Reconciliation functions address the parent by ID instead of db_obj.
            rek = None
            if function.__module__.endswith('rekonsiliasi_bank_service'):
                from app.models.transaksi.kas_bank.rekonsiliasi_bank import RekonsiliasiBank, RekonsiliasiBankDetail
                rek_id = bound.arguments.get('rekonsiliasi_id')
                if not rek_id and bound.arguments.get('detail_id'):
                    detail = db.get(RekonsiliasiBankDetail, bound.arguments['detail_id'])
                    rek_id = detail.rekonsiliasi_bank_id if detail else None
                if rek_id:
                    rek = db.query(RekonsiliasiBank).filter_by(id=rek_id).populate_existing().with_for_update().first()
            for tanggal in (getattr(item, "tanggal", None), bound.arguments.get("tanggal"),
                            getattr(rek, 'tanggal_akhir', None), getattr(item, 'tanggal_akhir', None), bound.arguments.get('tanggal_akhir')):
                if tanggal is not None:
                    validate_periode_not_closed(db, tanggal)
            actor = db.info.get('request_actor')
            for field in ('gudang_id', 'dari_gudang_id', 'ke_gudang_id'):
                warehouse_id = bound.arguments.get(field)
                if warehouse_id:
                    from app.models.master.gudang import Gudang
                    warehouse = db.get(Gudang, warehouse_id)
                    if not warehouse or warehouse.status != 'AKTIF':
                        raise ValueError('Gudang tidak ditemukan atau tidak aktif')
            if bound.arguments.get('pengiriman_id'):
                from app.models import PengirimanBarang
                if db.get(PengirimanBarang, bound.arguments['pengiriman_id']) is None:
                    raise ValueError('Pengiriman sumber tidak ditemukan')
            if actor and rek is not None and function.__name__ not in ('complete_rekonsiliasi', 'void_rekonsiliasi'):
                from app.services.workflow_service import role, APPROVERS
                if actor.id != rek.created_by and role(actor) not in APPROVERS:
                    from fastapi import HTTPException
                    raise HTTPException(403, 'Hanya pembuat atau manajer/admin yang dapat mengedit rekonsiliasi')
            if item is not None and function.__name__ in ('finish_sales_retur', 'finish_purchase_retur'):
                if not item.jurnal_umum_id:
                    raise ValueError('Retur harus disetujui dan diposting sebelum finalisasi stok')
            if item is not None and function.__name__.startswith('update_'):
                # === Update #051 — fungsi safe-list memvalidasi sendiri
                # dokumen posted-nya (lihat _UPDATE_POSTED_SAFE); guard
                # require_unposted bawaan decorator dilewati HANYA untuk
                # fungsi tersebut. ===
                if (function.__module__, function.__name__) not in _UPDATE_POSTED_SAFE:
                    require_unposted(item)
                if actor:
                    from app.services.workflow_service import role, APPROVERS
                    if actor.id != item.created_by and role(actor) not in APPROVERS:
                        from fastapi import HTTPException
                        raise HTTPException(403, 'Hanya pembuat atau manajer/admin yang dapat mengubah dokumen ini')
            # Inner service commits become flushes until audit and idempotency are saved.
            db.info['accounting_depth'] = 1
            db.commit = db.flush
            result = function(*bound.args, **bound.kwargs)
            if item is not None and function.__name__.startswith('update_'):
                from app.services.workflow_service import find_workflow, append_event, effective_state
                wf = find_workflow(db, item)
                if wf:
                    # === Update #051 — dokumen final (safe-list): catat event
                    # 'edit' TANPA me-reset state workflow ke DRAFT — link
                    # invoice tidak mengubah status dokumen, jurnal & stok
                    # tidak tersentuh. Dokumen draft tetap di-reset seperti
                    # semula (perilaku lama). ===
                    state = effective_state(item, wf)
                    if (function.__module__, function.__name__) in _UPDATE_POSTED_SAFE and state not in ('DRAFT', 'REJECTED'):
                        append_event(db, wf, 'edit', state, actor.id if actor else item.created_by)
                    else:
                        wf.approved_by = None
                        append_event(db, wf, 'edit', 'DRAFT', actor.id if actor else item.created_by)
            if item is not None and function.__name__ in ('finish_sales_retur', 'finish_purchase_retur'):
                from app.services.workflow_service import find_workflow, append_event
                wf = find_workflow(db, item)
                if wf:
                    append_event(db, wf, 'finish_stock', 'POSTED', actor.id if actor else item.created_by)
            if item is not None and function.__name__.startswith('cancel_'):
                from app.services.workflow_service import find_workflow, append_event, effective_state
                wf = find_workflow(db, item)
                if wf:
                    append_event(db, wf, 'cancel', effective_state(item),
                                 actor.id if actor else bound.arguments.get('user_id') or item.created_by)
            if idem:
                from uuid import uuid4
                # Fix #042: hasil service bisa berupa tuple/list (mis. PO draft
                # dari ROP → (po, info)); ekstrak model utamanya sebelum disimpan
                # ke IdempotentOperation — akses langsung result.__tablename__
                # pada tuple memicu AttributeError → HTTP 500.
                primary = _extract_primary_model(result)
                db.add(IdempotentOperation(actor_id=idem[0], request_key=idem[1], fingerprint=idem[2],
                                           result_type=type(primary).__tablename__ if primary is not None else '__none__',
                                           result_id=primary.id if primary is not None else uuid4()))
            db.flush()
            original_commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.commit = original_commit
            db.info.pop('accounting_depth', None)
    return wrapped


def require_unposted(item):
    if getattr(item, "jurnal_umum_id", None):
        raise ValueError("Dokumen sudah diposting. Batalkan dengan jurnal pembalik sebelum membuat koreksi.")
    value = getattr(getattr(item, "status", None), "value", getattr(item, "status", None))
    # DIBUAT = status final ROP hasil tombol "Buat ROP" (update ASAHI #003)
    if value in ("BATAL", "DIBATALKAN", "SELESAI", "DISETUJUI", "DIBUAT", "POSTED"):
        raise ValueError("Dokumen final/batal tidak bisa diubah.")
    db = object_session(item)
    if db:
        from app.services.workflow_service import find_workflow
        wf = find_workflow(db, item)
        if wf and wf.state not in ('DRAFT', 'REJECTED'):
            raise ValueError('Dokumen terkunci oleh workflow. Withdraw/reject sebelum mengedit.')


def require_no_stock_movement(item):
    value = getattr(item.status, "value", item.status)
    if value in ("SELESAI", "DISETUJUI", "DIBUAT"):
        raise ValueError("Dokumen sudah mengubah stok. Gunakan retur/penyesuaian stok, bukan pembatalan langsung.")
