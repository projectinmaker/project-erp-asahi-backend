"""One-level maker/checker workflow. All writes lock the source document first."""
from fastapi import HTTPException
from app.models import (
    SalesInvoice, PurchaseInvoice, SalesRetur, PurchaseRetur, PembayaranKas, PenerimaanKas,
    AssetEvent, TransferBank, SalesOrder, PurchaseOrder, PengirimanBarang, PenerimaanBarang,
    PenyesuaianStok, PemindahanBarang, PermintaanBarang, JurnalUmum,
)
from app.models.transaksi.workflow import DocumentWorkflow, WorkflowEvent
from app.services.accounting_control import atomic_accounting_write

FINANCE = {'ADMINISTRATOR', 'MANAJER_KEUANGAN', 'STAFF_AKUNTANSI'}
APPROVERS = {'ADMINISTRATOR', 'MANAJER_KEUANGAN'}
MODELS = {m.__tablename__: m for m in (
    SalesInvoice, PurchaseInvoice, SalesRetur, PurchaseRetur, PembayaranKas, PenerimaanKas,
    AssetEvent, TransferBank, SalesOrder, PurchaseOrder, PengirimanBarang, PenerimaanBarang,
    PenyesuaianStok, PemindahanBarang, PermintaanBarang, JurnalUmum,
)}
SALES = {'sales_order', 'sales_invoice', 'sales_retur'}
STOCK = {'pengiriman_barang', 'penerimaan_barang', 'penyesuaian_stok', 'pemindahan_barang', 'permintaan_barang'}
ORDERS = {'sales_order', 'purchase_order'}
FINANCIAL = set(MODELS) - STOCK - ORDERS


def role(user):
    return getattr(user.role, 'value', user.role)


def can_read(user, kind):
    return role(user) in FINANCE or (role(user) == 'STAFF_PENJUALAN' and kind in SALES | {'pengiriman_barang'}) or (role(user) == 'STAFF_GUDANG' and kind in STOCK)


def can_make(user, kind):
    return role(user) in FINANCE or (role(user) == 'STAFF_PENJUALAN' and kind in SALES) or (role(user) == 'STAFF_GUDANG' and kind in STOCK)


def get_document(db, kind, document_id, lock=False):
    model = MODELS.get(kind)
    if model is None:
        raise HTTPException(404, 'Jenis dokumen tidak didukung')
    query = db.query(model).filter(model.id == document_id)
    if lock:
        query = query.populate_existing().with_for_update()
    obj = query.first()
    if obj is None or (kind == 'jurnal_umum' and (getattr(obj.ref_module, 'value', obj.ref_module) != 'MANUAL' or obj.reversal_of_id)):
        raise HTTPException(404, 'Dokumen workflow tidak ditemukan')
    return obj


def find_workflow(db, obj):
    return db.query(DocumentWorkflow).filter_by(document_type=obj.__tablename__, document_id=obj.id).first()


def effective_state(obj, wf=None):
    status = getattr(obj.status, 'value', obj.status)
    if wf and wf.state == 'CANCELLED':
        return 'CANCELLED'
    if status in ('BATAL', 'DIBATALKAN'):
        return 'CANCELLED'
    if obj.__tablename__ in ('jurnal_umum', 'asset_event') and status == 'POSTED':
        return 'POSTED'
    if obj.__tablename__ in STOCK and status in ('SELESAI', 'DISETUJUI'):
        return 'EXECUTED'
    if getattr(obj, 'jurnal_umum_id', None):
        return 'POSTED'
    if status in ('SELESAI', 'DISETUJUI'):
        return 'EXECUTED'
    return wf.state if wf else 'DRAFT'


def append_event(db, wf, action, state, user_id, reason=None):
    before = wf.state
    wf.state = state
    wf.version += 1
    db.add(WorkflowEvent(workflow_id=wf.id, version=wf.version, action=action,
                         from_state=before, to_state=state, actor_id=user_id, reason=reason))


