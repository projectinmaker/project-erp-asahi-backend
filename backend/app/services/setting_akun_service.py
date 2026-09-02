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
