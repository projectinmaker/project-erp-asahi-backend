from datetime import date
from typing import Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from app.api.deps import get_current_db, get_current_user
from app.models import PenerimaanKas, PembayaranKas, Pelanggan, Supplier
from app.schemas.base import PaginatedResponse
from app.schemas.settlement import SettlementCreate, AllocationUpdate, SettlementResponse, InvoiceBalanceResponse, SettlementHistoryResponse
from app.services import settlement_service as svc
from app.services import workflow_service
from app.services import excel_service

router = APIRouter()
Jenis = Literal['piutang', 'hutang']

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(wb, filename: str) -> StreamingResponse:
    return StreamingResponse(
        excel_service.workbook_to_stream(wb),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def get_payment(db, jenis, payment_id):
    obj = db.get(PenerimaanKas if jenis == 'piutang' else PembayaranKas, payment_id)
    if obj is None:
        raise HTTPException(404, 'Dokumen kas/bank tidak ditemukan')
    return obj


def _filter_tagihan(invoices, summaries, status_pembayaran):
    """Update #7 (M-06): filter baris tagihan untuk GET /tagihan dan export.

    - ``'SEMUA'`` (opsi "Semua" FE) → semua invoice AKTIF tanpa syarat
      ``sisa_tagihan > 0`` — sebelumnya invoice LUNAS hilang dari tampilan
      default karena filternya ``sisa_tagihan > 0``.
    - ``None`` (default tanpa param) → perilaku lama: hanya tagihan berjalan
      (``sisa_tagihan > 0``).
    - nilai spesifik (BELUM_DIBAYAR/PARSIAL/LUNAS/LEBIH_BAYAR) → samakan
      ``status_pembayaran``.
    Baris non-aktif (jurnal belum POSTED / di-reverse / dibatalkan — lihat
    ``settlement_service.balances`` kolom ``aktif``) selalu dikecualikan.
    """
    if status_pembayaran == 'SEMUA':
        return [summaries[obj.id] for obj in invoices if summaries[obj.id]['aktif']]
    if status_pembayaran is None:
        return [summaries[obj.id] for obj in invoices if summaries[obj.id]['sisa_tagihan'] > 0]
    return [summaries[obj.id] for obj in invoices if summaries[obj.id]['status_pembayaran'] == status_pembayaran]


@router.get('/tagihan/{jenis}', response_model=PaginatedResponse[InvoiceBalanceResponse])
def get_outstanding(jenis: Jenis, pihak_id: Optional[UUID] = Query(default=None, alias='pihakId'),
                    status_pembayaran: Optional[Literal['BELUM_DIBAYAR','PARSIAL','LUNAS','LEBIH_BAYAR','SEMUA']] = Query(default=None, alias='statusPembayaran'),
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
    # Update #7 (M-06): SEMUA → tampilkan semua invoice aktif (termasuk LUNAS).
    data = _filter_tagihan(invoices, summaries, status_pembayaran)
    return {'data': data[skip:skip+limit], 'total': len(data), 'skip': skip, 'limit': limit}


@router.get('/tagihan/{jenis}/export')
def export_tagihan(jenis: Jenis,
                   pihak_id: UUID | None = Query(default=None, alias='pihakId'),
                   status_pembayaran: Literal['BELUM_DIBAYAR','PARSIAL','LUNAS','LEBIH_BAYAR','SEMUA'] | None = Query(default=None, alias='statusPembayaran'),
                   as_of: date | None = Query(default=None, alias='asOf'),
                   db=Depends(get_current_db), user=Depends(get_current_user)):
    """Export daftar tagihan piutang/hutang ke .xlsx (Update #5).

    Data sama seperti GET /tagihan/{jenis} — tanpa pagination (semua baris
    yang cocok filter). Kolom Umur Hari = hari sejak jatuh tempo (0 bila
    belum jatuh tempo), dihitung terhadap tanggal as-of.
    """
    model = svc.invoice_model(jenis)
    day = as_of or svc.today()
    query = db.query(model).filter(model.id.in_(svc.active_documents(db, model, day).scalar_subquery()))
    if pihak_id:
        query = query.filter((model.pelanggan_id if jenis == 'piutang' else model.supplier_id) == pihak_id)
    invoices = query.order_by(model.tanggal, model.id).all()
    summaries = svc.balances(db, invoices, day)
    # Update #7 (M-06): SEMUA → tampilkan semua invoice aktif (termasuk LUNAS).
    data = _filter_tagihan(invoices, summaries, status_pembayaran)

    # Lookup nama pelanggan/supplier untuk kolom Pihak.
    pihak_ids = {d['pihak_id'] for d in data if d['pihak_id']}
    pihak_model = Pelanggan if jenis == 'piutang' else Supplier
    pihak_names = {}
    if pihak_ids:
        pihak_names = {p.id: p.nama for p in db.query(pihak_model).filter(pihak_model.id.in_(pihak_ids)).all()}

    rows = []
    for d in data:
        umur = (day - d['jatuh_tempo']).days if d['jatuh_tempo'] else 0
        rows.append([
            d['no_dokumen'],
            pihak_names.get(d['pihak_id'], '-'),
            d['tanggal'],
            d['jatuh_tempo'],
            d['nilai_tagihan'], d['total_bayar'], d['total_retur'],
            d['sisa_tagihan'], d['kelebihan'],
            d['status_pembayaran'],
            max(0, umur),
        ])
    wb = excel_service.workbook_from_rows(
        headers=["No Invoice", "Pihak", "Tanggal", "Jatuh Tempo", "Nilai Tagihan",
                 "Total Bayar", "Total Retur", "Sisa Tagihan", "Kelebihan",
                 "Status Bayar", "Umur Hari"],
        rows=rows, sheet="Data",
        number_columns={10},
        decimal_columns={4, 5, 6, 7, 8},
        date_columns={2, 3},
    )
    return _xlsx_response(wb, f"tagihan-{jenis}-{day:%Y%m%d}.xlsx")


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
                                    data_in.no_nukti, [r.model_dump() for r in data_in.alokasi], user.id, data_in.catatan,
                                    penalti=data_in.penalti, akun_penalti_id=data_in.akun_penalti_id)
        # Administrator (revisi tim akuntansi): langsung final tanpa langkah approval.
        kind = 'penerimaan_kas' if jenis == 'piutang' else 'pembayaran_kas'
        workflow_service.direct_complete(db, user, kind, obj.id)
        db.refresh(obj)
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
                                     [r.model_dump() for r in data_in.alokasi],
                                     penalti=data_in.penalti, akun_penalti_id=data_in.akun_penalti_id)
        return svc.payment_summary(obj)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
