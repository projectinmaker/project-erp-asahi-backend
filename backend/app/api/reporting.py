from uuid import UUID
from fastapi import Depends, Query, HTTPException
from app.api.deps import get_current_db
from app.services.organization_service import validate


def report_scope(company_id: UUID | None = Query(None, alias='companyId'), branch_id: UUID | None = Query(None, alias='branchId'),
    department_id: UUID | None = Query(None, alias='departmentId'), cost_center_id: UUID | None = Query(None, alias='costCenterId'),
    project_id: UUID | None = Query(None, alias='projectId'), unassigned: bool = False, db=Depends(get_current_db)):
    data = dict(company_id=company_id, branch_id=branch_id, department_id=department_id, cost_center_id=cost_center_id, project_id=project_id)
    if unassigned and any(data.values()):
        raise HTTPException(400, 'unassigned tidak dapat digabung dengan filter organisasi')
    try:
        data = validate(db, data, active=False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.info['report_scope'] = dict(data, unassigned=unassigned)
