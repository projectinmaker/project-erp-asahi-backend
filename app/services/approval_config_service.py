"""=== Update #071 — service konfigurasi persetujuan per jenis dokumen ===

Sumber tunggal kebenaran saklar maker-checker (Preferensi ▸ Persetujuan).
Dipakai workflow_service untuk memutuskan: dokumen langsung final
(auto-final) atau menunggu approver, serta siapa yang berwenang menyetujui.

Aturan (hasil diskusi Update #071):
- Persetujuan 1 tahap tanpa syarat nominal: aktif → dokumen menunggu
  PENDING; sekali approve → final + posting/eksekusi otomatis.
- Non-aktif (default untuk semua jenis) → dokumen langsung final.
- Approver per jenis dokumen: daftar user dan/atau role; salah satu
  menyetujui = selesai. SUPER_ADMIN selalu boleh (fallback, di
  workflow_service).
- Mengaktifkan mensyaratkan minimal 1 approver.
- Dokumen yang sedang menunggu TIDAK difinalisasi otomatis saat aturan
  diubah/dimatikan (hanya dokumen BARU yang mengikuti aturan baru).
- Authorization approve/reject untuk jenis AKTIF mengikuti daftar approver
  (bukan permission RBAC ``*.approve``); untuk jenis NON-AKTIF perilaku
  lama tetap berlaku bagi dokumen lama yang masih menggantung (RBAC).
"""
from fastapi import HTTPException
from sqlalchemy import func

from app.models import ApprovalConfig, ApprovalApprover, Pengguna, Role
from app.services.access_service import _user_role_codes


# Jenis dokumen yang diekspos konfigurasi — HANYA yang daftar modulnya
# sudah punya kolom "Persetujuan" (frontend). Urutan = urutan tampilan UI.
SUPPORTED_KINDS: dict[str, dict] = {
    # Kas & Bank
    'pembayaran_kas': {'label': 'Pembayaran Kas', 'moduleGroup': 'Kas & Bank'},
    'penerimaan_kas': {'label': 'Penerimaan Kas', 'moduleGroup': 'Kas & Bank'},
    'transfer_bank': {'label': 'Transfer Bank', 'moduleGroup': 'Kas & Bank'},
    'voucher_memorial': {'label': 'Voucher Memorial', 'moduleGroup': 'Kas & Bank'},
    # Persediaan
    'penyesuaian_stok': {'label': 'Penyesuaian Stok', 'moduleGroup': 'Persediaan'},
    'pemindahan_barang': {'label': 'Pemindahan Barang', 'moduleGroup': 'Persediaan'},
    'permintaan_barang': {'label': 'Permintaan Barang', 'moduleGroup': 'Persediaan'},
    # Aset Tetap
    'asset_event': {'label': 'Transaksi Aset', 'moduleGroup': 'Aset Tetap'},
    # Buku Besar
    'jurnal_umum': {'label': 'Jurnal Manual', 'moduleGroup': 'Buku Besar'},
}

# Catatan scope: jenis Penjualan/Pembelian (sales_order, purchase_order,
# invoice, retur, pengiriman/penerimaan barang) TIDAK dulu diekspos karena
# daftar modulnya belum punya kolom Persetujuan — dokumennya tetap
# auto-final seperti sejak Update #043. Bila nanti kolomnya ditambahkan,
# cukup mendaftarkan jenisnya di SUPPORTED_KINDS.


def is_enabled(db, kind) -> bool:
    """True bila jenis dokumen ini memerlukan persetujuan approver."""
    row = db.query(ApprovalConfig).filter_by(document_type=kind).first()
    return bool(row and row.enabled)


def enabled_kinds(db) -> list:
    """Daftar jenis dokumen yang persetujuannya aktif (untuk payload izin)."""
    return [r.document_type for r in db.query(ApprovalConfig).filter_by(enabled=True).all()]


def is_configured_approver(db, user, kind) -> bool:
    """True bila user termasuk approver yang dikonfigurasi untuk jenis ini
    (penunjukan langsung by user, atau by role user tersebut)."""
    rows = db.query(ApprovalApprover).filter_by(document_type=kind).all()
    if not rows:
        return False
    for row in rows:
        if row.user_id is not None and str(row.user_id) == str(user.id):
            return True
    role_rows = [r.role_code for r in rows if r.role_code is not None]
    if role_rows:
        codes = _user_role_codes(db, user)
        if not codes:
            # Parity legacy: user tanpa link role — turunkan dari enum lama.
            legacy = getattr(user.role, 'value', user.role)
            codes = [legacy] if legacy else []
        if set(codes) & set(role_rows):
            return True
    return False


