from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.deps import get_current_db, get_current_user
from app.models.transaksi.asset_event import AssetEvent
from app.schemas.asset_cycle import AssetEventCreate, AssetEventResponse, AssetCancel
from app.services import asset_cycle_service as svc

router = APIRouter()


@router.post('', response_model=AssetEventResponse, status_code=201)
def create_asset_event(data: AssetEventCreate, db=Depends(get_current_db), user=Depends(get_current_user)):
    try:
        return svc.create_event(db, data.aset_id, data.jenis, data.tanggal,
            data.model_dump(mode='json', by_alias=True, exclude={'aset_id', 'jenis', 'tanggal'}), user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get('', response_model=list[AssetEventResponse])
def list_asset_events(aset_id: UUID | None = Query(None, alias='asetId'), skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db=Depends(get_current_db)):
    query = db.query(AssetEvent)
    if aset_id is not None:
        query = query.filter_by(aset_id=aset_id)
    return query.order_by(AssetEvent.tanggal, AssetEvent.created_at, AssetEvent.id).offset(skip).limit(limit).all()


@router.get('/{event_id}', response_model=AssetEventResponse)
def get_asset_event(event_id: UUID, db=Depends(get_current_db)):
    obj = db.get(AssetEvent, event_id)
    if not obj:
        raise HTTPException(404, 'Transaksi aset tidak ditemukan')
    return obj


@router.post('/{event_id}/cancel', response_model=AssetEventResponse)
def cancel_asset_event(event_id: UUID, data: AssetCancel, db=Depends(get_current_db), user=Depends(get_current_user)):
    obj = get_asset_event(event_id, db)
    try:
        return svc.cancel_event(db, obj, user, data.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
