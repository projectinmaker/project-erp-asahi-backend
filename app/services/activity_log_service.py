"""
=== Update #073 — Audit Aktivitas ERP otomatis (semua modul transaksi) ===

Menjawab: "aktivitas apa yang dilakukan user/pengguna di ERP belum ada ya?"
Benar — sebelum revisi ini tabel access_audit_logs hanya terisi event login,
manajemen pengguna, dan perubahan akses/RBAC. Aktivitas transaksi ERP
(membuat voucher, invoice, PO, jurnal, master data, persetujuan, dll.)
TIDAK tercatat sama sekali.

Service ini dipanggil dari middleware di app/main.py untuk SETIAP permintaan
yang mengubah data (POST/PUT/PATCH/DELETE) ke modul-modul ERP, lalu
menuliskannya ke tabel access_audit_logs — tabel yang sama yang dibaca
halaman Pengaturan ▸ Sistem ▸ Log Aktivitas — sehingga langsung tampil
tanpa migrasi database dan tanpa perubahan izin.

Kode aksi baru (kategori "Aktivitas ERP" di frontend):
  ERP_BUAT    — membuat data/dokumen (POST sukses, import, sync, apply)
  ERP_UBAH    — mengubah data (PUT/PATCH sukses, atur saldo awal)
  ERP_HAPUS   — menghapus data (DELETE sukses, aksi .../hapus)
  ERP_PROSES  — aksi proses lain (setujui, tolak, batalkan, selesaikan,
                posting, reverse, konversi, tutup/buka periode, dsb.)
  ERP_DITOLAK — percobaan yang ditolak sistem (HTTP 401/403/5xx) — sinyal
                keamanan: siapa mencoba melakukan apa yang tidak diizinkan.

Prinsip desain:
- TIDAK menyimpan body request — password, token, nilai transaksi, dan
  catatan internal tidak pernah masuk log; hanya metode + path + status
  HTTP + label ramah (modul/dokumen/kata kerja).
- Best-effort: kegagalan menulis log TIDAK boleh menjatuhkan request utama.
- Modul yang sudah punya audit semantik sendiri (auth, access/RBAC,
  pengguna) dikecualikan supaya tidak dobel catat.
- HTTP 400/404/422 dilewati (kebisingan validasi form yang normal);
  hanya sukses (2xx/3xx) dan kegagalan yang relevan keamanan yang dicatat.
"""

from typing import Optional, Tuple
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.master.access import AccessAuditLog
from app.models.master.pengguna import Pengguna
from app.utils.auth import verify_token

_API_PREFIX = '/api/v1/'

# Modul yang sudah menulis audit semantiknya sendiri (before/after kaya
# konteks) di kode endpoint — mencatatnya lagi di middleware hanya
# menghasilkan duplikat:
#   auth     → LOGIN / LOGIN_GAGAL            (endpoints/auth.py)
#   access   → CREATE_ROLE / UPDATE_ROLE / UPDATE_USER_ACCESS (access.py)
#   pengguna → CREATE_USER … DELETE_USER      (pengguna.py)
_EXCLUDED_MODULES = ('auth', 'access', 'pengguna')

# Endpoint POST yang benar-benar baca-saja (tidak mengubah data apa pun).
_READONLY_POST_PATHS = {'coa/preview'}

