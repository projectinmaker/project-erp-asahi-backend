from typing import Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from app.api.deps import get_current_db, get_current_user
from app.services import workflow_service as svc
from app.schemas.workflow import WorkflowResponse, WorkflowSummary, PaginatedResponse

router = APIRouter()


class ActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')
    expected_version: int = Field(alias='expectedVersion', ge=0)
    reason: Optional[str] = Field(default=None, max_length=2000)


@router.get('/{document_type}/{document_id}', response_model=WorkflowResponse)
def get_workflow(document_type: str, document_id: UUID, db=Depends(get_current_db), user=Depends(get_current_user)):
    if not svc.can_read(user, document_type):
        raise HTTPException(403, 'Tidak memiliki akses dokumen ini')
    return svc.describe(db, svc.get_document(db, document_type, document_id), user)


@router.post('/{document_type}/{document_id}/{action}', response_model=WorkflowResponse)
def act(document_type: str, document_id: UUID, action: Literal['submit','approve','reject','withdraw','post','execute','cancel'],
        data_in: ActionRequest, db=Depends(get_current_db), user=Depends(get_current_user)):
    if not svc.can_read(user, document_type):
        raise HTTPException(403, 'Tidak memiliki akses dokumen ini')
    try:
        obj = svc.transition(db, document_type, document_id, action, user, data_in.expected_version, data_in.reason)
        return svc.describe(db, obj, user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('')
def get_capabilities(user=Depends(get_current_user)):
    return {'role': svc.role(user), 'documents': sorted(k for k in svc.MODELS if svc.can_read(user, k)),
            'canApprove': svc.role(user) in svc.APPROVERS,
            'approvalLevels': 1, 'selfApprovalAllowed': False}


@router.get('/queue', response_model=PaginatedResponse[WorkflowSummary])
def get_queue(state: Literal['PENDING', 'APPROVED', 'REJECTED'] = 'PENDING',
              skip: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
              db=Depends(get_current_db), user=Depends(get_current_user)):
    from app.models.transaksi.workflow import DocumentWorkflow
    kinds = [k for k in svc.MODELS if svc.can_read(user, k)]
    query = db.query(DocumentWorkflow).filter(DocumentWorkflow.document_type.in_(kinds), DocumentWorkflow.state == state)
    total = query.count()
    rows = query.order_by(DocumentWorkflow.updated_at, DocumentWorkflow.id).offset(skip).limit(limit).all()
    data = []
    for row in rows:
        obj = svc.get_document(db, row.document_type, row.document_id)
        item = svc.describe(db, obj, user)
        item.pop('history')
        data.append(item)
    return {'data': data, 'total': total, 'skip': skip, 'limit': limit}