def available_actions(user, obj, wf):
    state = effective_state(obj, wf)
    kind = obj.__tablename__
    result = []
    if state in ('DRAFT', 'REJECTED') and can_make(user, kind) and (obj.created_by == user.id or role(user) in APPROVERS):
        result.append('submit')
    if state == 'PENDING':
        if role(user) in APPROVERS and user.id not in (obj.created_by, wf.submitted_by):
            result += ['approve', 'reject']
        if user.id == wf.submitted_by:
            result.append('withdraw')
    if state == 'APPROVED':
        if kind in FINANCIAL and role(user) in FINANCE:
            result.append('post')
        if kind in STOCK and role(user) in FINANCE | {'STAFF_GUDANG'}:
            result.append('execute')
    if kind == 'jurnal_umum' and state != 'CANCELLED' and role(user) in APPROVERS:
        result.append('cancel')
    if kind in ('sales_retur', 'purchase_retur') and state == 'POSTED' and getattr(obj.status, 'value', obj.status) != 'SELESAI' and role(user) in FINANCE:
        result.append('execute')
    return result


def describe(db, obj, user):
    from app.services.organization_service import for_source
    wf = find_workflow(db, obj)
    events = [] if not wf else db.query(WorkflowEvent).filter_by(workflow_id=wf.id).order_by(WorkflowEvent.version).all()
    return {
        'organization': for_source(db, obj.id),
        'documentType': obj.__tablename__, 'documentId': obj.id,
        'documentNumber': next((getattr(obj, name) for name in ('no_invoice', 'no_form', 'no_retur', 'no_bukti', 'no_transfer', 'no_pesanan', 'no_jurnal', 'no_surat_jalan', 'no_adj', 'no_pemindahan', 'no_permintaan') if hasattr(obj, name)), str(obj.id)),
        'createdBy': obj.created_by, 'submittedBy': wf.submitted_by if wf else None,
        'approvedBy': wf.approved_by if wf else None,
        'canEdit': effective_state(obj, wf) in ('DRAFT', 'REJECTED') and can_make(user, obj.__tablename__) and (user.id == obj.created_by or role(user) in APPROVERS),
        'tanggal': obj.tanggal,
        'total': str(getattr(obj, 'grand_total', getattr(obj, 'total_nilai', getattr(obj, 'nilai_transfer', getattr(obj, 'total_debit', getattr(obj, 'total', 0)))))),
        'state': effective_state(obj, wf), 'documentStatus': getattr(obj.status, 'value', obj.status),
        'version': wf.version if wf else 0,
        'journalId': obj.id if obj.__tablename__ == 'jurnal_umum' else getattr(obj, 'jurnal_umum_id', None),
        'availableActions': available_actions(user, obj, wf),
        'history': [{'version': e.version, 'action': e.action, 'fromState': e.from_state,
                     'toState': e.to_state, 'actorId': e.actor_id, 'reason': e.reason, 'at': e.created_at} for e in events],
    }


@atomic_accounting_write
def transition(db, document_type, document_id, action, user, expected_version, reason=None):
    if not can_read(user, document_type):
        raise HTTPException(403, 'Tidak memiliki akses dokumen ini')
    obj = get_document(db, document_type, document_id, lock=True)
    from app.services.penutupan_periode_service import validate_periode_not_closed
    validate_periode_not_closed(db, obj.tanggal)
    wf = find_workflow(db, obj)
    if wf is None:
        wf = DocumentWorkflow(document_type=document_type, document_id=obj.id, state=effective_state(obj), version=0)
        db.add(wf)
        db.flush()
    if wf.version != expected_version:
        raise HTTPException(409, 'Versi dokumen berubah. Muat ulang workflow sebelum melanjutkan')
    if action not in available_actions(user, obj, wf):
        if action in ('approve', 'reject') and (role(user) not in APPROVERS or user.id in (obj.created_by, wf.submitted_by)):
            raise HTTPException(403, 'Approval memerlukan manajer/admin lain, bukan pembuat atau pengaju dokumen')
        raise HTTPException(409, 'Aksi tidak diizinkan untuk role atau status dokumen saat ini')
    if action in ('submit', 'post', 'execute'):
        from app.services.organization_service import validate, for_source
        validate(db, for_source(db, obj.id))
    if action == 'submit':
        if document_type == 'asset_event':
            from app.services.asset_cycle_service import prepare
            prepare(db, obj)
        if document_type in FINANCIAL - {'jurnal_umum', 'asset_event'}:
            from app.services.document_totals import refresh_totals
            refresh_totals(obj)
            if document_type in ('penerimaan_kas', 'pembayaran_kas'):
                from app.services.settlement_service import validate_payment
                validate_payment(db, obj)
        wf.submitted_by = user.id
        wf.approved_by = None
        target = 'PENDING'
    elif action == 'approve':
        wf.approved_by = user.id
        target = 'APPROVED'
    elif action in ('reject', 'withdraw'):
        if not reason or not reason.strip():
            raise ValueError('Alasan wajib diisi untuk reject/withdraw')
        wf.approved_by = None
        target = 'REJECTED' if action == 'reject' else 'DRAFT'
    elif action == 'post':
        post_existing(db, obj, user.id)
        target = 'POSTED'
    elif action == 'cancel':
        if not reason or not reason.strip():
            raise ValueError('Alasan pembatalan wajib diisi')
        if effective_state(obj, wf) == 'POSTED':
            from app.services.posting_service import reverse_journal
            reverse_journal(db, obj.id, user.id, reason)
        target = 'CANCELLED'
    else:
        execute_existing(db, obj)
        target = 'POSTED' if document_type in ('sales_retur', 'purchase_retur') else 'EXECUTED'
    append_event(db, wf, action, target, user.id, reason)
    db.flush()
    return obj