# ─── Label (modul, dokumen) per prefix path ─────────────────────────────────
# Dicocokkan dari awal sub-path dengan batas segmen (prefix 'master/barang'
# TIDAK cocok dengan 'master/barang-satuan'). Urutan: paling spesifik dulu.
_ROUTE_LABELS: Tuple[Tuple[str, str, str], ...] = (
    # ── Kas & Bank (router kas_bank + rekonsiliasi_bank, sama-sama /kas-bank)
    ('kas-bank/pembayaran', 'Kas & Bank', 'Voucher Pembayaran Kas'),
    ('kas-bank/penerimaan', 'Kas & Bank', 'Voucher Penerimaan Kas'),
    ('kas-bank/transfer', 'Kas & Bank', 'Transfer Bank'),
    ('kas-bank/voucher-memorial', 'Kas & Bank', 'Voucher Memorial'),
    ('kas-bank/rekonsiliasi-bank', 'Kas & Bank', 'Rekonsiliasi Bank'),
    ('kas-bank', 'Kas & Bank', 'Dokumen Kas & Bank'),
    # ── Penjualan
    ('penjualan/sales-order', 'Penjualan', 'Sales Order'),
    ('penjualan/sales-invoice', 'Penjualan', 'Invoice Penjualan'),
    ('penjualan/sales-retur', 'Penjualan', 'Retur Penjualan'),
    ('penjualan/pengiriman', 'Penjualan', 'Pengiriman Barang'),
    ('penjualan/penawaran', 'Penjualan', 'Penawaran'),
    ('penjualan/tukar-faktur', 'Penjualan', 'Tukar Faktur'),
    # ── Pembelian
    ('pembelian/purchase-order', 'Pembelian', 'Purchase Order'),
    ('pembelian/purchase-invoice', 'Pembelian', 'Invoice Pembelian'),
    ('pembelian/purchase-retur', 'Pembelian', 'Retur Pembelian'),
    ('pembelian/penerimaan', 'Pembelian', 'Penerimaan Barang'),
    ('pembelian/rop-item', 'Pembelian', 'Draft PO (ROP)'),
    ('pembelian/part-stok-menipis', 'Pembelian', 'Draft PO (Part Stok Menipis)'),
    # ── Persediaan
    ('persediaan/penyesuaian-stok', 'Persediaan', 'Penyesuaian Stok'),
    ('persediaan/pemindahan', 'Persediaan', 'Pemindahan Barang'),
    ('persediaan/permintaan', 'Persediaan', 'Permintaan Barang'),
    ('stok-kartu', 'Persediaan', 'Kartu Stok'),
    # ── Aset
    ('aset-tetap', 'Aset Tetap', 'Aset Tetap'),
    ('aset-transaksi', 'Siklus Aset', 'Transaksi Aset'),
    # ── Buku Besar
    ('jurnal', 'Buku Besar', 'Jurnal Umum Manual'),
    # ── Akun Perkiraan (COA)
    ('coa/saldo-awal', 'Akun Perkiraan', 'Akun COA'),
    ('coa/migration', 'Akun Perkiraan', 'Migrasi COA'),
    ('coa', 'Akun Perkiraan', 'Akun Perkiraan (COA)'),
    # ── Master Data & Pengaturan (router master)
    ('master/pelanggan-from-coa', 'Master Data', 'Pelanggan (dari COA)'),
    ('master/pelanggan', 'Master Data', 'Pelanggan'),
    ('master/supplier-from-coa', 'Master Data', 'Supplier (dari COA)'),
    ('master/supplier', 'Master Data', 'Supplier'),
    ('master/barang-satuan', 'Master Data', 'Satuan Barang'),
    ('master/barang', 'Master Data', 'Barang'),
    ('master/gudang', 'Master Data', 'Gudang'),
    ('master/syarat-bayar', 'Master Data', 'Syarat Bayar'),
    ('master/kategori-aset', 'Master Data', 'Kategori Aset'),
    ('master/kategori-barang', 'Master Data', 'Kategori Barang'),
    ('master/kas-bank-akun', 'Master Data', 'Akun Kas & Bank'),
    ('master/satuan', 'Master Data', 'Satuan'),
    ('master/mata-uang', 'Master Data', 'Mata Uang'),
    ('master/alamat-pengiriman', 'Master Data', 'Alamat Pengiriman'),
    ('master/rekening-bank', 'Master Data', 'Rekening Bank'),
    ('master/coa', 'Master Data', 'Akun COA'),
    ('master/setting-akun', 'Pengaturan', 'Setting Akun'),
    ('master/app-setting', 'Pengaturan', 'Pengaturan Aplikasi'),
    ('master/preferensi', 'Pengaturan', 'Preferensi'),
    ('master/company-profile', 'Pengaturan', 'Profil Perusahaan'),
    # ── Karyawan
    ('karyawan', 'Karyawan', 'Data Karyawan'),
    # ── Workflow (selain approval-config, ditangani khusus di _label_for)
    ('workflow/approval-config', 'Persetujuan Dokumen', 'Konfigurasi Persetujuan'),
    # ── Penutupan Periode
    ('periode/penutupan-periode', 'Penutupan Periode', 'Periode Akuntansi'),
    # ── Produksi
    ('produksi/request-order-part', 'Produksi', 'Request Order Part'),
)

# Fallback bila prefix pertama dikenal sebagai modul tapi sub-path-nya belum
# terpetakan (endpoint baru di masa depan) — log tetap tercatat dengan label
# modul yang benar.
_MODULE_FALLBACK: Tuple[Tuple[str, str, str], ...] = (
    ('penjualan', 'Penjualan', 'Dokumen Penjualan'),
    ('pembelian', 'Pembelian', 'Dokumen Pembelian'),
    ('persediaan', 'Persediaan', 'Dokumen Persediaan'),
    ('master', 'Master Data', 'Master Data'),
    ('workflow', 'Persetujuan Dokumen', 'Dokumen'),
    ('periode', 'Penutupan Periode', 'Periode Akuntansi'),
    ('produksi', 'Produksi', 'Dokumen Produksi'),
    ('pelunasan', 'Pelunasan', 'Pelunasan Dokumen'),
)

