"""Transaction boundaries and guards shared by accounting writes."""

from sqlalchemy import text
from sqlalchemy.orm import object_session
from functools import wraps
from inspect import signature


def accounting_lock(db):
    """Serialize accounting writes with period closing, until outer commit."""
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('asahi-accounting-write', 0))"))


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
            if actor and rek is not None and function.__name__ not in ('complete_rekonsiliasi', 'void_rekonsiliasi'):
                from app.services.workflow_service import role, APPROVERS
                if actor.id != rek.created_by and role(actor) not in APPROVERS:
                    from fastapi import HTTPException
                    raise HTTPException(403, 'Hanya pembuat atau manajer/admin yang dapat mengedit rekonsiliasi')
            if item is not None and function.__name__ in ('finish_sales_retur', 'finish_purchase_retur'):
                if not item.jurnal_umum_id:
                    raise ValueError('Retur harus disetujui dan diposting sebelum finalisasi stok')
            if item is not None and function.__name__.startswith('update_'):
                require_unposted(item)
                if actor:
                    from app.services.workflow_service import role, APPROVERS
                    if actor.id != item.created_by and role(actor) not in APPROVERS:
                        from fastapi import HTTPException
                        raise HTTPException(403, 'Hanya pembuat atau manajer/admin yang dapat mengedit draft')
            # Inner service commits become flushes until audit and idempotency are saved.
            db.info['accounting_depth'] = 1
            db.commit = db.flush
            result = function(*bound.args, **bound.kwargs)
            if item is not None and function.__name__.startswith('update_'):
                from app.services.workflow_service import find_workflow, append_event
                wf = find_workflow(db, item)
                if wf:
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
                db.add(IdempotentOperation(actor_id=idem[0], request_key=idem[1], fingerprint=idem[2],
                                           result_type=result.__tablename__ if result is not None else '__none__',
                                           result_id=result.id if result is not None else uuid4()))
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
    if value in ("BATAL", "DIBATALKAN", "SELESAI", "DISETUJUI", "POSTED"):
        raise ValueError("Dokumen final/batal tidak bisa diubah.")
    db = object_session(item)
    if db:
        from app.services.workflow_service import find_workflow
        wf = find_workflow(db, item)
        if wf and wf.state not in ('DRAFT', 'REJECTED'):
            raise ValueError('Dokumen terkunci oleh workflow. Withdraw/reject sebelum mengedit.')


def require_no_stock_movement(item):
    value = getattr(item.status, "value", item.status)
    if value in ("SELESAI", "DISETUJUI"):
        raise ValueError("Dokumen sudah mengubah stok. Gunakan retur/penyesuaian stok, bukan pembatalan langsung.")
