"""Service pengaturan aplikasi global (tabel app_setting).

Saat ini menampung METODE_VALUASI — metode valuasi persediaan GLOBAL yang
dipindahkan dari form per-barang ke halaman Setting Akun. Seluruh engine
valuasi (warehouse/stok/persediaan) membaca nilai dari sini.

Caching per-process mengikuti pola setting_akun_service; cache dibersihkan
setiap kali nilai diubah lewat set_metode_valuasi().
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.master.app_setting import AppSetting

# Kunci yang dikenal
KEY_METODE_VALUASI = "METODE_VALUASI"
# === AUD-10 (update #04) — tarif pajak default global ===
# Default rate PPN & PPh 23 yang sebelumnya hardcoded 11/2 di 4 lapis
# (model/schema/service/frontend). Dokumen baru yang tidak mengirim tarif
# eksplisit memakai nilai dari sini; tarif tersimpan tetap snapshot
# per-dokumen (historis, tidak ikut berubah bila setting diubah).
KEY_RATE_PPN = "RATE_PPN"
KEY_RATE_PPH23 = "RATE_PPH23"

DEFAULT_RATE_PPN = "11"
DEFAULT_RATE_PPH23 = "2"

# Nilai valid untuk metode valuasi (samakan dengan enum MetodeValuasi)
VALID_METODE_VALUASI = ("AVERAGE", "FIFO", "FEFO")
DEFAULT_METODE_VALUASI = "AVERAGE"

# Label deskriptif per key — dipakai pesan error/endpoint
KNOWN_APP_SETTING_LABELS = {
    KEY_METODE_VALUASI: "Metode Valuasi Persediaan",
    KEY_RATE_PPN: "Tarif PPN Default (%)",
    KEY_RATE_PPH23: "Tarif PPh 23 Default (%)",
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
    return _upsert(db, KEY_METODE_VALUASI, value)


# ==========================================
# === AUD-10 (update #04) — tarif pajak default global ===
# ==========================================
def _parse_rate(value: str | None, default: str, label: str) -> Decimal:
    from decimal import Decimal, InvalidOperation
    raw = (value if value is not None else default).strip()
    try:
        rate = Decimal(raw)
    except InvalidOperation:
        return Decimal(default)
    if rate < 0 or rate > 100:
        return Decimal(default)
    return rate


def get_rate_ppn(db: Session) -> Decimal:
    """Tarif PPN default (%) — setting RATE_PPN; fallback 11 bila invalid."""
    return _parse_rate(get_setting(db, KEY_RATE_PPN), DEFAULT_RATE_PPN, "PPN")


def get_rate_pph23(db: Session) -> Decimal:
    """Tarif PPh 23 default (%) — setting RATE_PPH23; fallback 2 bila invalid."""
    return _parse_rate(get_setting(db, KEY_RATE_PPH23), DEFAULT_RATE_PPH23, "PPh 23")


def _validate_rate(value: str, label: str) -> Decimal:
    from decimal import Decimal, InvalidOperation
    try:
        rate = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"{label} harus berupa angka (contoh: '11')")
    if rate < 0 or rate > 100:
        raise ValueError(f"{label} harus di rentang 0–100")
    return rate


def set_rate_ppn(db: Session, value: str) -> AppSetting:
    rate = _validate_rate(value, "Tarif PPN")
    return _upsert(db, KEY_RATE_PPN, str(rate))


def set_rate_pph23(db: Session, value: str) -> AppSetting:
    rate = _validate_rate(value, "Tarif PPh 23")
    return _upsert(db, KEY_RATE_PPH23, str(rate))


def apply_default_tax_rates(db: Session, data_in, *, ppn: bool = True, pph23: bool = False) -> None:
    """AUD-10 — isi tarif pajak None dari setting global (mutasi in-place).

    Dipanggil endpoint create sebelum meneruskan payload ke service:
    schema memakai Optional[Decimal] = None supaya "tidak dikirim" berarti
    "pakai default global", bukan lagi 11/2 hardcoded.
    """
    if ppn and getattr(data_in, "ppn", None) is None:
        data_in.ppn = get_rate_ppn(db)
    if pph23 and getattr(data_in, "pph23", None) is None:
        data_in.pph23 = get_rate_pph23(db)


def _upsert(db: Session, key: str, value: str) -> AppSetting:
    item = db.query(AppSetting).filter(AppSetting.key == key).first()
    if item:
        item.value = value
    else:
        item = AppSetting(key=key, value=value)
        db.add(item)
    db.commit()
    db.refresh(item)
    clear_cache()
    return item
