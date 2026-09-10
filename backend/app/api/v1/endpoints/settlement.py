from datetime import date
from typing import Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.deps import get_current_db, get_current_user
from app.models import PenerimaanKas, PembayaranKas
from app.schemas.base import PaginatedResponse
from app.schemas.settlement import SettlementCreate, AllocationUpdate, SettlementResponse, InvoiceBalanceResponse, SettlementHistoryResponse
from app.services import settlement_service as svc

router = APIRouter()
Jenis = Literal['piutang', 'hutang']


def get_payment(db, jenis, payment_id):
    obj = db.get(PenerimaanKas if jenis == 'piutang' else PembayaranKas, payment_id)
    if obj is None:
        raise HTTPException(404, 'Dokumen kas/bank tidak ditemukan')
    return obj


@router.get('/tagihan/{jenis}', response_model=PaginatedResponse[InvoiceBalanceResponse])
def get_outstanding(jenis: Jenis, pihak_id: Optional[UUID] = Query(default=None, alias='pihakId'),
                    status_pembayaran: Optional[Literal['BELUM_DIBAYAR','PARSIAL','LUNAS','LEBIH_BAYAR']] = Query(default=None, alias='statusPembayaran'),
                    as_of: Optional[date] = Query(default=None, alias='asOf'),
                    skip: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
                    db=Depends(get_current_db), user=Depends(get_current_user)):
    model = svc.invoice_model(jenis)
    day = as_of or svc.today()
    query = db.query(model).filter(model.id.in_(svc.active_documents(db, model, day).scalar_subquery()))
    if pihak_id:
        query = query.filter((model.pelanggan_id if jenis == 'piutang' else model.supplier_id) == pihak_id)
    invoices = query.order_by(model.tanggal, model.id).all()
    summaries = svc.balances(db, invoices, day)
    data = [summaries[obj.id] for obj in invoices if (summaries[obj.id]['status_pembayaran'] == status_pembayaran if status_pembayaran else summaries[obj.id]['sisa_tagihan'] > 0)]
    return {'data': data[skip:skip+limit], 'total': len(data), 'skip': skip, 'limit': limit}


@router.get('/invoice/{jenis}/{invoice_id}', response_model=SettlementHistoryResponse)
def get_invoice_settlement(jenis: Jenis, invoice_id: UUID, as_of: Optional[date] = Query(default=None, alias='asOf'),
                           db=Depends(get_current_db), user=Depends(get_current_user)):
    obj = db.get(svc.invoice_model(jenis), invoice_id)
    if obj is None:
        raise HTTPException(404, 'Invoice tidak ditemukan')
    day = as_of or svc.today()
    data = svc.balances(db, [obj], day)[obj.id]
    from app.models.transaksi.payment_allocation import PaymentAllocation
    invoice_fk = PaymentAllocation.sales_invoice_id if jenis == 'piutang' else PaymentAllocation.purchase_invoice_id
    payment_model = PenerimaanKas if jenis == 'piutang' else PembayaranKas
    active = {r[0] for r in svc.active_documents(db, payment_model, day).all()}
    history = []
    for allocation in db.query(PaymentAllocation).filter(invoice_fk == obj.id).order_by(PaymentAllocation.created_at, PaymentAllocation.id):
        payment = allocation.penerimaan if jenis == 'piutang' else allocation.pembayaran
        history.append({'paymentId': str(payment.id), 'noBukti': payment.no_bukti, 'tanggal': payment.tanggal,
                        'nilai': str(allocation.nilai), 'status': getattr(payment.status, 'value', payment.status),
                        'dihitung': payment.id in active})
    return {**data, 'as_of_date': day, 'pembayaran': history}


@router.post('/{jenis}', response_model=SettlementResponse, status_code=201)
def create_pelunasan(jenis: Jenis, data_in: SettlementCreate, db=Depends(get_current_db), user=Depends(get_current_user)):
    try:
        obj = svc.create_settlement(db, jenis, data_in.pihak_id, data_in.tanggal, data_in.kas_bank_id,
                                    data_in.no_nukti, [r.model_dump() for r in data_in.alokasi], user.id, data_in.catatan)
        return svc.payment_summary(obj)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('/{jenis}/{payment_id}', response_model=SettlementResponse)
def get_pelunasan(jenis: Jenis, payment_id: UUID, db=Depends(get_current_db), user=Depends(get_current_user)):
    return svc.payment_summary(get_payment(db, jenis, payment_id))


@router.put('/{jenis}/{payment_id}', response_model=SettlementResponse)
def update_pelunasan(jenis: Jenis, payment_id: UUID, data_in: AllocationUpdate, db=Depends(get_current_db), user=Depends(get_current_user)):
    try:
        obj = svc.update_allocations(db, get_payment(db, jenis, payment_id), jenis, data_in.pihak_id,
                                     [r.model_dump() for r in data_in.alokasi])
        return svc.payment_summary(obj)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
