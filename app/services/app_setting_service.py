"""Service pengaturan aplikasi global (tabel app_setting).

Saat ini menampung METODE_VALUASI — metode valuasi persediaan GLOBAL yang
dipindahkan dari form per-barang ke halaman Setting Akun. Seluruh engine
valuasi (warehouse/stok/persediaan) membaca nilai dari sini.

Caching per-process mengikuti pola setting_akun_service; cache dibersihkan
setiap kali nilai diubah lewat set_metode_valuasi().
"""

from sqlalchemy.orm import Session

from app.models.master.app_setting import AppSetting

# Kunci yang dikenal
KEY_METODE_VALUASI = "METODE_VALUASI"

# Nilai valid untuk metode valuasi (samakan dengan enum MetodeValuasi)
VALID_METODE_VALUASI = ("AVERAGE", "FIFO", "FEFO")
DEFAULT_METODE_VALUASI = "AVERAGE"

# Label deskriptif per key — dipakai pesan error/endpoint
KNOWN_APP_SETTING_LABELS = {
    KEY_METODE_VALUASI: "Metode Valuasi Persediaan",
}

# Simple in-memory cache (per process lifecycle) — pola setting_akun_service
_cache: dict[str, str] = {}


def _load_all_settings(db: Session) -> None:
    global _cache
    rows = db.query(AppSetting).all()
    _cache = {r.key: r.value for r in rows}


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    """Ambil nilai setting global berdasarkan key (default bila belum ada)."""
    if not _cache:
        _load_all_settings(db)
    return _cache.get(key, default)


def clear_cache() -> None:
    """Clear cache (dipanggil setelah update setting)."""
    global _cache
    _cache = {}


def get_metode_valuasi(db: Session) -> str:
    """Metode valuasi persediaan global ('AVERAGE' | 'FIFO' | 'FEFO').

    Return default AVERAGE bila setting belum dikonfigurasi atau berisi
    nilai tidak dikenal — supaya engine selalu punya metode yang valid.
    """
    value = get_setting(db, KEY_METODE_VALUASI, DEFAULT_METODE_VALUASI)
    return value if value in VALID_METODE_VALUASI else DEFAULT_METODE_VALUASI


def set_metode_valuasi(db: Session, value: str) -> AppSetting:
    """Simpan metode valuasi global. Raise ValueError bila nilai tidak valid."""
    if value not in VALID_METODE_VALUASI:
        raise ValueError(f"Metode valuasi harus salah satu dari: {', '.join(VALID_METODE_VALUASI)}")
    item = db.query(AppSetting).filter(AppSetting.key == KEY_METODE_VALUASI).first()
    if item:
        item.value = value
    else:
        item = AppSetting(key=KEY_METODE_VALUASI, value=value)
        db.add(item)
    db.commit()
    db.refresh(item)
    clear_cache()
    return item
