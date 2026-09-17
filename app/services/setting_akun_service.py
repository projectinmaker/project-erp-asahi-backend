"""Service untuk mengambil konfigurasi akun default dari tabel setting_akun.

Digunakan oleh semua modul transaksi saat auto-posting jurnal.
Caching per-request untuk menghindari query berulang.
"""

from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session

from app.models.master.setting_akun import SettingAkun


# Kunci-kunci setting akun yang tersedia
KEY_PENDAPATAN_PENJUALAN = "PENDAPATAN_PENJUALAN"
KEY_PPN_KELUARAN = "PPN_KELUARAN"
KEY_PPN_MASUKAN = "PPN_MASUKAN"
KEY_RETUR_PENJUALAN = "RETUR_PENJUALAN"
KEY_RETUR_PEMBELIAN = "RETUR_PEMBELIAN"
KEY_HPP_PENJUALAN = "HPP_PENJUALAN"
KEY_PEMBELIAN = "PEMBELIAN"
KEY_PERSEDIAAN_BAHAN_BAKU = "PERSEDIAAN_BAHAN_BAKU"
KEY_PERSEDIAAN_WIP = "PERSEDIAAN_WIP"
KEY_PERSEDIAAN_BARANG_JADI = "PERSEDIAAN_BARANG_JADI"
KEY_BEBAN_ANGKUT_PEMBELIAN = "BEBAN_ANGKUT_PEMBELIAN"
KEY_BEBAN_TRANSFER_BANK = "BEBAN_TRANSFER_BANK"
KEY_BEBAN_ADMIN = "BEBAN_ADMIN"
KEY_PIUTANG_USAHA = "PIUTANG_USAHA"
KEY_HUTANG_USAHA = "HUTANG_USAHA"
KEY_SELISIH_PERSEDIAAN = "SELISIH_PERSEDIAAN"
KEY_PERSEDIAAN_BAHAN_PEMBANTU = "PERSEDIAAN_BAHAN_PEMBANTU"
KEY_LABA_RUGI_BERJALAN = "LABA_RUGI_BERJALAN"
KEY_KAS_DAN_SETARA_KAS = "KAS_DAN_SETARA_KAS"
KEY_PENDAPATAN_ANGKUT = "PENDAPATAN_ANGKUT"

# Tahap 2: Akun perantara untuk penerimaan barang yang belum diinvois
# (Goods Received Not Invoiced / GRNI). Bersifat opsional:
# - Jika di-configure: penerimaan barang mempost D: Persediaan, K: PENERIMAAN_DALAM_PROSES.
#   Lalu saat purchase invoice terkait dipost, dilakukan D: PENERIMAAN_DALAM_PROSES,
#   K: Utang Dagang (clearing akun perantara).
# - Jika belum di-configure: penerimaan barang TIDAK mempost jurnal (legacy),
#   invoice pembelian tetap D: Pembelian, K: Utang Dagang (cara lama).
KEY_PENERIMAAN_DALAM_PROSES = "PENERIMAAN_DALAM_PROSES"

# === ASAHI COA Revisi v2 — Keys baru ===
# Default akun-akun kritis yang direferensikan dari CATATAN_IMPORT_COA_ASAHI.md
# section 10. Wajib di-configure via PUT /master/setting-akun/{key} sebelum
# fitur-fitur baru (transfer bank clearing, COGS auto-post, year-end closing)
# bisa berjalan.

# Akun clearing untuk transfer antar kas/bank (111200 Akun Clearing / Ayat Silang)
KEY_BANK_CLEARING = "BANK_CLEARING"

# Akun HPP aktual yang diposting saat barang jadi terjual (531001 HPP Produk Jadi)
KEY_HPP_PRODUK_JADI = "HPP_PRODUK_JADI"

# Akun laba/rugi tahun berjalan (322000) — system account, tidak diposting manual
KEY_LABA_RUGI_TAHUN_BERJALAN = "LABA_RUGI_TAHUN_BERJALAN"

# Akun laba ditahan (321000) — tujuan transfer laba/rugi saat year-end closing
KEY_LABA_DITAHAN = "LABA_DITAHAN"

