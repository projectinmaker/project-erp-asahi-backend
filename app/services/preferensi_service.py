"""Service preferensi aplikasi (menu Preferensi — Update ASAHI #019).

Registry tunggal seluruh preferensi non-akun (sumber kebenaran, mengikuti
pola app_setting_service): setiap item didefinisikan di sini lengkap dengan
tipe, default, opsi, dan batasan nilai. Nilai tersimpan di tabel
app_setting (key-value global); bila baris belum ada, GET mengembalikan
default (lazy default — TIDAK di-seed migrasi, supaya default bisa
berubah di kode tanpa migrasi data).

Preferensi terkait AKUN (Akun Default, Biaya Pesanan, dll.) TIDAK di sini —
dikelola tabel setting_akun (lihat setting_akun_service; halaman Preferensi
→ Akun Default di frontend memakai endpoint /master/setting-akun).

Tiga key didelegasikan ke setter app_setting_service supaya validasi &
cache tetap satu pintu (engine valuasi/pajak membaca dari sana):
- METODE_VALUASI → set_metode_valuasi
- RATE_PPN        → set_rate_ppn
- RATE_PPH23      → set_rate_pph23
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.master.app_setting import AppSetting
from app.services import app_setting_service

# Key khusus yang tulisannya didelegasikan (ambil dari app_setting_service
# agar tidak dobel definisi).
KEY_METODE_VALUASI = app_setting_service.KEY_METODE_VALUASI
KEY_RATE_PPN = app_setting_service.KEY_RATE_PPN
KEY_RATE_PPH23 = app_setting_service.KEY_RATE_PPH23

# === Update ASAHI #019 — kode perusahaan & prefix nomor dokumen ===
# Dipakai app/utils/nomor_dokumen.py (get_kode_perusahaan / resolve_prefix)
# untuk menggantikan segmen "ASI" dan prefix hardcoded per jenis dokumen.
KEY_KODE_PERUSAHAAN_NOMOR = "KODE_PERUSAHAAN_NOMOR"
DEFAULT_KODE_PERUSAHAAN_NOMOR = "ASI"

# Pola nilai teks prefix penomoran: huruf/angka/tanda minus, 1–10 karakter.
_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,10}$")


@dataclass(frozen=True)
class PrefDef:
    """Definisi satu item preferensi (satu baris registry)."""

    key: str
    group: str
    label: str
    type: str                      # 'boolean' | 'select' | 'number' | 'text'
    default: object                # bool | int | str
    options: tuple = ()            # pasangan (value, label) untuk type select
    min: int | None = None         # batas bawah/atas untuk type number
    max: int | None = None
    help: str | None = None


# (id grup, label, deskripsi) — urutan tampil di halaman Preferensi frontend.
GROUP_DEFS = (
    ("perusahaan", "Perusahaan", "Perilaku aplikasi terkait data perusahaan & riwayat harga."),
    ("fitur", "Fitur", "Format tampilan tanggal & metode biaya persediaan global."),
    ("pengingat", "Pengingat", "Pengingat otomatis saat sistem mulai (piutang, hutang, stok)."),
    ("penjualan_pembelian", "Penjualan & Pembelian", "Perilaku form faktur, retur, dan pelunasan."),
    ("pembayaran_pesanan", "Pembayaran Pesanan", "Pemrosesan biaya pada pembayaran pesanan (DP)."),
    ("perpajakan", "Perpajakan", "Layanan pajak, format nomor faktur pajak, dan tarif default."),
    ("font", "Font", "Jenis huruf tampilan seluruh aplikasi."),
    ("penomoran", "Penomoran Dokumen", "Kode perusahaan & prefix nomor dokumen otomatis."),
)

# Opsi select yang dipakai lebih dari satu item.
_OPSI_AUTO_SETTLEMENT = (
    ("DENGAN_PAJAK", "Dengan settlement pajak"),
    ("TANPA_PAJAK", "Tanpa settlement pajak"),
    ("KONFIRMASI", "Tampilkan konfirmasi settlement pajak"),
)
_OPSI_RETUR_MODE = (
    ("SEMUA_FAKTUR", "Semua faktur"),
    ("BELUM_LUNAS", "Hanya faktur belum lunas"),
)

# ═══════════════════════════════════════════════════════════════════════════
# REGISTRY PREFERENSI (sumber kebenaran — urutan = urutan tampil per grup)
# ═══════════════════════════════════════════════════════════════════════════
_PREFS: list[PrefDef] = [
    # ── Perusahaan ─────────────────────────────────────────────────────────
    PrefDef("KONFIRMASI_KELUAR", "perusahaan",
            "Konfirmasi sebelum keluar aplikasi", "boolean", True),
    PrefDef("TAMPILKAN_HARGA_HISTORY", "perusahaan",
            "Tampilkan harga pembelian & penjualan di history barang", "boolean", False),
    # ── Fitur ──────────────────────────────────────────────────────────────
    PrefDef("FORMAT_TANGGAL", "fitur", "Format tanggal tampilan", "select", "DD/MM/YYYY",
            options=(
                ("DD/MM/YYYY", "DD/MM/YYYY"),
                ("DD-MM-YYYY", "DD-MM-YYYY"),
                ("DD.MM.YYYY", "DD.MM.YYYY"),
                ("YYYY-MM-DD", "YYYY-MM-DD"),
                ("DD MMM YYYY", "DD MMM YYYY"),
                ("MM/DD/YYYY", "MM/DD/YYYY"),
            ),
            help="Dipakai seluruh tampilan tanggal di aplikasi & cetak (pdf-utils)."),
    PrefDef(KEY_METODE_VALUASI, "fitur", "Metode biaya persediaan", "select", "AVERAGE",
            options=(
                ("AVERAGE", "Rata-rata Bergerak"),
                ("FIFO", "FIFO"),
                ("FEFO", "FEFO"),
            ),
            help="Engine valuasi persediaan global — tulisan didelegasikan ke app_setting_service."),
    # ── Pengingat ───────────────────────────────────────────────────────────
    PrefDef("PENGINGAT_AKTIF", "pengingat",
            "Tampilkan pengingat setiap kali sistem mulai", "boolean", True),
    PrefDef("PENGINGAT_BARANG_DIPESAN", "pengingat",
            "Pengingat barang yang perlu dipesan", "boolean", True),
    PrefDef("PENGINGAT_PIUTANG_HARI", "pengingat",
            "Piutang jatuh tempo — jumlah hari sebelum jatuh tempo", "number", 7, min=0, max=365),
    PrefDef("PENGINGAT_DISKON_PIUTANG_HARI", "pengingat",
            "Jatuh tempo diskon piutang — jumlah hari sebelum", "number", 7, min=0, max=365),
    PrefDef("PENGINGAT_HUTANG_HARI", "pengingat",
            "Hutang jatuh tempo — jumlah hari sebelum", "number", 7, min=0, max=365),
    PrefDef("PENGINGAT_DISKON_HUTANG_HARI", "pengingat",
            "Diskon hutang jatuh tempo — jumlah hari sebelum", "number", 7, min=0, max=365),
    # ── Penjualan & Pembelian ───────────────────────────────────────────────
    PrefDef("AUTO_SETTLEMENT_PENJUALAN", "penjualan_pembelian",
            "Faktur penjualan — auto settlement", "select", "KONFIRMASI",
            options=_OPSI_AUTO_SETTLEMENT),
    PrefDef("AUTO_SETTLEMENT_PEMBELIAN", "penjualan_pembelian",
            "Faktur pembelian — auto settlement", "select", "KONFIRMASI",
            options=_OPSI_AUTO_SETTLEMENT),
    PrefDef("RETUR_PENJUALAN_MODE", "penjualan_pembelian",
            "Retur penjualan", "select", "SEMUA_FAKTUR", options=_OPSI_RETUR_MODE),
    PrefDef("RETUR_PEMBELIAN_MODE", "penjualan_pembelian",
            "Retur pembelian", "select", "SEMUA_FAKTUR", options=_OPSI_RETUR_MODE),
    PrefDef("KONFIRMASI_FAKTUR_PENJUALAN", "penjualan_pembelian",
            "Tampilkan konfirmasi saat membuat faktur penjualan", "boolean", False),
    PrefDef("KONFIRMASI_FAKTUR_PEMBELIAN", "penjualan_pembelian",
            "Tampilkan konfirmasi saat membuat faktur pembelian", "boolean", False),
    PrefDef("PESAN_PEMBAYARAN_LEBIH", "penjualan_pembelian",
            "Tampilkan pesan saat penerimaan pelanggan terjadi pembayaran lebih", "boolean", True),
    # ── Pembayaran Pesanan ──────────────────────────────────────────────────
    PrefDef("BIAYA_BEDA_AKUN", "pembayaran_pesanan",
            "Simpan biaya pesanan ke akun berbeda", "boolean", False,
            help="Saat aktif, biaya pesanan diposting ke akun 'Biaya Pesanan' "
                 "(Setting Akun key BIAYA_PESANAN) — dikonfigurasi di halaman ini."),
    # ── Perpajakan ──────────────────────────────────────────────────────────
    PrefDef("LAYANAN_PAJAK", "perpajakan", "Layanan pajak", "select", "PMK_1195",
            options=(
                ("PMK_1195", "Layanan Pajak 1195"),
                ("PMK_1107", "Layanan Pajak 1107"),
            )),
    PrefDef("KODE_DALAM_NOMOR_FAKTUR", "perpajakan",
            "Gunakan kode dalam nomor faktur pajak", "boolean", True),
    PrefDef("DIGIT_NOMOR_FAKTUR", "perpajakan",
            "Jumlah digit angka urut per faktur", "number", 8, min=1, max=12),
    PrefDef("RANGKAP_FAKTUR_PPN", "perpajakan",
            "Rangkap cetak Faktur PPN", "select", "1",
            options=(("1", "1"), ("2", "2"), ("3", "3"))),
    PrefDef(KEY_RATE_PPN, "perpajakan", "Tarif PPN default (%)", "number", 11, min=0, max=100,
            help="Dipakai dokumen baru tanpa tarif eksplisit — tulisan didelegasikan ke "
                 "app_setting_service."),
    PrefDef(KEY_RATE_PPH23, "perpajakan", "Tarif PPh 23 default (%)", "number", 2, min=0, max=100,
            help="Dipakai dokumen baru tanpa tarif eksplisit — tulisan didelegasikan ke "
                 "app_setting_service."),
    # ── Font ────────────────────────────────────────────────────────────────
    PrefDef("FONT_APLIKASI", "font", "Font aplikasi", "select", "DEFAULT",
            options=(
                ("DEFAULT", "Default Aplikasi"),
                ("INTER", "Inter"),
                ("ARIAL", "Arial"),
                ("HELVETICA", "Helvetica"),
                ("TIMES_NEW_ROMAN", "Times New Roman"),
                ("GEORGIA", "Georgia"),
                ("VERDANA", "Verdana"),
                ("TAHOMA", "Tahoma"),
                ("TREBUCHET_MS", "Trebuchet MS"),
                ("COURIER_NEW", "Courier New"),
            )),
    # ── Penomoran Dokumen (semua type text; default = nilai hardcoded lama) ─
    PrefDef(KEY_KODE_PERUSAHAAN_NOMOR, "penomoran",
            "Kode perusahaan (segmen nomor dokumen)", "text", DEFAULT_KODE_PERUSAHAAN_NOMOR,
            help="Segmen perusahaan pada nomor dokumen, contoh: SO ASI/2026/001."),
    PrefDef("PREFIX_SALES_ORDER", "penomoran", "Pesanan Penjualan (SO)", "text", "SO"),
    PrefDef("PREFIX_PENAWARAN", "penomoran", "Penawaran", "text", "PEN"),
    PrefDef("PREFIX_PENGIRIMAN_BARANG", "penomoran", "Pengiriman Barang (DO)", "text", "DO"),
    PrefDef("PREFIX_SALES_INVOICE", "penomoran", "Invoice Penjualan", "text", "INV"),
    PrefDef("PREFIX_RETUR_PENJUALAN", "penomoran", "Retur Penjualan", "text", "RET-J"),
    PrefDef("PREFIX_TUKAR_FAKTUR", "penomoran", "Tukar Faktur", "text", "TF"),
    PrefDef("PREFIX_PURCHASE_ORDER", "penomoran", "Pesanan Pembelian (PO)", "text", "PO"),
    PrefDef("PREFIX_PENERIMAAN_BARANG", "penomoran", "Penerimaan Barang", "text", "PB"),
    PrefDef("PREFIX_PURCHASE_INVOICE", "penomoran", "Invoice Pembelian", "text", "PINV"),
    PrefDef("PREFIX_RETUR_PEMBELIAN", "penomoran", "Retur Pembelian", "text", "RET-B"),
    PrefDef("PREFIX_LPPB", "penomoran", "LPPB", "text", "LPPB"),
    PrefDef("PREFIX_REQUEST_ORDER_PART", "penomoran", "Request Order Part", "text", "ROP"),
    PrefDef("PREFIX_PENYESUAIAN_STOK", "penomoran", "Penyesuaian Stok", "text", "ADJ"),
    PrefDef("PREFIX_PEMINDAHAN_BARANG", "penomoran", "Pemindahan Barang", "text", "TRF-STK"),
    PrefDef("PREFIX_PERMINTAAN_BARANG", "penomoran", "Permintaan Barang", "text", "REQ"),
    PrefDef("PREFIX_PEMBAYARAN", "penomoran", "Pembayaran (Pelunasan Invoice)", "text", "PAY"),
    PrefDef("PREFIX_PEMBAYARAN_LAIN", "penomoran", "Pembayaran Lain (Voucher)", "text", "VC"),
    PrefDef("PREFIX_PENERIMAAN", "penomoran", "Penerimaan (Pelunasan Invoice)", "text", "REC"),
    PrefDef("PREFIX_PENERIMAAN_LAIN", "penomoran", "Penerimaan Lain", "text", "PB"),
    PrefDef("PREFIX_TRANSFER_BANK", "penomoran", "Transfer Bank", "text", "TRF"),
    PrefDef("PREFIX_JURNAL_MANUAL", "penomoran", "Jurnal Umum (manual)", "text", "JV"),
]

# Lookup key → definisi (validasi & pembacaan cepat).
PREFERENSI_BY_KEY: dict[str, PrefDef] = {p.key: p for p in _PREFS}

# === Update ASAHI #019 — perkaya label app_setting_service ===
# Semua key preferensi ditambahkan ke KNOWN_APP_SETTING_LABELS agar pesan
# error/dokumentasi key app_setting lengkap. Dilakukan DI SINI (bukan di
# app_setting_service) supaya tidak ada circular import — modul ini sudah
# mengimpor app_setting_service; dict diperkaya saat modul ini dimuat.
app_setting_service.KNOWN_APP_SETTING_LABELS.update(
    {p.key: p.label for p in _PREFS if p.key not in app_setting_service.KNOWN_APP_SETTING_LABELS}
)


# ═══════════════════════════════════════════════════════════════════════════
# Pembacaan (GET /master/preferensi)
# ═══════════════════════════════════════════════════════════════════════════
def _nilai_efektif(defn: PrefDef, raw: str | None):
    """Nilai tersimpan (string di app_setting) → tipe deklarasikan item.

    Fallback ke default bila baris belum ada / nilai rusak / di luar opsi
    (defensif — pola app_setting_service.get_metode_valuasi).
    """
    if raw is None:
        return defn.default
    s = raw.strip()
    if not s:
        return defn.default
    if defn.type == "boolean":
        return s.lower() in ("true", "1", "yes")
    if defn.type == "number":
        try:
            d = Decimal(s)
        except InvalidOperation:
            return defn.default
        return int(d) if d == d.to_integral_value() else s
    # select / text — select divalidasi ke daftar opsi.
    if defn.options:
        valid = {v for v, _ in defn.options}
        if s not in valid:
            return defn.default
    return s


def get_all(db: Session) -> list[dict]:
    """Snapshot seluruh grup & item preferensi + nilai efektifnya.

    Dipakai endpoint GET /master/preferensi — dan setelah PUT, supaya
    frontend langsung menerima kondisi terbaru tanpa fetch ulang logika.
    """
    snapshot: list[dict] = []
    for group_id, group_label, group_desc in GROUP_DEFS:
        items = [p for p in _PREFS if p.group == group_id]
        if not items:
            continue
        snapshot.append({
            "id": group_id,
            "label": group_label,
            "description": group_desc,
            "items": [
                {
                    "key": p.key,
                    "label": p.label,
                    "type": p.type,
                    "value": _nilai_efektif(p, app_setting_service.get_setting(db, p.key)),
                    "default": p.default,
                    "options": [{"value": v, "label": lbl} for v, lbl in p.options],
                    "min": p.min,
                    "max": p.max,
                    "help": p.help,
                }
                for p in items
            ],
        })
    return snapshot


# ═══════════════════════════════════════════════════════════════════════════
# Penulisan (PUT /master/preferensi)
# ═══════════════════════════════════════════════════════════════════════════
def _validasi_dan_normalisasi(defn: PrefDef, value) -> str:
    """Validasi + normalisasi satu nilai → string siap simpan.

    Raise ValueError (pesan Indonesia) bila nilai tidak sesuai tipe/opsi/
    batasan item — endpoint menerjemahkan ke HTTP 400.
    """
    label = defn.label
    if defn.type == "boolean":
        # Terima bool maupun string "true"/"false" (ala payload JSON longgar).
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower()
        raise ValueError(f"{label} harus berupa boolean (true/false)")
    if defn.type == "select":
        s = str(value).strip()
        valid = {v for v, _ in defn.options}
        if s not in valid:
            raise ValueError(f"{label} harus salah satu dari: {', '.join(v for v, _ in defn.options)}")
        return s
    if defn.type == "number":
        if isinstance(value, bool):
            raise ValueError(f"{label} harus berupa angka bulat")
        try:
            d = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise ValueError(f"{label} harus berupa angka bulat") from None
        if d != d.to_integral_value():
            raise ValueError(f"{label} harus berupa angka bulat")
        n = int(d)
        if (defn.min is not None and n < defn.min) or (defn.max is not None and n > defn.max):
            raise ValueError(f"{label} harus di rentang {defn.min}–{defn.max}")
        return str(n)
    # type text (prefix penomoran) — normalisasi UPPERCASE utk grup penomoran.
    s = str(value).strip()
    if defn.group == "penomoran":
        s = s.upper()
    if not _TEXT_PATTERN.fullmatch(s):
        raise ValueError(
            f"{label} hanya boleh huruf, angka, atau tanda minus (1–10 karakter)"
        )
    return s


def _upsert_setting(db: Session, key: str, value: str) -> None:
    """Upsert satu baris app_setting + clear cache (pola app_setting_service)."""
    item = db.query(AppSetting).filter(AppSetting.key == key).first()
    if item:
        item.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()
    app_setting_service.clear_cache()


def update_bulk(db: Session, updates: dict) -> None:
    """Simpan sekumpulan preferensi sekaligus (PUT /master/preferensi).

    Dua tahap:
    1. Validasi SEMUA key lebih dulu — key tidak dikenal / nilai tidak valid
       → ValueError SEBELUM ada baris tertulis (tidak ada update parsial).
    2. Tulis satu per satu; METODE_VALUASI / RATE_PPN / RATE_PPH23
       didelegasikan ke setter app_setting_service (validasi khusus + cache
       engine valuasi/pajak tetap konsisten).
    """
    if not updates:
        raise ValueError("Payload updates kosong — tidak ada preferensi yang diubah")

    # Tahap 1 — validasi & normalisasi semua key (gagal cepat).
    normalized: dict[str, str] = {}
    for key, value in updates.items():
        defn = PREFERENSI_BY_KEY.get(key)
        if defn is None:
            raise ValueError(f"Preferensi tidak dikenal: {key}")
        normalized[key] = _validasi_dan_normalisasi(defn, value)

    # Tahap 2 — tulis.
    for key, value in normalized.items():
        if key == KEY_METODE_VALUASI:
            app_setting_service.set_metode_valuasi(db, value)
        elif key == KEY_RATE_PPN:
            app_setting_service.set_rate_ppn(db, value)
        elif key == KEY_RATE_PPH23:
            app_setting_service.set_rate_pph23(db, value)
        else:
            _upsert_setting(db, key, value)
