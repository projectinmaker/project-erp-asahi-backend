"""Explicit role gates shared by each API router, including legacy endpoints."""
import hashlib
import json
from fastapi import Depends, HTTPException, Request, Header
from app.api.deps import get_current_db, get_current_user
from app.services.workflow_service import FINANCE, APPROVERS, role


def module_access(module):
    async def check(request: Request, db=Depends(get_current_db), user=Depends(get_current_user),
                    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
        user_role = role(user)
        read = request.method in ('GET', 'HEAD', 'OPTIONS')
        name = request.scope['endpoint'].__name__
        allowed = set(FINANCE)
        if module == 'workflow':
            allowed |= {'STAFF_PENJUALAN', 'STAFF_GUDANG'}
        if module in ('penjualan', 'master', 'stok_kartu'):
            allowed.add('STAFF_PENJUALAN')
        if module in ('persediaan', 'master', 'stok_kartu'):
            allowed.add('STAFF_GUDANG')
        if module == 'penjualan' and 'pengiriman' in name:
            allowed.add('STAFF_GUDANG')
            if not read:
                allowed.discard('STAFF_PENJUALAN')
        if module == 'pembelian' and 'penerimaan' in name:
            allowed.add('STAFF_GUDANG')
        if module == 'pengguna':
            allowed = {'ADMINISTRATOR'}
        elif module == 'karyawan':
            allowed = set(APPROVERS)
        elif not read:
            if module in ('master', 'coa', 'aset_tetap', 'penutupan_periode') or name.startswith(('cancel_', 'void_', 'delete_', 'hapus_')):
                allowed = set(APPROVERS)
            if name in ('complete_rekonsiliasi',):
                allowed = set(APPROVERS)
        if user_role not in allowed:
            raise HTTPException(403, 'Role pengguna tidak memiliki izin untuk aksi ini')
        if not read:
            # Old stock approval endpoints must not bypass submit + maker/checker.
            if name in ('approve_penyesuaian', 'approve_pemindahan', 'approve_permintaan', 'finish_pengiriman', 'finish_penerimaan'):
                raise HTTPException(409, 'Gunakan endpoint workflow: submit, approve, lalu execute')
            db.info['request_actor'] = user
            key = request.headers.get('Idempotency-Key')
            # Required for document creates; optional for updates/cancels. Scope per actor.
            required = request.method == 'POST' and name.startswith('create_') and module in ('penjualan', 'pembelian', 'kas_bank', 'persediaan', 'jurnal')
            if required and not key:
                raise HTTPException(400, 'Header Idempotency-Key wajib diisi untuk membuat dokumen')
            if key:
                if len(key) > 128 or not key.strip():
                    raise HTTPException(400, 'Idempotency-Key harus berisi 1–128 karakter')
                body = await request.body()
                try:
                    body = json.dumps(json.loads(body), sort_keys=True, separators=(',', ':')).encode() if body else b''
                except (ValueError, UnicodeDecodeError):
                    pass  # Normal request validation will report malformed JSON.
                digest = hashlib.sha256(request.method.encode() + request.url.path.encode() + b'\0' + request.url.query.encode() + b'\0' + body).hexdigest()
                db.info['idempotency'] = (user.id, key, digest)
    return check