# Mapping key -> expected system_account_type (untuk validasi konfigurasi)
# Kalau user set key PIUTANG_USAHA ke akun yang bukan AR_CONTROL, service bisa
# warning (tidak fatal, supaya flexible).
SETTING_KEY_TO_SYSTEM_ACCOUNT_TYPE = {
    KEY_PIUTANG_USAHA: "AR_CONTROL",
    KEY_HUTANG_USAHA: "AP_CONTROL",
    KEY_BANK_CLEARING: "BANK_CLEARING",
    KEY_HPP_PRODUK_JADI: "COGS_FINISHED_GOODS",
    KEY_LABA_RUGI_TAHUN_BERJALAN: "CURRENT_EARNINGS",
    KEY_LABA_DITAHAN: "RETAINED_EARNINGS",
    KEY_PERSEDIAAN_BAHAN_BAKU: "INVENTORY_RAW",
    KEY_PERSEDIAAN_BAHAN_PEMBANTU: "INVENTORY_AUX",
    KEY_PERSEDIAAN_WIP: "INVENTORY_WIP",
    KEY_PERSEDIAAN_BARANG_JADI: "INVENTORY_FINISHED",
}

# Key-key yang WAJIB di-configure agar auto-posting jurnal Sales/Purchase
# (Order, Invoice, Retur) tidak gagal. Dipakai untuk startup check.
CRITICAL_KEYS = [
    KEY_PENDAPATAN_PENJUALAN,
    KEY_PPN_KELUARAN,
    KEY_RETUR_PENJUALAN,
    KEY_PEMBELIAN,
    KEY_PPN_MASUKAN,
    KEY_RETUR_PEMBELIAN,
    # === ASAHI COA Revisi v2 — keys yang wajib di-configure ===
    KEY_BANK_CLEARING,
    KEY_HPP_PRODUK_JADI,
    KEY_LABA_RUGI_TAHUN_BERJALAN,
    KEY_LABA_DITAHAN,
]


# Simple in-memory cache (per process lifecycle)
_cache: dict[str, Optional[UUID]] = {}


def _load_all_settings(db: Session) -> None:
    """Load semua setting akun ke cache."""
    global _cache
    rows = db.query(SettingAkun).all()
    _cache = {r.key: r.akun_perkiraan_id for r in rows}
    logger.debug(f"Loaded {len(_cache)} setting akun into cache")


def get_akun_id(db: Session, key: str) -> Optional[UUID]:
    """Ambil UUID akun perkiraan berdasarkan key setting.

    Return:
        UUID akun_perkiraan, atau None jika belum di-configure.
    """
    if not _cache:
        _load_all_settings(db)

    return _cache.get(key)


def get_akun_id_or_raise(db: Session, key: str, context: str = "") -> UUID:
    """Ambil UUID akun perkiraan, raise ValueError jika tidak ditemukan.

    Parameter:
        key: Kunci setting (misal 'PENDAPATAN_PENJUALAN')
        context: Keterangan tambahan untuk error message

    Return:
        UUID akun_perkiraan

    Raises:
        ValueError: Jika akun belum di-configure
    """
    akun_id = get_akun_id(db, key)
    if not akun_id:
        ctx = f" ({context})" if context else ""
        raise ValueError(
            f"Setting akun '{key}' belum di-configure. "
            f"Jalankan 'python3 -m app.seed.phase3_setting_akun_seed' dulu.{ctx}"
        )
    return akun_id


def clear_cache() -> None:
    """Clear cache (digunakan setelah update setting)."""
    global _cache
    _cache = {}


def check_critical_settings(db: Session) -> list[str]:
    """Cek key-key kritis (dipakai auto-posting Sales/Purchase) sudah di-configure.

    Return:
        List key yang BELUM di-configure. Kosong berarti semua sudah OK.
        Dipakai untuk warning non-fatal saat startup aplikasi.
    """
    if not _cache:
        _load_all_settings(db)
    return [key for key in CRITICAL_KEYS if not _cache.get(key)]