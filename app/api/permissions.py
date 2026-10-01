"""RBAC v2 — Permission gate yang menggantikan role-set hard-coded lama.

Setiap request dihitung: permission code via access_registry.infer_permission(),
lalu dicek terhadap effective permissions user (union role template + ALLOW − DENY;
super admin short-circuit). Endpoint tak terpetakan → default deny (RBAC-13),
kecuali modul yang ditegakkan di service (workflow).

Logika idempotency & invariant 409 lama DIPERTAHANKAN persis (maker-checker,
rekalkulasi dinonaktifkan, endpoint approve_* lama wajib lewat workflow).
"""
import hashlib
import json
from fastapi import Depends, HTTPException, Request, Header
from loguru import logger

from app.api.deps import get_current_db, get_current_user
from app.services.access_registry import infer_permission
from app.services.access_service import effective_permissions


# Modul yang gate-nya ditegakkan di service (bukan di router).
_SERVICE_ENFORCED = {'workflow'}


def module_access(module: str):
    async def check(request: Request, db=Depends(get_current_db), user=Depends(get_current_user),
                    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
        fn = request.scope['endpoint'].__name__
        method = request.method
        path = str(request.url.path)

        # ── Permission check (RBAC v2) ────────────────────────────────────
        if module in _SERVICE_ENFORCED:
            code = infer_permission(module, fn, method, path)
            if code is None:
                pass  # get_workflow / act → ditegakkan per-dokumen di workflow_service
            else:
                perms, is_super = effective_permissions(db, user)
                if not is_super and code not in perms:
                    raise HTTPException(403, 'Role pengguna tidak memiliki izin untuk aksi ini')
        else:
            code = infer_permission(module, fn, method, path)
            perms, is_super = effective_permissions(db, user)
            if code is None:
                # Endpoint belum dipetakan → default deny (kecuali super admin)
                if not is_super:
                    logger.warning(f"access: unmapped endpoint denied module={module} fn={fn} {method} {path} user={user.username}")
                    raise HTTPException(403, 'Aksi ini belum memiliki mapping izin (default deny)')
            elif not is_super and code not in perms:
                raise HTTPException(403, 'Role pengguna tidak memiliki izin untuk aksi ini')

        # ── Invariant 409 lama (dipertahankan persis) ─────────────────────
        read = method in ('GET', 'HEAD', 'OPTIONS')
        if not read:
            if fn == 'rekalkulasi_stok_kartu':
                raise HTTPException(409, 'Rekalkulasi histori dinonaktifkan; gunakan rekonsiliasi dan penyesuaian yang disetujui')
            # Old stock approval endpoints must not bypass submit + maker/checker.
            if fn in ('approve_penyesuaian', 'approve_pemindahan', 'approve_permintaan', 'finish_pengiriman', 'finish_penerimaan'):
                raise HTTPException(409, 'Gunakan endpoint workflow: submit, approve, lalu execute')
            db.info['request_actor'] = user
            key = request.headers.get('Idempotency-Key')
            # Required for document creates; optional for updates/cancels. Scope per actor.
            required = request.method == 'POST' and fn.startswith('create_') and module in ('penjualan', 'pembelian', 'kas_bank', 'persediaan', 'jurnal', 'pelunasan', 'asset_cycle', 'organisasi')
            if required and not key:
                raise HTTPException(400, 'Header Idempotency-Key wajib diisi untuk membuat dokumen')
            if key:
                if len(key) > 128 or not key.strip():
                    raise HTTPException(400, 'Idempotency-Key harus berisi 1–128 karakter')
                # Update #5: body hanya dibaca untuk request JSON. Request
                # multipart (upload file, mis. import Excel) tidak boleh
                # mengonsumsi stream di sini — stream-nya dibutuhkan parser
                # UploadFile, dan memanggil request.body() lebih dulu
                # memicu RuntimeError "Stream consumed" (500). Digest untuk
                # multipart memakai body kosong.
                if (request.headers.get('content-type') or '').startswith('application/json'):
                    body = await request.body()
                    try:
                        body = json.dumps(json.loads(body), sort_keys=True, separators=(',', ':')).encode() if body else b''
                    except (ValueError, UnicodeDecodeError):
                        pass  # Normal request validation will report malformed JSON.
                else:
                    body = b''
                digest = hashlib.sha256(request.method.encode() + request.url.path.encode() + b'\0' + request.url.query.encode() + b'\0' + body).hexdigest()
                db.info['idempotency'] = (user.id, key, digest)
    return check
