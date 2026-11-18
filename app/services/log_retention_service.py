"""=== Update #074 — Lama simpan log aktivitas + salinan CSV otomatis ===

Tabel access_audit_logs bersifat append-only: sekali ditulis, baris audit
tidak pernah diubah atau dihapus secara manual — itulah yang membuatnya
layak dipercaya sebagai jejak audit. Update #074 menambahkan setelan
lama simpan yang bisa diatur Admin IT (Pengaturan ▸ Sistem ▸ Log Aktivitas,
kartu "Lama Simpan Log"): catatan yang lebih tua dari batas dihapus
otomatis setiap hari pukul 02:00 waktu server oleh penjadwal latar
belakang (start_scheduler).

Prinsip "salinan dulu, hapus kemudian": bila salinan otomatis aktif, CSV
salinan SELALU ditulis lebih dulu sebelum baris lama dihapus. Kegagalan
menulis salinan (disk penuh, folder tidak dapat ditulis, dsb.) MEMBATALKAN
penghapusan — catatan tidak pernah hilang tanpa salinan.

SETIAP penghapusan dan perubahan setelan dicatat sebagai baris audit
baru (aksi LOG_PURGE / RETENSI_UBAH) di access_audit_logs, sehingga jejak
penghapusan itu sendiri tetap tercatat: siapa yang menjalankannya, kapan,
berapa catatan yang dihapus, dan di mana salinannya disimpan.

Catatan kepatuhan: pembukuan pajak Indonesia (UU KUP) mewajibkan
penyimpanan dokumen beserta pendukungnya selama 10 tahun. Batas simpan
di database hanyalah jendela query "panas" (demi performa); salinan CSV
di folder logs_archive/ adalah simpanan jangka panjang — disarankan Admin
IT membackup folder tersebut secara rutin ke penyimpanan terpisah.
"""

import csv
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.master.access import AccessAuditLog
from app.models.master.pengguna import Pengguna

# Pilihan retensi (dalam hari) yang diizinkan — 0 berarti "Selamanya".
RETENTION_CHOICES = (0, 30, 60, 90, 180, 365)  # 0 = Selamanya
DEFAULT_RETENTION_DAYS = 180
ARCHIVE_DIR = Path('logs_archive')

# Penjaga idempotensi _ensure_table (double-checked locking) — aman dipanggil
# bersamaan dari banyak request maupun dari thread penjadwal.
_TABLE_LOCK = threading.Lock()
_TABLE_READY = False


