"""One-level maker/checker workflow. All writes lock the source document first.

RBAC v2: gate aksi berbasis permission `module.resource.action` (revisi role user).
Aturan bisnis (maker-checker, status, period lock) TETAP ditegakkan di atasnya.

Revisi pemilik (21-i): SUPER_ADMIN dibebaskan dari maker-checker — boleh
menyetujui dokumen yang dibuat/diajukannya sendiri agar bisa full akses solo.
Role lain tetap terikat RBAC-07 (checker ≠ maker).

Revisi tim akuntansi (28): role ADMINISTRATOR bebas dari LANGKAH approval —
dokumen yang dibuat administrator langsung difinalisasi otomatis
(ajukan → setujui → posting/eksekusi dalam satu transaksi) via direct_complete().
Jejak audit tetap tercatat lengkap di workflow events.
"""
from fastapi import HTTPException
from app.models import (
    SalesInvoice, PurchaseInvoice, SalesRetur, PurchaseRetur, PembayaranKas, PenerimaanKas,
    AssetEvent, TransferBank, SalesOrder, PurchaseOrder, PengirimanBarang, PenerimaanBarang,
    PenyesuaianStok, PemindahanBarang, PermintaanBarang, JurnalUmum,
)
from app.models.transaksi.workflow import DocumentWorkflow, WorkflowEvent
from app.services.accounting_control import atomic_accounting_write
from app.services.access_registry import KIND_RESOURCE
from app.services.access_service import effective_permissions

# Kompabilitas lama — modul lain masih mengimpor konstanta ini (jangan dihapus dulu).
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

# Dokumen yang didukung aksi POST (posting jurnal) dan EXECUTE (finalisasi stok/retur).
# Mencegah KeyError saat tombol post/execute dipanggil untuk jenis dokumen tanpa handler
# (bug: tombol "Eksekusi" muncul di pembayaran_kas padahal kas&bank hanya punya alur post).
POSTABLE = {
    'sales_invoice', 'sales_retur', 'purchase_invoice', 'purchase_retur',
    'pembayaran_kas', 'penerimaan_kas', 'transfer_bank', 'jurnal_umum', 'asset_event',
}
EXECUTABLE = {
    'pengiriman_barang', 'penerimaan_barang', 'penyesuaian_stok', 'pemindahan_barang',
    'permintaan_barang', 'sales_retur', 'purchase_retur',
}


def role(user):
    return getattr(user.role, 'value', user.role)


# Role yang bebas dari langkah approval (revisi tim akuntansi — khusus administrator).
ADMIN_BYPASS_ROLE_CODES = {'SUPER_ADMIN', 'ADMINISTRATOR'}


def is_administrator(db, user) -> bool:
    """True bila user ber-role Administrator/Super Admin (link RBAC v2 atau enum lama).

    Hanya role inilah yang dokumennya langsung difinalisasi tanpa langkah
    ajukan/setujui/posting. Role lain tetap menjalankan maker-checker penuh.
    """
    from app.services.access_service import _user_role_codes
    codes = _user_role_codes(db, user)
    # Kompatibilitas lintas versi access_service: versi tertentu mengembalikan
    # Set[str], versi lain (RBAC v2 asli) mengembalikan list — normalisasi
    # ke set agar operasi irisan (&) selalu valid (fix TypeError list & set).
    if not isinstance(codes, (set, frozenset)):
        codes = set(codes or ())
    if codes & ADMIN_BYPASS_ROLE_CODES:
        return True
    # Fallback parity: user lama tanpa link role — turunkan dari enum role.
    return role(user) == 'ADMINISTRATOR'


def _perms(db, user):
    """(set permission code, is_super) — resolve per request (RBAC-09: langsung efektif)."""
    return effective_permissions(db, user)


def _has(db, user, resource: str, action: str) -> bool:
    perms, is_super = _perms(db, user)
    return is_super or f"{resource}.{action}" in perms


