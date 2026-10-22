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

# Endpoint bulk import Excel (multipart, banyak baris per request).
# FIX BUG import master barang: endpoint ini membuat BANYAK baris/dokumen
# dalam SATU request, sedangkan model idempotency AUD-03 adalah satu-key-
# satu-hasil (IdempotentOperation ditegakkan di atomic_accounting_write).
# Bila key didaftarkan untuk bulk import, dokumen turunan baris ke-2 dst.
# (mis. Penyesuaian Stok awal dari kolom "Stok") akan "berhasil" via replay
# hasil baris pertama TANPA benar-benar dibuat — silent data loss.
# Karena itu bulk import:
#   1. TIDAK diwajibkan mengirim Idempotency-Key (bukan dokumen tunggal);
#   2. key yang tetap dikirim klien DIABAIKAN (tidak didaftarkan).
# Anti-duplikasi retry tetap terjaga secara natural: create_master menolak
# kode duplikat (constraint unik + eksplisit) per baris, sehingga import
# ulang hanya menolak baris yang sudah ada — tidak pernah menduplikasi.
_BULK_IMPORT_FNS = {'import_barang', 'import_pelanggan', 'import_supplier', 'import_gudang'}


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
            # AUD-03 (update #04): Idempotency-Key kini wajib untuk SEMUA POST
            # di balik module_access — sebelumnya hanya create_* di 9 modul
            # (coa/master/aset-tetap/penutupan-periode/rekonsiliasi-bank/
            # karyawan/pengguna/workflow tidak diwajibkan). PUT/DELETE tetap
            # opsional (update bersifat idempotent-by-key natural).
            # Pengecualian: POST yang murni komputasi/sinkronisasi read-like,
            # plus bulk import Excel (lihat _BULK_IMPORT_FNS) — import master
            # bukan "pembuatan dokumen" tunggal sehingga di luar cakupan
            # idempotency satu-key-satu-hasil.
            exempt = fn in ('preview_coa', 'sync_kas_bank_akun') or fn in _BULK_IMPORT_FNS
            required = request.method == 'POST' and not exempt
            if required and not key:
                raise HTTPException(400, 'Header Idempotency-Key wajib diisi untuk membuat dokumen')
            # Bulk import: key yang dikirim klien diabaikan total — jangan
            # daftarkan db.info['idempotency'] (lihat catatan _BULK_IMPORT_FNS).
            if fn in _BULK_IMPORT_FNS:
                key = None
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