def _ensure_table(db: Session) -> None:
    """Buat tabel system_log_retention + index created_at bila belum ada.

    Idempoten dan thread-safe (threading.Lock + flag modul). TANPA alembic —
    tabelnya kecil (satu baris konfigurasi), jadi dibuat otomatis via DDL
    saat pertama kali dipakai; baris id=1 di-INSERT hanya bila belum ada.
    """
    global _TABLE_READY
    if _TABLE_READY:
        return
    with _TABLE_LOCK:
        if _TABLE_READY:
            return
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS system_log_retention (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                retention_days INTEGER NOT NULL DEFAULT 180,
                archive_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                last_purge_at TIMESTAMP,
                last_purge_count INTEGER,
                last_purge_archive VARCHAR(255),
                updated_by VARCHAR(64),
                updated_at TIMESTAMP
            )
        """))
        # Baris tunggal id=1 — INSERT hanya bila belum ada (guard SELECT dulu).
        if db.execute(text('SELECT id FROM system_log_retention WHERE id = 1')).fetchone() is None:
            db.execute(text(
                'INSERT INTO system_log_retention (id, retention_days, archive_enabled) '
                'VALUES (1, :days, TRUE)'
            ), {'days': DEFAULT_RETENTION_DAYS})
        # Index pendukung query cutoff (created_at < ...) supaya penghapusan
        # dan hitung catatan lama tetap cepat walau tabel audit membesar.
        db.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_access_audit_logs_created_at '
            'ON access_audit_logs (created_at)'
        ))
        db.commit()
        _TABLE_READY = True


def _policy_label(days: int) -> str:
    """Label ramah untuk jumlah hari retensi (0 → 'Selamanya')."""
    return 'Selamanya' if days == 0 else f'{days} hari'


def get_policy(db: Session) -> dict:
    """Baca setelan lama simpan aktif (baris tunggal id=1)."""
    _ensure_table(db)
    row = db.execute(text(
        'SELECT retention_days, archive_enabled, last_purge_at, '
        'last_purge_count, last_purge_archive FROM system_log_retention WHERE id = 1'
    )).fetchone()
    return {
        'retentionDays': row[0] if row else DEFAULT_RETENTION_DAYS,
        'archiveEnabled': bool(row[1]) if row else True,
        'lastPurgeAt': row[2] if row else None,
        'lastPurgeCount': row[3] if row else None,
        'lastPurgeArchive': row[4] if row else None,
    }


def set_policy(db: Session, retention_days: int, archive_enabled: bool,
               actor: Optional[Pengguna]) -> dict:
    """Simpan setelan lama simpan baru + catat baris audit RETENSI_UBAH.

    Validasi retention_days harus salah satu RETENTION_CHOICES (else
    ValueError). Kebijakan dan baris audit di-commit sekali; setiap
    perubahan kebijakan otomatis meninggalkan jejak audit.
    """
    if retention_days not in RETENTION_CHOICES:
        raise ValueError(
            f'retentionDays harus salah satu dari: {", ".join(str(c) for c in RETENTION_CHOICES)}'
        )
    _ensure_table(db)
    current = get_policy(db)
    label_lama = _policy_label(current['retentionDays'])
    label_baru = _policy_label(retention_days)
    db.execute(
        text(
            'UPDATE system_log_retention '
            'SET retention_days = :days, archive_enabled = :archive, '
            'updated_by = :updated_by, updated_at = :updated_at '
            'WHERE id = 1'
        ),
        {
            'days': retention_days,
            'archive': archive_enabled,
            'updated_by': str(actor.id) if actor else None,
            'updated_at': datetime.now(),
        },
    )
    if label_lama != label_baru:
        reason = f'Lama simpan log diubah: {label_lama} → {label_baru}'
    else:
        reason = f'Salinan otomatis sebelum hapus: {"dinyalakan" if archive_enabled else "dimatikan"}'
    db.add(AccessAuditLog(
        actor_id=actor.id if actor else None,
        target_user_id=None,
        action='RETENSI_UBAH',
        before={'lamaSimpan': label_lama, 'salinanOtomatis': current['archiveEnabled']},
        after={'lamaSimpan': label_baru, 'salinanOtomatis': archive_enabled},
        reason=reason,
    ))
    db.commit()
    return get_policy(db)


def _write_archive_csv(rows, cutoff: datetime) -> str:
    """Tulis baris audit lama ke file CSV salinan; return NAMA file saja.

    - File: ARCHIVE_DIR / f'arsip-log-aktivitas_{stamp}.csv' dengan stamp
      '%Y%m%d_%H%M%S' waktu server; folder dibuat otomatis bila belum ada.
    - Encoding UTF-8 dengan BOM (utf-8-sig) supaya Excel membuka teks
      Indonesia dengan benar; delimiter ';' (kebiasaan CSV lokal).
    - `rows` adalah iterable AccessAuditLog (pemanggil memakai
      query.yield_per(1000) agar hemat memori); `cutoff` adalah batas usia
      catatan yang disalin.
    - Gagal IO → raise RuntimeError('Gagal membuat salinan CSV: ...') —
      pemanggil MEMBATALKAN penghapusan (salinan dulu, hapus kemudian).
    """
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'arsip-log-aktivitas_{stamp}.csv'
    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        with open(ARCHIVE_DIR / filename, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['id', 'waktu', 'aktor_id', 'aksi', 'target_user_id',
                             'keterangan', 'sebelum', 'sesudah'])
            for r in rows:
                writer.writerow([
                    r.id,
                    r.created_at,
                    r.actor_id,
                    r.action,
                    r.target_user_id,
                    r.reason,
                    json.dumps(r.before, ensure_ascii=False, default=str) if r.before is not None else '',
                    json.dumps(r.after, ensure_ascii=False, default=str) if r.after is not None else '',
                ])
    except OSError as exc:
        raise RuntimeError(f'Gagal membuat salinan CSV: {exc} — penghapusan dibatalkan, tidak ada data yang terhapus') from exc
    return filename


def retention_info(db: Session, user: Pengguna) -> dict:
    """Bentuk lengkap setelan lama simpan untuk endpoint GET /audit-logs/retention."""
    policy = get_policy(db)
    days = policy['retentionDays']
    if days == 0:
        cutoff = None
        old_entries = 0
    else:
        cutoff = datetime.now() - timedelta(days=days)
        old_entries = db.query(AccessAuditLog).filter(AccessAuditLog.created_at < cutoff).count()
    # Jadwal penghapusan berikutnya: 02:00 waktu server (konsisten dengan
    # perhitungan di start_scheduler).
    now = datetime.now()
    next_purge = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if next_purge <= now:
        next_purge = next_purge + timedelta(days=1)
    # Import di dalam fungsi supaya tidak circular dengan modul endpoint.
    from app.services.access_service import effective_permissions
    perms, is_super = effective_permissions(db, user)
    return {
        'retentionDays': days,
        'retentionLabel': _policy_label(days),
        'archiveEnabled': policy['archiveEnabled'],
        'cutoff': cutoff.isoformat() if cutoff else None,
        'oldEntries': old_entries,
        'lastPurgeAt': policy['lastPurgeAt'].isoformat() if policy['lastPurgeAt'] else None,
        'lastPurgeCount': policy['lastPurgeCount'],
        'lastPurgeArchive': policy['lastPurgeArchive'],
        'nextPurgeAt': next_purge.isoformat(),
        'totalEntries': db.query(AccessAuditLog).count(),
        'canManage': is_super or 'system.access.edit' in perms,
    }


def purge_now(db: Session, actor: Optional[Pengguna]) -> dict:
    """Jalankan penghapusan log lama sekali (endpoint manual & scheduler).

    Alur (salinan dulu, hapus kemudian):
    1. Baca setelan; bila "Selamanya" → tidak melakukan apa pun.
    2. cutoff = WAKTU SERVER sekarang dikurangi retention_days
       (datetime.now(), bukan UTC — konsisten dengan created_at yang
       ditulis aplikasi).
    3. Ambil semua baris created_at < cutoff (urut naik, yield_per 1000).
    4. Kosong → selesai tanpa efek samping.
    5. Bila salinan aktif: tulis CSV salinan LEBIH DULU — kegagalannya
       (RuntimeError) merambat dan TIDAK ADA baris yang terhapus
       (endpoint mengubahnya jadi HTTP 500).
    6. Hapus per batch 500 id (synchronize_session=False).
    7. Tulis baris audit LOG_PURGE + update statistik kebijakan
       (last_purge_*), lalu commit sekali.

    Catatan: bila commit gagal SETELAH arsip tertulis, file arsip tetap
    ada — kalah redundan lebih baik daripada data hilang.
    """
    policy = get_policy(db)
    if policy['retentionDays'] == 0:
        return {'deleted': 0, 'cutoff': None, 'archive': None, 'skipped': 'SELAMANYA'}
    cutoff = datetime.now() - timedelta(days=policy['retentionDays'])
    query = (
        db.query(AccessAuditLog)
        .filter(AccessAuditLog.created_at < cutoff)
        .order_by(AccessAuditLog.created_at.asc())
        .yield_per(1000)
    )
    rows = list(query)
    if not rows:
        return {'deleted': 0, 'cutoff': cutoff.isoformat(), 'archive': None, 'skipped': None}
    archive_name = None
    if policy['archiveEnabled']:
        # Kegagalan menulis arsip → RuntimeError merambat; sampai titik ini
        # belum ada satu baris pun yang dihapus.
        archive_name = _write_archive_csv(rows, cutoff)
    ids = [r.id for r in rows]
    deleted = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        deleted += db.query(AccessAuditLog).filter(
            AccessAuditLog.id.in_(chunk)
        ).delete(synchronize_session=False)
    label = _policy_label(policy['retentionDays'])
    reason = (f'Hapus log lama: {deleted} catatan lebih lama dari '
              f'{cutoff.strftime("%d/%m/%Y")} sudah dihapus')
    if archive_name:
        reason += f' — salinannya disimpan di {archive_name}'
    # Purge terjadwal otomatis memakai actor=None → di halaman log tampil
    # sebagai "Sistem".
    db.add(AccessAuditLog(
        actor_id=actor.id if actor else None,
        target_user_id=None,
        action='LOG_PURGE',
        before=None,
        after={
            'lamaSimpan': label,
            'batasTanggal': cutoff.isoformat(),
            'jumlahDihapus': deleted,
            'salinan': archive_name,
        },
        reason=reason,
    ))
    db.execute(
        text(
            'UPDATE system_log_retention '
            'SET last_purge_at = :ts, last_purge_count = :n, last_purge_archive = :archive '
            'WHERE id = 1'
        ),
        {'ts': datetime.now(), 'n': deleted, 'archive': archive_name},
    )
    db.commit()
    return {'deleted': deleted, 'cutoff': cutoff.isoformat(), 'archive': archive_name, 'skipped': None}


def start_scheduler(logger) -> threading.Thread:
    """Mulai daemon thread 'log-retention' — hapus log lama tiap hari 02:00 waktu server.

    Thread tidur sampai pukul 02:00 berikutnya lalu memanggil
    run_scheduled_purge() dalam try/except — kegagalan dicatat via logger
    loguru (gaya placeholder {}) dan TIDAK pernah mematikan thread maupun
    aplikasi. Return thread yang baru dimulai (daemon=True — ikut berakhir
    bersama proses).
    """
    def _loop() -> None:
        while True:
            now = datetime.now()
            next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run = next_run + timedelta(days=1)
            time.sleep((next_run - now).total_seconds())
            try:
                result = run_scheduled_purge()
                if result:
                    logger.info('Hapus log terjadwal: {} catatan dihapus', result['deleted'])
            except Exception as exc:  # noqa: BLE001 — jangan matikan thread penjadwal
                logger.warning('Hapus log terjadwal gagal: {}', exc)

    thread = threading.Thread(target=_loop, name='log-retention', daemon=True)
    thread.start()
    return thread


def run_scheduled_purge() -> Optional[dict]:
    """Pembersihan terjadwal — buka session sendiri (SessionLocal), dipanggil scheduler.

    - Kebijakan "Selamanya" → return None (skip senyap).
    - Aman dipanggil ulang (idempoten): purge kedua dalam hari yang sama
      menemukan 0 baris dan tidak melakukan apa-apa.
    - Catatan: mode --reload uvicorn (dev) bisa memunculkan lebih dari
      satu thread penjadwal — tidak berbahaya karena purge idempoten;
      di produksi (single worker) hanya ada satu.
    """
    with SessionLocal() as db:
        policy = get_policy(db)
        if policy['retentionDays'] == 0:
            from loguru import logger
            logger.info('Lama simpan log "Selamanya" — hapus terjadwal dilewati')
            return None
        return purge_now(db, actor=None)