def can_read(db, user, kind):
    resource = KIND_RESOURCE.get(kind)
    if not resource:
        return False
    return _has(db, user, resource, 'view')


def can_make(db, user, kind):
    resource = KIND_RESOURCE.get(kind)
    if not resource:
        return False
    return _has(db, user, resource, 'create')


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


def available_actions(db, user, obj, wf):
    state = effective_state(obj, wf)
    kind = obj.__tablename__
    resource = KIND_RESOURCE.get(kind)
    result = []
    if not resource:
        return result
    perms, is_super = _perms(db, user)

    def has(action):
        return is_super or f'{resource}.{action}' in perms

    can_approve = has('approve')
    if state in ('DRAFT', 'REJECTED') and has('submit') and (obj.created_by == user.id or can_approve):
        result.append('submit')
    if state == 'PENDING':
        # Akses penuh Super Admin (revisi pemilik): SUPER_ADMIN dibebaskan dari maker-checker
        # sehingga boleh menyetujui/menolak dokumen yang dibuat/diajukan sendiri.
        # Role lain tetap terikat RBAC-07 (checker harus berbeda dari maker).
        if can_approve and (is_super or user.id not in (obj.created_by, wf.submitted_by)):
            result += ['approve', 'reject']
        if user.id == wf.submitted_by:
            result.append('withdraw')
    if state == 'APPROVED':
        if has('post') and kind in POSTABLE:
            result.append('post')
        if has('execute') and kind in EXECUTABLE:
            result.append('execute')
    if kind == 'jurnal_umum' and state != 'CANCELLED' and has('cancel'):
        result.append('cancel')
    if kind in ('sales_retur', 'purchase_retur') and state == 'POSTED' and getattr(obj.status, 'value', obj.status) != 'SELESAI' and has('execute'):
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
        'canEdit': effective_state(obj, wf) in ('DRAFT', 'REJECTED') and can_make(db, user, obj.__tablename__) and (user.id == obj.created_by or _has(db, user, KIND_RESOURCE.get(obj.__tablename__, ''), 'approve')),
        'tanggal': obj.tanggal,
        'total': str(getattr(obj, 'grand_total', getattr(obj, 'total_nilai', getattr(obj, 'nilai_transfer', getattr(obj, 'total_debit', getattr(obj, 'total', 0)))))),
        'state': effective_state(obj, wf), 'documentStatus': getattr(obj.status, 'value', obj.status),
        'version': wf.version if wf else 0,
        'journalId': obj.id if obj.__tablename__ == 'jurnal_umum' else getattr(obj, 'jurnal_umum_id', None),
        'availableActions': available_actions(db, user, obj, wf),
        'history': [{'version': e.version, 'action': e.action, 'fromState': e.from_state,
                     'toState': e.to_state, 'actorId': e.actor_id, 'reason': e.reason, 'at': e.created_at} for e in events],
    }


def direct_complete(db, user, kind, document_id):
    """ADMINISTRATOR bypass: finalisasi dokumen otomatis tanpa langkah approval.

    Dipanggil endpoint create SETELAH dokumen tersimpan (DRAFT). Bila user bukan
    administrator → no-op (workflow normal). Bila administrator → jalankan
    submit → approve → post/execute dalam SATU transaksi (logika identik dengan
    transition(), termasuk seluruh validasi bisnis), lalu catat event workflow
    sehingga jejak audit tetap lengkap.

    Bila finalisasi gagal (mis. validasi posting), hanya transaksi ini yang
    dibatalkan — dokumen tetap DRAFT dan bisa diselesaikan manual lewat tombol.
    """
    # Idempotency-Key sudah dikonsumsi transaksi create; buang agar wrapper tidak
    # menganggap finalisasi ini sebagai replay request yang sama.
    db.info.pop('idempotency', None)
    return _direct_complete_tx(db, user, kind, document_id)