def get_config(db) -> list:
    """Konfigurasi lengkap semua jenis dokumen yang didukung (+ approver)."""
    configs = {c.document_type: c for c in db.query(ApprovalConfig).all()}
    approver_rows = db.query(ApprovalApprover).all()
    user_ids = {r.user_id for r in approver_rows if r.user_id is not None}
    users = {}
    if user_ids:
        for u in db.query(Pengguna).filter(Pengguna.id.in_(list(user_ids))).all():
            users[str(u.id)] = u.nama_lengkap
    role_codes = {r.role_code for r in approver_rows if r.role_code is not None}
    roles = {}
    if role_codes:
        for r in db.query(Role).filter(Role.code.in_(list(role_codes))).all():
            roles[r.code] = r.name

    result = []
    for kind, meta in SUPPORTED_KINDS.items():
        cfg = configs.get(kind)
        approvers = [
            {
                'id': str(r.id),
                'userId': str(r.user_id) if r.user_id else None,
                'userName': users.get(str(r.user_id)) if r.user_id else None,
                'roleCode': r.role_code,
                'roleName': roles.get(r.role_code) if r.role_code else None,
            }
            for r in approver_rows if r.document_type == kind
        ]
        # Urutan tampilan: user dulu, lalu role.
        approvers.sort(key=lambda a: (a['userId'] is None, a['userName'] or a['roleName'] or ''))
        result.append({
            'documentType': kind,
            'label': meta['label'],
            'moduleGroup': meta['moduleGroup'],
            'enabled': bool(cfg and cfg.enabled),
            'approvers': approvers,
        })
    return result


def set_config(db, kind, enabled, approvers, user) -> dict:
    """Simpan konfigurasi satu jenis dokumen (replace approver).

    Validasi:
    - kind harus jenis yang didukung (punya kolom Persetujuan di UI).
    - enabled=True mensyaratkan minimal 1 approver (dokumen tidak boleh
      menggantung tanpa berpengawai).
    - user_id / role_code harus valid & aktif; penunjuk duplikat dibuang.
    """
    if kind not in SUPPORTED_KINDS:
        raise HTTPException(400, f"Jenis dokumen '{kind}' tidak didukung konfigurasi persetujuan")

    clean: list = []
    seen_users: set = set()
    seen_roles: set = set()
    for entry in approvers or []:
        user_id = entry.get('userId') or entry.get('user_id')
        role_code = entry.get('roleCode') or entry.get('role_code')
        if bool(user_id) == bool(role_code):
            raise HTTPException(400, 'Setiap approver harus berupa userId ATAU roleCode (salah satu saja)')
        if user_id:
            if user_id in seen_users:
                continue
            if db.get(Pengguna, user_id) is None:
                raise HTTPException(400, 'Approver user tidak ditemukan')
            seen_users.add(user_id)
            clean.append({'user_id': user_id, 'role_code': None})
        else:
            if role_code in seen_roles:
                continue
            if db.query(Role).filter_by(code=role_code, status='AKTIF').first() is None:
                raise HTTPException(400, f"Role approver '{role_code}' tidak ditemukan / tidak aktif")
            seen_roles.add(role_code)
            clean.append({'user_id': None, 'role_code': role_code})

    if enabled and not clean:
        raise HTTPException(400, 'Aktifkan persetujuan minimal dengan 1 approver (user atau role) — dokumen tidak boleh menggantung tanpa berpengawai')

    cfg = db.query(ApprovalConfig).filter_by(document_type=kind).first()
    if cfg is None:
        cfg = ApprovalConfig(document_type=kind, enabled=False)
        db.add(cfg)
    cfg.enabled = bool(enabled)
    cfg.updated_by = user.id

    # Replace penunjuk approver untuk jenis ini.
    db.query(ApprovalApprover).filter_by(document_type=kind).delete()
    for item in clean:
        db.add(ApprovalApprover(document_type=kind, user_id=item['user_id'], role_code=item['role_code']))
    db.flush()
    return {'documentType': kind, 'enabled': cfg.enabled, 'approverCount': len(clean)}


def approver_summary(db, kind) -> str:
    """Ringkasan approver untuk keperluan log/teks (opsional)."""
    rows = db.query(ApprovalApprover).filter_by(document_type=kind).all()
    parts = []
    for r in rows:
        if r.user_id is not None:
            u = db.get(Pengguna, r.user_id)
            parts.append(u.nama_lengkap if u else str(r.user_id))
        else:
            role = db.query(Role).filter_by(code=r.role_code).first()
            parts.append(role.name if role else r.role_code)
    return ', '.join(parts)
