from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.deps import get_current_db, get_current_user
from app.models.organization import OrganizationUnit, ReportingAudit, CashFlowClassification
from app.schemas.organization import (OrganizationCreate, OrganizationEdit, OrganizationResponse, OrganizationAssignment,
    OrganizationDimensions, CashFlowAssignment, CashFlowClassificationResponse, ReportingAuditResponse)
from app.services import organization_service as svc

router = APIRouter()


@router.get('/units', response_model=list[OrganizationResponse])
def list_organization_units(kind: Literal['COMPANY','BRANCH','DEPARTMENT','COST_CENTER','PROJECT'] | None = None,
    parent_id: UUID | None = Query(None, alias='parentId'), skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db=Depends(get_current_db)):
    query = db.query(OrganizationUnit)
    if kind:
        query = query.filter_by(kind=kind)
    if parent_id:
        query = query.filter_by(parent_id=parent_id)
    return query.order_by(OrganizationUnit.kind, OrganizationUnit.code, OrganizationUnit.id).offset(skip).limit(limit).all()


@router.post('/units', response_model=OrganizationResponse, status_code=201)
def create_organization_unit(data: OrganizationCreate, db=Depends(get_current_db), user=Depends(get_current_user)):
    try:
        return svc.create_unit(db, data.model_dump(), user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put('/units/{unit_id}', response_model=OrganizationResponse)
def edit_organization_unit(unit_id: UUID, data: OrganizationEdit, db=Depends(get_current_db), user=Depends(get_current_user)):
    try:
        return svc.edit_unit(db, unit_id, data.model_dump(), user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get('/dokumen/{kind}/{document_id}', response_model=OrganizationDimensions)
def get_document_organization(kind: str, document_id: UUID, db=Depends(get_current_db), user=Depends(get_current_user)):
    from app.services import workflow_service as wf
    if not wf.can_read(user, kind):
        raise HTTPException(403, 'Tidak memiliki akses dokumen')
    wf.get_document(db, kind, document_id)
    return svc.for_source(db, document_id)


@router.put('/dokumen/{kind}/{document_id}', response_model=OrganizationDimensions)
def assign_document_organization(kind: str, document_id: UUID, data: OrganizationAssignment, db=Depends(get_current_db), user=Depends(get_current_user)):
    try:
        row = svc.assign_document(db, kind, document_id, data.model_dump(exclude={'expected_version'}), data.expected_version, user)
        return svc.values(row)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get('/audit', response_model=list[ReportingAuditResponse])
def list_reporting_audit(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db=Depends(get_current_db)):
    return db.query(ReportingAudit).order_by(ReportingAudit.created_at.desc(), ReportingAudit.id).offset(skip).limit(limit).all()


@router.get('/klasifikasi-arus-kas', response_model=list[CashFlowClassificationResponse])
def list_cashflow_classifications(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db=Depends(get_current_db)):
    return db.query(CashFlowClassification).order_by(CashFlowClassification.target_type, CashFlowClassification.id).offset(skip).limit(limit).all()


@router.put('/klasifikasi-arus-kas', response_model=CashFlowClassificationResponse)
def set_cashflow_classification(data: CashFlowAssignment, db=Depends(get_current_db), user=Depends(get_current_user)):
    from app.services.cashflow_service import set_classification
    try:
        return set_classification(db, data.model_dump(), user)
    except ValueError as e:
        raise HTTPException(400, str(e))