@atomic_accounting_write
def _direct_complete_tx(db, user, kind, document_id):
    if not is_administrator(db, user):
        return None
    obj = get_document(db, kind, document_id, lock=True)
    from app.services.penutupan_periode_service import validate_periode_not_closed
    validate_periode_not_closed(db, obj.tanggal)
    wf = find_workflow(db, obj)
    if wf is None:
        wf = DocumentWorkflow(document_type=kind, document_id=obj.id, state='DRAFT', version=0)
        db.add(wf)
        db.flush()
    if effective_state(obj, wf) != 'DRAFT':
        return obj  # Sudah bergerak/berfinal — jangan paksa (idempotent).
    from app.services.organization_service import validate, for_source
    validate(db, for_source(db, obj.id))

    # 1) Ajukan (DRAFT → PENDING) — logika sama dengan transition('submit').
    if kind == 'asset_event':
        from app.services.asset_cycle_service import prepare
        prepare(db, obj)
    if kind in FINANCIAL - {'jurnal_umum', 'asset_event'}:
        from app.services.document_totals import refresh_totals
        refresh_totals(obj)
        if kind in ('penerimaan_kas', 'pembayaran_kas'):
            from app.services.settlement_service import validate_payment
            validate_payment(db, obj)
    wf.submitted_by = user.id
    wf.approved_by = None
    append_event(db, wf, 'submit', 'PENDING', user.id)

    # 2) Setujui (PENDING → APPROVED). Administrator boleh menyetujui sendiri (21-i).
    wf.approved_by = user.id
    append_event(db, wf, 'approve', 'APPROVED', user.id)

    # 3) Posting/Eksekusi (APPROVED → final). Order berhenti di APPROVED (terminal).
    if kind in ('sales_retur', 'purchase_retur'):
        post_existing(db, obj, user.id)
        append_event(db, wf, 'post', 'POSTED', user.id)
        execute_existing(db, obj)
        append_event(db, wf, 'execute', 'POSTED', user.id)
    elif kind in POSTABLE:
        post_existing(db, obj, user.id)
        append_event(db, wf, 'post', 'POSTED', user.id)
    elif kind in EXECUTABLE:
        execute_existing(db, obj)
        append_event(db, wf, 'execute', 'EXECUTED', user.id)
    db.flush()
    return obj


@atomic_accounting_write
def transition(db, document_type, document_id, action, user, expected_version, reason=None):
    if not can_read(db, user, document_type):
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
    if action not in available_actions(db, user, obj, wf):
        resource = KIND_RESOURCE.get(document_type, '')
        _, is_super = _perms(db, user)
        if action in ('approve', 'reject') and (
            not _has(db, user, resource, 'approve')
            or (user.id in (obj.created_by, wf.submitted_by) and not is_super)
        ):
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
        debit, kredit = validate_entries(db, entries, is_manual=True)
        if debit != obj.total_debit or kredit != obj.total_kredit:
            raise ValueError('Total jurnal draft tidak sesuai detail')
        obj.status = StatusJurnal.POSTED
    elif obj.__tablename__ in ('pembayaran_kas', 'penerimaan_kas'):
        # Deteksi settlement: kalau punya allocation (relasi `alokasi`), pass is_settlement=True
        # supaya RefModule canonical (AR_SETTLEMENT / AP_SETTLEMENT) dipakai.
        is_settlement = bool(getattr(obj, 'alokasi', None))
        methods[obj.__tablename__](db, obj, actor_id, is_settlement=is_settlement)
    else:
        if obj.__tablename__ not in methods:
            raise HTTPException(409, f"Jenis dokumen '{obj.__tablename__}' tidak mendukung aksi posting")
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
        if obj.__tablename__ not in methods:
            raise HTTPException(409, f"Jenis dokumen '{obj.__tablename__}' tidak mendukung aksi eksekusi. Gunakan aksi posting untuk dokumen kas&bank/invoice/jurnal.")
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