# Tipe dokumen workflow (segmen ke-2 path /workflow/{document_type}/…)
# dipetakan ke label dokumen yang sama dengan vocabulary modulnya.
_DOC_TYPE_LABELS = {
    'sales-order': 'Sales Order',
    'sales-invoice': 'Invoice Penjualan',
    'sales-retur': 'Retur Penjualan',
    'pengiriman': 'Pengiriman Barang',
    'penawaran': 'Penawaran',
    'tukar-faktur': 'Tukar Faktur',
    'purchase-order': 'Purchase Order',
    'purchase-invoice': 'Invoice Pembelian',
    'purchase-retur': 'Retur Pembelian',
    'penerimaan': 'Penerimaan Barang',
    'pembayaran': 'Voucher Pembayaran Kas',
    'penerimaan-kas': 'Voucher Penerimaan Kas',
    'transfer': 'Transfer Bank',
    'voucher-memorial': 'Voucher Memorial',
    'penyesuaian-stok': 'Penyesuaian Stok',
    'pemindahan': 'Pemindahan Barang',
    'permintaan': 'Permintaan Barang',
    'jurnal-manual': 'Jurnal Umum Manual',
    'aset-tetap': 'Aset Tetap',
}

# ─── Kata kerja dari segmen path terakhir ───────────────────────────────────
# Segmen terakhir (lalu kedua-terakhir, untuk path berakhir parameter seperti
# /tutup-tahun/{tahun}) dicocokkan ke kata kerja ramah. Aksi workflow yang
# diizinkan endpoint: submit/approve/reject/withdraw/post/execute/cancel.
_SPECIAL_VERBS = {
    'approve': 'Menyetujui',
    'reject': 'Menolak',
    'submit': 'Mengajukan',
    'withdraw': 'Menarik',
    'post': 'Memposting',
    'execute': 'Mengeksekusi',
    'cancel': 'Membatalkan',
    'batal': 'Membatalkan',
    'selesaikan': 'Menyelesaikan',
    'selesai': 'Menyelesaikan',
    'reverse': 'Membalik Jurnal',
    'aktifkan': 'Mengaktifkan',
    'perbaikan': 'Memperbaiki',
    'hapus': 'Menghapus',
    'buat': 'Membuat',
    'revisi': 'Merevisi',
    'to-sales-order': 'Mengonversi ke Sales Order',
    'terima-pembayaran': 'Menerima Pembayaran',
    'tutup-tahun': 'Menutup Tahun',
    'buka-tahun': 'Membuka Tahun',
    'tutup': 'Menutup',
    'buka': 'Membuka',
    'sync': 'Menyinkronkan',
    'apply': 'Menerapkan',
    'import': 'Mengimpor',
    'rekalkulasi': 'Merekalkulasi',
    'saldo-awal': 'Mengatur Saldo Awal',
}

# Kata kerja default per HTTP method (path tanpa segmen kata kerja khusus).
_METHOD_VERBS = {'POST': 'Membuat', 'PUT': 'Mengubah', 'PATCH': 'Mengubah', 'DELETE': 'Menghapus'}

# Kata kerja → kode aksi. Kata kerja khusus lainnya (setujui, tolak,
# batalkan, selesaikan, posting, dsb.) jatuh ke ERP_PROSES.
_VERB_ACTIONS = {
    'Membuat': 'ERP_BUAT',
    'Mengimpor': 'ERP_BUAT',
    'Menyinkronkan': 'ERP_BUAT',
    'Menerapkan': 'ERP_BUAT',
    'Menerima Pembayaran': 'ERP_BUAT',
    'Mengubah': 'ERP_UBAH',
    'Mengatur Saldo Awal': 'ERP_UBAH',
    'Menghapus': 'ERP_HAPUS',
}


