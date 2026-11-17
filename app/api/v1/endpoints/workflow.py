from typing import Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from app.api.deps import get_current_db, get_current_user
from app.services import workflow_service as svc
from app.services import approval_config_service
from app.services.access_service import effective_permissions
from app.schemas.workflow import WorkflowResponse

router = APIRouter()


class ActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')
    expected_version: int = Field(alias='expectedVersion', ge=0)
    reason: Optional[str] = Field(default=None, max_length=2000)


class ApprovalConfigUpdate(BaseModel):
    """=== Update #071 — payload ubah konfigurasi persetujuan satu jenis dokumen.
    Setiap approver: userId ATAU roleCode (salah satu saja). ==="""
    model_config = ConfigDict(populate_by_name=True, extra='forbid')
    enabled: bool
    approvers: list[dict] = Field(default_factory=list)


@router.get('/{document_type}/{document_id}', response_model=WorkflowResponse)
def get_workflow(document_type: str, document_id: UUID, db=Depends(get_current_db), user=Depends(get_current_user)):
    if not svc.can_read(db, user, document_type):
        raise HTTPException(403, 'Tidak memiliki akses dokumen ini')
    return svc.describe(db, svc.get_document(db, document_type, document_id), user)


@router.post('/{document_type}/{document_id}/{action}', response_model=WorkflowResponse)
def act(document_type: str, document_id: UUID, action: Literal['submit','approve','reject','withdraw','post','execute','cancel'],
        data_in: ActionRequest, db=Depends(get_current_db), user=Depends(get_current_user)):
    if not svc.can_read(db, user, document_type):
        raise HTTPException(403, 'Tidak memiliki akses dokumen ini')
    try:
        obj = svc.transition(db, document_type, document_id, action, user, data_in.expected_version, data_in.reason)
        return svc.describe(db, obj, user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# === Update #071 — konfigurasi persetujuan (Preferensi ▸ Persetujuan) ===
# GET  /workflow/approval-config                 → daftar jenis dokumen + approver
# PUT  /workflow/approval-config/{document_type} → ubah saklar + approver
#
# Hanya SUPER_ADMIN (gate eksplisit di bawah — router workflow ditegakkan
# per-dokumen di service, endpoint ini harus meng-gate dirinya sendiri).
# ═══════════════════════════════════════════════════════════════════════════

def _require_super_admin(db, user):
    _, is_super = effective_permissions(db, user)
    if not is_super:
        raise HTTPException(403, 'Hanya Super Admin yang dapat mengatur konfigurasi persetujuan')


@router.get('/approval-config')
def get_approval_config(db=Depends(get_current_db), user=Depends(get_current_user)):
    _require_super_admin(db, user)
    return {'data': approval_config_service.get_config(db)}


@router.put('/approval-config/{document_type}')
def update_approval_config(document_type: str, data_in: ApprovalConfigUpdate,
                           db=Depends(get_current_db), user=Depends(get_current_user)):
    _require_super_admin(db, user)
    result = approval_config_service.set_config(db, document_type, data_in.enabled, data_in.approvers, user)
    db.commit()
    return result


# ═══════════════════════════════════════════════════════════════════════════
# === Update #071 — batch status persetujuan untuk daftar dokumen ===
# GET /workflow/states?documentType=...&ids=id1,id2,...
#
# Pengganti polling per-baris (N+1) use-workflow-states lama: SATU request
# untuk satu halaman daftar. Response = describe() TANPA history (riwayat
# lengkap diambil on-demand oleh popover riwayat via get_workflow).
# Gate per jenis dokumen: can_read (sama seperti get_workflow).
# ═══════════════════════════════════════════════════════════════════════════

@router.get('/states')
def get_states(document_type: str = Query(alias='documentType'),
               ids: str = Query(default='', max_length=4000),
               db=Depends(get_current_db), user=Depends(get_current_user)):
    if not svc.can_read(db, user, document_type):
        raise HTTPException(403, 'Tidak memiliki akses dokumen ini')
    doc_ids = [part for part in ids.split(',') if part]
    if not doc_ids:
        return {'data': []}
    if len(doc_ids) > 200:
        raise HTTPException(400, 'Maksimal 200 dokumen per request')
    model = svc.MODELS.get(document_type)
    if model is None:
        raise HTTPException(404, 'Jenis dokumen tidak didukung')
    rows = db.query(model).filter(model.id.in_(doc_ids)).all()
    data = []
    for obj in rows:
        try:
            item = svc.describe(db, obj, user)
        except Exception:
            continue  # baris tanpa workflow (mis. jurnal sistem) → skip senyap
        item.pop('history', None)
        data.append(item)
    return {'data': data}


# ═══════════════════════════════════════════════════════════════════════════
# === Update #071 — ringkasan dokumen menunggu persetujuan (badge lonceng) ===
#
# Endpoint GET /workflow (capabilities) dan GET /workflow/queue DIHAPUS
# bersama halaman "Antrean Persetujuan" di frontend. Penggantinya: badge
# lonceng notifikasi di header aplikasi yang memanggil endpoint ringan ini.
# Perbedaan penting vs /queue yang lama: TIDAK ada fetch detail dokumen per
# baris (N+1 query describe() — berat kalau di-poll), cukup SATU query
# agregasi COUNT(*) GROUP BY document_type — respons <1KB, aman di-poll
# tiap 30 detik oleh badge. Persetujuan dilakukan lewat kolom "Persetujuan"
# pada daftar dokumen modul terkait. Gating: workflow.queue.view (mapping di
# access_registry.infer_permission, cabang _SERVICE_ENFORCED) — hanya
# SUPER_ADMIN / Finance Manager. ===
# ═══════════════════════════════════════════════════════════════════════════
@router.get('/pending-summary')
def get_pending_summary(db=Depends(get_current_db), user=Depends(get_current_user)):
    from sqlalchemy import func
    from app.models.transaksi.workflow import DocumentWorkflow
    rows = (
        db.query(DocumentWorkflow.document_type, func.count(DocumentWorkflow.id))
        .filter(DocumentWorkflow.state == 'PENDING')
        .group_by(DocumentWorkflow.document_type)
        .all()
    )
    per_type = {doc_type: count for doc_type, count in rows}
    return {'total': sum(per_type.values()), 'perType': per_type}