def post_existing(db, obj, actor_id):
    if obj.__tablename__ == "asset_event":
        from app.services.asset_cycle_service import post_event
        return post_event(db, obj, actor_id)
    if obj.__tablename__ == 'purchase_retur' and not obj.purchase_invoice_id:
        raise ValueError('Pilih purchaseInvoiceId sebelum posting retur pembelian agar hutang invoice dapat diperbarui')
    validate_order_approval(db, obj)
    from app.services import penjualan_service as sales, pembelian_service as purchase, kas_bank_service as cash
    methods = {
        'sales_invoice': sales.post_sales_invoice, 'sales_retur': sales.post_sales_retur,
        'purchase_invoice': purchase.post_purchase_invoice, 'purchase_retur': purchase.post_purchase_retur,
        'pembayaran_kas': cash.post_pembayaran, 'penerimaan_kas': cash.post_penerimaan, 'transfer_bank': cash.post_transfer,
    }
    if obj.__tablename__ == 'jurnal_umum':
        from app.services.posting_service import validate_entries, JurnalEntryItem
        from app.models.transaksi.jurnal import StatusJurnal
        entries = [JurnalEntryItem(r.akun_perkiraan_id, r.debit, r.kredit) for r in obj.details]
        debit, kredit = validate_entries(db, entries)
        if debit != obj.total_debit or kredit != obj.total_kredit:
            raise ValueError('Total jurnal draft tidak sesuai detail')
        obj.status = StatusJurnal.POSTED
    else:
        methods[obj.__tablename__](db, obj, actor_id)


def execute_existing(db, obj):
    validate_order_approval(db, obj)
    from app.services import penjualan_service as sales, pembelian_service as purchase, persediaan_service as stock
    methods = {
        'pengiriman_barang': sales.finish_pengiriman, 'penerimaan_barang': purchase.finish_penerimaan,
        'penyesuaian_stok': stock.approve_penyesuaian, 'pemindahan_barang': stock.approve_pemindahan,
        'permintaan_barang': stock.approve_permintaan,
        'sales_retur': sales.finish_sales_retur, 'purchase_retur': purchase.finish_purchase_retur,
    }
    db.info['workflow_executing'] = True
    try:
        methods[obj.__tablename__](db, obj)
    finally:
        db.info.pop('workflow_executing', None)


def validate_order_approval(db, obj):
    for field, model in (('sales_order_id', SalesOrder), ('purchase_order_id', PurchaseOrder)):
        ref_id = getattr(obj, field, None)
        if ref_id:
            source = db.get(model, ref_id)
            if not source or effective_state(source, find_workflow(db, source)) not in ('APPROVED', 'EXECUTED'):
                raise ValueError('Order sumber harus disetujui sebelum posting/eksekusi dokumen lanjutan')
            party = 'pelanggan_id' if field == 'sales_order_id' else 'supplier_id'
            if getattr(source, party) != getattr(obj, party):
                raise ValueError('Pelanggan/supplier berbeda dengan order sumber')