def _label_for(sub: str) -> Tuple[str, str]:
    """(modul, dokumen) dari sub-path setelah /api/v1/ (tanpa trailing slash)."""
    segments = sub.split('/')
    # Workflow: /workflow/{document_type}/{id}/{action} — label dokumen
    # diambil dari tipe dokumennya (mis. sales-invoice → Invoice Penjualan).
    if segments[0] == 'workflow' and len(segments) >= 2 and segments[1] != 'approval-config':
        dok = _DOC_TYPE_LABELS.get(segments[1], (segments[1].replace('-', ' ').title() or 'Dokumen'))
        return 'Persetujuan Dokumen', dok
    # Pelunasan: /pelunasan/{jenis}/… — AR (piutang) / AP (hutang).
    if segments[0] == 'pelunasan' and len(segments) >= 2:
        jenis = segments[1].upper()
        if jenis == 'AR':
            return 'Pelunasan', 'Pelunasan Piutang (AR)'
        if jenis == 'AP':
            return 'Pelunasan', 'Pelunasan Hutang (AP)'
    for prefix, modul, dokumen in _ROUTE_LABELS:
        if sub == prefix or sub.startswith(prefix + '/'):
            return modul, dokumen
    for prefix, modul, dokumen in _MODULE_FALLBACK:
        if sub == prefix or sub.startswith(prefix + '/'):
            return modul, dokumen
    # Prefix tak dikenal (endpoint baru tanpa pemetaan) — tetap tercatat.
    return 'ERP', (segments[-1].replace('-', ' ').title() or 'Dokumen')


def _verb_for(method: str, sub: str) -> Tuple[str, str]:
    """(kata kerja, kode aksi) — kata kerja khusus dari segmen akhir path,
    jika ada; selain itu default per HTTP method."""
    segments = sub.split('/')
    # Cek segmen terakhir lalu kedua-terakhir (path bisa berakhir parameter:
    # /tutup-tahun/{tahun} → kata kerja ada di kedua-terakhir).
    for seg in (segments[-1], segments[-2] if len(segments) >= 2 else ''):
        verb = _SPECIAL_VERBS.get(seg.lower())
        if verb:
            return verb, _VERB_ACTIONS.get(verb, 'ERP_PROSES')
    verb = _METHOD_VERBS.get(method, 'Memproses')
    return verb, _VERB_ACTIONS[verb]


def _actor_id(request: Request, db: Session) -> Optional[UUID]:
    """ID pengguna pemilik token Bearer (None bila tanpa/invalid token atau
    penggunanya sudah tidak terdaftar). Pengguna nonaktif tetap diidentifikasi
    — justru percobaan mereka yang menarik untuk audit."""
    header = request.headers.get('authorization') or ''
    if not header.lower().startswith('bearer '):
        return None
    try:
        payload = verify_token(header[7:].strip())
        uid = payload.get('id')
    except Exception:  # noqa: BLE001 — token invalid/kadaluarsa → aktor tak dikenal
        return None
    if not uid:
        return None
    try:
        return uid if db.get(Pengguna, uid) is not None else None
    except Exception:  # noqa: BLE001
        return None


def _log_activity(request: Request, status_code: int) -> None:
    """Inti pencatatan — HANYA dipanggil via maybe_log_activity()."""
    if request.method not in _METHOD_VERBS:
        return
    path = request.url.path
    if not path.startswith(_API_PREFIX):
        return
    sub = path[len(_API_PREFIX):].rstrip('/')
    if not sub:
        return
    if sub.split('/', 1)[0] in _EXCLUDED_MODULES:
        return
    if sub in _READONLY_POST_PATHS:
        return

    # 400/404/422 = kebisingan validasi form normal — dilewati. Sukses
    # (2xx/3xx) dicatat sebagai aktivitas; 401/403/5xx dicatat sebagai
    # percobaan yang DITOLAK (sinyal keamanan).
    success = 200 <= status_code < 400
    denied = status_code in (401, 403) or status_code >= 500
    if not (success or denied):
        return

    modul, dokumen = _label_for(sub)
    verb, action = _verb_for(request.method, sub)
    if denied:
        action = 'ERP_DITOLAK'
        reason = f'DITOLAK (HTTP {status_code}): {verb} {dokumen} — modul {modul}'
    else:
        reason = f'{verb} {dokumen} — modul {modul}'

    with SessionLocal() as db:
        db.add(AccessAuditLog(
            actor_id=_actor_id(request, db),
            target_user_id=None,  # target aktivitas ERP adalah dokumen, bukan pengguna
            action=action,
            before=None,
            after={
                'aktivitas': verb,
                'modul': modul,
                'dokumen': dokumen,
                'metode': request.method,
                'path': path,
                'status': status_code,
            },
            reason=reason,
        ))
        db.commit()


def maybe_log_activity(request: Request, status_code: int) -> None:
    """Entry point dari middleware — best-effort, tidak pernah melempar
    exception supaya request utama pengguna tidak pernah gagal karena
    kegagalan pencatatan audit."""
    try:
        _log_activity(request, status_code)
    except Exception:  # noqa: BLE001
        from loguru import logger
        logger.warning('Gagal menulis audit aktivitas ERP untuk {}', request.url.path)
