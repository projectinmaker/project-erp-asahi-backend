from fastapi import HTTPException
from app.models.organization import OrganizationUnit, DocumentOrganization, ReportingAudit, FIELDS
from app.services.accounting_control import atomic_accounting_write, require_unposted

KINDS = dict(zip(FIELDS, ('COMPANY','BRANCH','DEPARTMENT','COST_CENTER','PROJECT')))
PARENTS = {'COMPANY': (), 'BRANCH': ('COMPANY',), 'DEPARTMENT': ('COMPANY','BRANCH'),
           'COST_CENTER': ('COMPANY','BRANCH','DEPARTMENT'), 'PROJECT': ('COMPANY','BRANCH','DEPARTMENT')}


def values(obj):
    return {key: getattr(obj, key, None) for key in FIELDS}


def audit(db, kind, obj_id, action, before, after, user):
    def serial(data):
        return {k: str(v) if v is not None else None for k,v in data.items()} if data is not None else None
    db.add(ReportingAudit(entity_type=kind, entity_id=obj_id, action=action, before=serial(before), after=serial(after), actor_id=user.id))


def ancestors(db, unit, active=True):
    result = {}
    while unit:
        if active and unit.status != 'AKTIF':
            raise ValueError('Organisasi atau induknya tidak aktif')
        result[unit.kind] = unit.id
        unit = db.get(OrganizationUnit, unit.parent_id) if unit.parent_id else None
    return result


def validate(db, data, active=True):
    result = dict(data)
    combined = {}
    for field, kind in KINDS.items():
        if data.get(field) is None:
            continue
        unit = db.get(OrganizationUnit, data[field])
        if not unit or unit.kind != kind:
            raise ValueError(f'{field} tidak ditemukan atau jenisnya salah')
        for ancestor_kind, identifier in ancestors(db, unit, active).items():
            if ancestor_kind in combined and combined[ancestor_kind] != identifier:
                raise ValueError('Dimensi organisasi berasal dari perusahaan/cabang/departemen berbeda')
            combined[ancestor_kind] = identifier
    for field, kind in KINDS.items():
        result[field] = combined.get(kind)
    return result


@atomic_accounting_write
def create_unit(db, data, user):
    if not data['code'].strip() or not data['name'].strip():
        raise ValueError('Kode dan nama organisasi wajib diisi')
    if db.query(OrganizationUnit).filter_by(kind=data['kind'], code=data['code']).first():
        raise ValueError('Kode organisasi sudah digunakan')
    parent = db.get(OrganizationUnit, data['parent_id']) if data.get('parent_id') else None
    if data['kind'] == 'COMPANY':
        if parent or data.get('parent_id'):
            raise ValueError('Perusahaan tidak memiliki induk')
    elif not parent or parent.kind not in PARENTS[data['kind']]:
        raise ValueError('Induk organisasi tidak sesuai')
    if parent:
        ancestors(db, parent)
    obj = OrganizationUnit(**data)
    db.add(obj)
    db.flush()
    audit(db, 'organization_unit', obj.id, 'create', None, data, user)
    return obj


@atomic_accounting_write
def edit_unit(db, unit_id, data, user):
    obj = db.get(OrganizationUnit, unit_id)
    if not obj:
        raise HTTPException(404, 'Organisasi tidak ditemukan')
    if not data['name'].strip():
        raise ValueError('Nama wajib diisi')
    before = {'name': obj.name, 'status': obj.status}
    obj.name, obj.status = data['name'], data['status']
    if obj.status == 'AKTIF' and obj.parent_id:
        ancestors(db, db.get(OrganizationUnit, obj.parent_id))
    audit(db, 'organization_unit', obj.id, 'edit', before, data, user)
    return obj


@atomic_accounting_write
def assign_document(db, kind, document_id, data, expected_version, user):
    from app.services import workflow_service as wf
    if not wf.can_make(user, kind):
        raise HTTPException(403, 'Tidak memiliki akses dokumen ini')
    obj = wf.get_document(db, kind, document_id, lock=True)
    if obj.created_by != user.id and wf.role(user) not in wf.APPROVERS:
        raise HTTPException(403, 'Hanya pembuat atau manajer/admin yang dapat mengedit draft')
    require_unposted(obj)
    from app.services.penutupan_periode_service import validate_periode_not_closed
    validate_periode_not_closed(db, obj.tanggal)
    workflow = wf.find_workflow(db, obj)
    if (workflow.version if workflow else 0) != expected_version:
        raise HTTPException(409, 'Versi dokumen berubah; muat ulang workflow')
    normalized = validate(db, data)
    row = db.query(DocumentOrganization).filter_by(document_id=document_id).first()
    if row and row.document_type != kind:
        raise ValueError('ID dokumen organisasi ambigu')
    before = values(row)
    if row is None:
        row = DocumentOrganization(document_type=kind, document_id=document_id)
        db.add(row)
    for field, value in normalized.items():
        setattr(row, field, value)
        if kind == 'jurnal_umum':
            setattr(obj, field, value)
    if workflow is None:
        from app.models.transaksi.workflow import DocumentWorkflow
        workflow = DocumentWorkflow(document_type=kind, document_id=document_id, state='DRAFT', version=0)
        db.add(workflow)
        db.flush()
    wf.append_event(db, workflow, 'organization', 'DRAFT', user.id)
    audit(db, kind, document_id, 'organization', before, normalized, user)
    db.flush()
    return row


def for_source(db, document_id):
    row = db.query(DocumentOrganization).filter_by(document_id=document_id).first() if document_id else None
    return values(row)


def apply_scope(db, query, journal):
    scope = db.info.get('report_scope', {})
    for field in FIELDS:
        if scope.get(field):
            query = query.filter(getattr(journal, field) == scope[field])
    if scope.get('unassigned'):
        query = query.filter(journal.company_id.is_(None))
    return query
