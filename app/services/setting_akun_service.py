"""Service untuk mengambil konfigurasi akun default dari tabel setting_akun.

Digunakan oleh semua modul transaksi saat auto-posting jurnal.
Caching per-request untuk menghindari query berulang.
"""

from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session

from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun
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

# Akun PPh 23 Dibayar Dimuka (150100002) — dipakai saat Invoice Penjualan
# memakai pilihan PPh23 (potongan pajak oleh pelanggan): debit saat posting
# agar jurnal balance (grand_total sudah dipotong total_pph23).
# Update ASAHI — pilihan PPh23/PPN opsional pada PO & Invoice Penjualan.
KEY_PPH23_DIBAYAR_DIMUKA = "PPH23_DIBAYAR_DIMUKA"

# Akun laba ditahan (321000) — tujuan transfer laba/rugi saat year-end closing
KEY_LABA_DITAHAN = "LABA_DITAHAN"

# === Update cetak "Bayar Pemasok" ===
# Akun potongan hutang pelunasan — dikredit saat pembayaran ke supplier
# memakai diskon per faktur (kas keluar < hutang dilunasi). Bersifat
# opsional: wajib dikonfigurasi HANYA bila ada pelunasan dengan diskon > 0.
# Konfigurasi via Pengaturan → Setting Akun (PUT /master/setting-akun/POTONGAN_HUTANG).
KEY_POTONGAN_HUTANG = "POTONGAN_HUTANG"

# === Update #01 — PPh 23 saat pelunasan ===
# Akun Hutang PPh 23 (COA v2 213002) — dikredit saat pelunasan hutang
# (pembayaran ke supplier) menyertakan potongan PPh 23 per faktur yang
# ditahan perusahaan: kas keluar = nilai - diskon - pph23. Opsional:
# hanya wajib bila ada pelunasan hutang dengan pph23 > 0. Konfigurasi
# via Pengaturan → Setting Akun (PUT /master/setting-akun/HUTANG_PPH23).
# (Sisi piutang memakai KEY_PPH23_DIBAYAR_DIMUKA yang sudah ada.)
KEY_HUTANG_PPH23 = "HUTANG_PPH23"

# === Update #02 — Kas & Bank: akun penampung Belum Teridentifikasi ===
# Penerimaan/Pembayaran dengan jenis transaksi BELUM_TERIDENTIFIKASI
# diposting ke akun penampung sementara ini (default COA 216000 "Hutang
# Belum Teridentifikasi (Penampung)" di bawah 210000). Setelah asal mutasi
# diketahui, nilai dipindahkan ke akun yang benar lewat fitur Identifikasi
# & Alokasi (jurnal reklasifikasi tanpa jurnal bank ganda). Konfigurasi
# per perusahaan via Pengaturan → Setting Akun
# (PUT /master/setting-akun/KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI).
# Update #03: key ini menjadi LEGACY — dokumen lama tetap membaca akun
# penampung dari rincian masing-masing; dokumen baru memakai key arah-
# spesifik di bawah.
KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI = "KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI"

# === Update #03 — Piutang/Hutang Kontrol Sementara (dua akun penampung
# terpisah, penyajian neraca benar: penerimaan = liabilitas, pembayaran =
# aset; keduanya TERPISAH dari akun piutang/hutang operasional). ===
# Penerimaan BELUM_TERIDENTIFIKASI → 216001 "Penerimaan Kas Belum
# Teridentifikasi (Penampung)" (liabilitas, kredit).
KEY_KAS_PENAMPUNG_PENERIMAAN = "KAS_PENAMPUNG_PENERIMAAN"
# Pembayaran BELUM_TERIDENTIFIKASI → 117004 "Pembayaran Kas Belum
# Teridentifikasi (Penampung)" (aset lancar, debit).
KEY_KAS_PENAMPUNG_PEMBAYARAN = "KAS_PENAMPUNG_PEMBAYARAN"

# Label untuk semua key setting yang dikenal sistem. Dipakai endpoint
# PUT /master/setting-akun/{key} untuk MEMBUAT baris setting baru bila belum ada
# (mis. PENDAPATAN_ANGKUT yang di-skip phase3 seed karena akun default legacy
# 402000002 tidak ada di COA v2 — kini bisa dikonfigurasi langsung dari UI
# Setting Akun tanpa harus menjalankan ulang seeder).
KNOWN_SETTING_LABELS = {
    KEY_PENDAPATAN_PENJUALAN: "Pendapatan Penjualan",
    KEY_PPN_KELUARAN: "PPN Keluaran",
    KEY_PPN_MASUKAN: "PPN Masukan",
    KEY_RETUR_PENJUALAN: "Retur Penjualan",
    KEY_RETUR_PEMBELIAN: "Retur Pembelian",
    KEY_HPP_PENJUALAN: "Harga Pokok Penjualan",
    KEY_PEMBELIAN: "Pembelian",
    KEY_PERSEDIAAN_BAHAN_BAKU: "Persediaan Bahan Baku",
    KEY_PERSEDIAAN_WIP: "Persediaan Barang Dalam Proses (WIP)",
    KEY_PERSEDIAAN_BARANG_JADI: "Persediaan Barang Jadi",
    KEY_BEBAN_ANGKUT_PEMBELIAN: "Beban Angkut Pembelian",
    KEY_BEBAN_TRANSFER_BANK: "Beban Biaya Transfer Bank",
    KEY_BEBAN_ADMIN: "Beban Biaya Admin",
    KEY_PIUTANG_USAHA: "Piutang Usaha",
    KEY_HUTANG_USAHA: "Hutang Usaha",
    KEY_SELISIH_PERSEDIAAN: "Selisih Persediaan",
    KEY_PERSEDIAAN_BAHAN_PEMBANTU: "Persediaan Bahan Pembantu",
    KEY_LABA_RUGI_BERJALAN: "Laba Rugi Berjalan",
    KEY_KAS_DAN_SETARA_KAS: "Kas dan Setara Kas",
    KEY_PENDAPATAN_ANGKUT: "Pendapatan Angkut",
    KEY_PENERIMAAN_DALAM_PROSES: "Penerimaan Dalam Proses (GRNI)",
    KEY_BANK_CLEARING: "Akun Clearing / Ayat Silang",
    KEY_HPP_PRODUK_JADI: "HPP Produk Jadi",
    KEY_LABA_RUGI_TAHUN_BERJALAN: "Laba (Rugi) Tahun Berjalan",
    KEY_LABA_DITAHAN: "Laba (Rugi) Ditahan",
    KEY_PPH23_DIBAYAR_DIMUKA: "PPh 23 Dibayar Dimuka",
    KEY_POTONGAN_HUTANG: "Potongan Hutang (Pelunasan)",
    KEY_HUTANG_PPH23: "Hutang PPh 23 (Pelunasan)",
    # === Update #02 — Kas & Bank ===
    KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI: "Kas Belum Teridentifikasi (Penampung)",
    # === Update #03 — penampung arah-spesifik (neraca terpisah AR/AP) ===
    KEY_KAS_PENAMPUNG_PENERIMAAN: "Penerimaan Kas Belum Teridentifikasi (Penampung AR)",
    KEY_KAS_PENAMPUNG_PEMBAYARAN: "Pembayaran Kas Belum Teridentifikasi (Penampung AP)",
}

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

# === AUD-05 (update #04) — validasi tipe akun per key saat PUT setting-akun ===
# Temuan audit 7-a: PUT /master/setting-akun/{key} menerima akun APA PUN tanpa
# validasi tipe/arah — misconfig baru terlihat saat runtime (jurnal salah arah
# diam-diam atau ValueError saat posting). Peta ini menegakkan kontrak tiap key:
#   headers      : header COA yang wajar untuk key tsb (AKTIVA/KEWAJIBAN/MODAL/
#                  PENDAPATAN/HPP/BEBAN).
#   saldo_normal : arah saldo akun (DEBIT/KREDIT); None = dua arah (clearing/
#                  selisih).
#   group        : True bila akun GROUP (control root, mis. PIUTANG_USAHA 112000)
#                  diperbolehkan — key lain wajib DETAIL.
# Validasi umum untuk semua key: akun ada, aktif, bukan subledger (kecuali group).
# PENERIMAAN_DALAM_PROSES tetap memakai validator khusus validate_grni_account
# (lebih ketat: ≠ akun hutang supplier) — peta ini jadi lapis tambahan.
SETTING_KEY_VALIDATION: dict[str, dict] = {
    KEY_PENDAPATAN_PENJUALAN: {"headers": {"PENDAPATAN"}, "saldo_normal": "KREDIT"},
    KEY_PPN_KELUARAN: {"headers": {"KEWAJIBAN"}, "saldo_normal": "KREDIT"},
    KEY_PPN_MASUKAN: {"headers": {"AKTIVA"}, "saldo_normal": "DEBIT"},
    KEY_RETUR_PENJUALAN: {"headers": {"PENDAPATAN"}, "saldo_normal": "DEBIT"},
    KEY_RETUR_PEMBELIAN: {"headers": {"HPP", "AKTIVA"}, "saldo_normal": "KREDIT"},
    KEY_HPP_PENJUALAN: {"headers": {"HPP"}, "saldo_normal": "DEBIT"},
    KEY_HPP_PRODUK_JADI: {"headers": {"HPP"}, "saldo_normal": "DEBIT"},
    KEY_PEMBELIAN: {"headers": {"HPP"}, "saldo_normal": "DEBIT"},
    KEY_PERSEDIAAN_BAHAN_BAKU: {"headers": {"AKTIVA"}, "saldo_normal": "DEBIT"},
    KEY_PERSEDIAAN_WIP: {"headers": {"AKTIVA"}, "saldo_normal": "DEBIT"},
    KEY_PERSEDIAAN_BARANG_JADI: {"headers": {"AKTIVA"}, "saldo_normal": "DEBIT"},
    KEY_PERSEDIAAN_BAHAN_PEMBANTU: {"headers": {"AKTIVA"}, "saldo_normal": "DEBIT"},
    KEY_SELISIH_PERSEDIAAN: {"headers": {"BEBAN", "PENDAPATAN"}},
    KEY_BEBAN_ANGKUT_PEMBELIAN: {"headers": {"HPP", "BEBAN"}, "saldo_normal": "DEBIT"},
    KEY_BEBAN_TRANSFER_BANK: {"headers": {"BEBAN"}, "saldo_normal": "DEBIT"},
    KEY_BEBAN_ADMIN: {"headers": {"BEBAN"}, "saldo_normal": "DEBIT"},
    KEY_PIUTANG_USAHA: {"headers": {"AKTIVA"}, "saldo_normal": "DEBIT", "group": True},
    KEY_HUTANG_USAHA: {"headers": {"KEWAJIBAN"}, "saldo_normal": "KREDIT", "group": True},
    KEY_LABA_RUGI_BERJALAN: {"headers": {"MODAL"}},
    KEY_LABA_RUGI_TAHUN_BERJALAN: {"headers": {"MODAL"}},
    KEY_LABA_DITAHAN: {"headers": {"MODAL"}},
    KEY_KAS_DAN_SETARA_KAS: {"headers": {"AKTIVA"}, "group": True},
    KEY_PENDAPATAN_ANGKUT: {"headers": {"PENDAPATAN"}, "saldo_normal": "KREDIT"},
    KEY_PENERIMAAN_DALAM_PROSES: {"headers": {"KEWAJIBAN"}, "saldo_normal": "KREDIT"},
    KEY_BANK_CLEARING: {"headers": {"KEWAJIBAN", "AKTIVA"}},
    KEY_PPH23_DIBAYAR_DIMUKA: {"headers": {"AKTIVA"}, "saldo_normal": "DEBIT"},
    KEY_POTONGAN_HUTANG: {"headers": {"PENDAPATAN"}, "saldo_normal": "KREDIT"},
    KEY_HUTANG_PPH23: {"headers": {"KEWAJIBAN"}, "saldo_normal": "KREDIT"},
    KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI: {"headers": {"KEWAJIBAN"}, "saldo_normal": "KREDIT"},
    KEY_KAS_PENAMPUNG_PENERIMAAN: {"headers": {"KEWAJIBAN"}, "saldo_normal": "KREDIT"},
    KEY_KAS_PENAMPUNG_PEMBAYARAN: {"headers": {"AKTIVA"}, "saldo_normal": "DEBIT"},
}


def _enum_value(value) -> str:
    return getattr(value, "value", value)


def validate_setting_akun_target(db: Session, key: str, akun_id) -> AkunPerkiraan:
    """AUD-05 (update #04): validasi akun target setting sebelum disimpan.

    Memastikan akun ADA (bukan FK 500), AKTIF, dan bertipe/arah yang wajar
    untuk key tsb (header COA + saldo normal + tingkat DETAIL/group). Raise
    ValueError dengan pesan spesifik — endpoint PUT menerjemahkan ke 400.
    Return objek AkunPerkiraan yang tervalidasi.
    """
    akun = db.get(AkunPerkiraan, akun_id)
    if akun is None:
        raise ValueError("Akun perkiraan tidak ditemukan")
    label = KNOWN_SETTING_LABELS.get(key, key)
    akun_desc = f"{akun.kode} — {akun.nama}"

    if not akun.active:
        raise ValueError(f"Akun {akun_desc} tidak aktif; pilih akun aktif untuk {label}")

    spec = SETTING_KEY_VALIDATION.get(key)
    if spec is None:
        # Key tanpa kontrak khusus (termasuk key custom) — cukup ada & aktif.
        return akun

    tingkat = _enum_value(akun.tingkat)
    if tingkat != "DETAIL" and not spec.get("group"):
        raise ValueError(
            f"Akun {akun_desc} berjenjang {tingkat}; setting {label} harus akun DETAIL"
        )
    if akun.is_subledger and not spec.get("group"):
        raise ValueError(
            f"Akun {akun_desc} adalah subledger; setting {label} harus akun posting biasa"
        )

    headers = {_enum_value(h) for h in spec.get("headers", ())}
    header = _enum_value(akun.header)
    if headers and header not in headers:
        raise ValueError(
            f"Akun {akun_desc} bertipe {header}; setting {label} seharusnya "
            f"{ ' / '.join(sorted(headers)) }"
        )

    saldo_wajib = spec.get("saldo_normal")
    saldo = _enum_value(akun.saldo_normal)
    if saldo_wajib and saldo != saldo_wajib:
        raise ValueError(
            f"Akun {akun_desc} bersaldo normal {saldo}; setting {label} "
            f"seharusnya bersaldo normal {saldo_wajib}"
        )

    expected_systype = SETTING_KEY_TO_SYSTEM_ACCOUNT_TYPE.get(key)
    if (expected_systype and akun.system_account_type
            and _enum_value(akun.system_account_type) != expected_systype):
        raise ValueError(
            f"Akun {akun_desc} terklasifikasi {akun.system_account_type}; "
            f"setting {label} seharusnya {expected_systype}"
        )
    return akun


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


# === ASAHI #006 — Fallback auto-configure setting akun ===
# Masalah: key seperti PPH23_DIBAYAR_DIMUKA hanya pernah dibuat oleh seeder
# legacy phase3 (desain COA format lama 150100002) yang tidak pernah
# dijalankan otomatis — sehingga fitur Invoice Penjualan dengan PPh23 aktif
# gagal di database yang tidak punya entri itu, dengan pesan minta menjalankan
# seeder yang bahkan tidak relevan dengan COA v2.
# Solusi: mapping kandidat akun pengganti per key. Saat setting belum ada,
# kandidat dicocokkan berurutan (kode akun → nama eksak → nama ilike) dengan
# filter akun DETAIL + allow_system_posting + aktif. Bila ketemu, baris
# setting dibuat OTOMATIS (auto_configure_missing) — baik saat startup
# (ensure_fallback_settings) maupun lazy saat transaksi butuh (get_akun_id_or_raise).
# Setting yang SUDAH ada tidak pernah diubah oleh mekanisme ini.
SETTING_KEY_FALLBACK_CANDIDATES: dict[str, dict[str, list[str]]] = {
    # PPh 23 Dibayar di Muka — COA v2 kode 116003 (legacy 150100002 / 150.100.002)
    KEY_PPH23_DIBAYAR_DIMUKA: {
        "kode": ["116003", "150100002", "150.100.002"],
        "nama_exact": ["PPh 23 Dibayar di Muka", "PPh 23 Dibayar Dimuka"],
        "nama_like": ["%pph 23 dibayar%"],
    },
    # === Update #01 — Hutang PPh 23 untuk pelunasan hutang ===
    # COA v2 kode 213002 (di bawah group 213000 Hutang Pajak).
    KEY_HUTANG_PPH23: {
        "kode": ["213002", "213.002", "215200002"],
        "nama_exact": ["Hutang PPh 23", "Hutang PPh23"],
        "nama_like": ["%hutang pph 23%", "%hutang pph23%"],
    },
    # === Update #02 — akun penampung mutasi kas Belum Teridentifikasi ===
    # Default COA v2 kode 216000 (dibuat migration cp0000000012 di bawah
    # 210000 KEWAJIBAN JANGKA PENDEK). nama_like longgar supaya perusahaan
    # dengan akun penampung/suspense custom tetap ter-match otomatis.
    KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI: {
        "kode": ["216000"],
        "nama_exact": ["Hutang Belum Teridentifikasi (Penampung)"],
        "nama_like": ["%belum teridentifikasi%", "%penampung%", "%suspense%"],
    },
    # === Update #03 — penampung penerimaan (liabilitas, AR kontrol sementara) ===
    KEY_KAS_PENAMPUNG_PENERIMAAN: {
        "kode": ["216001"],
        "nama_exact": ["Penerimaan Kas Belum Teridentifikasi (Penampung)"],
        "nama_like": ["%penerimaan belum teridentifikasi%"],
    },
    # === Update #03 — penampung pembayaran (aset, AP kontrol sementara) ===
    KEY_KAS_PENAMPUNG_PEMBAYARAN: {
        "kode": ["117004"],
        "nama_exact": ["Pembayaran Kas Belum Teridentifikasi (Penampung)"],
        "nama_like": ["%pembayaran belum teridentifikasi%"],
    },
}


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


def _find_fallback_akun(db: Session, key: str) -> Optional[UUID]:
    """Cari akun kandidat untuk key setting yang belum dikonfigurasi.

    Urutan pencocokan: kode akun → nama eksak → nama ilike.
    Hanya akun DETAIL + allow_system_posting + aktif yang lolos, supaya
    tidak salah mengambil akun GROUP / header / akun utang pajak.
    """
    spec = SETTING_KEY_FALLBACK_CANDIDATES.get(key)
    if not spec:
        return None
    base_q = db.query(AkunPerkiraan).filter(
        AkunPerkiraan.tingkat == TingkatAkun.DETAIL,
        AkunPerkiraan.allow_system_posting.is_(True),
        AkunPerkiraan.active.is_(True),
    )
    for kode in spec.get("kode", []):
        akun = base_q.filter(AkunPerkiraan.kode == kode).first()
        if akun:
            return akun.id
    for nama in spec.get("nama_exact", []):
        akun = base_q.filter(AkunPerkiraan.nama == nama).first()
        if akun:
            return akun.id
    for pattern in spec.get("nama_like", []):
        akun = base_q.filter(AkunPerkiraan.nama.ilike(pattern)).first()
        if akun:
            return akun.id
    return None


def auto_configure_missing(db: Session, key: str) -> Optional[UUID]:
    """Auto-configure setting `key` bila belum ada dan kandidat ditemukan.

    Perilaku:
    - Setting yang SUDAH ada tidak pernah diubah (return id-nya saja).
    - Baris setting baru di-add + flush() mengikuti transaksi pemanggil
      (tidak commit di sini) — aman di-rollback bersama transaksi gagal.
    - Cache in-memory langsung di-update supaya get_akun_id() melihatnya.

    Update #02 — hardening: sebelum insert, baris di-cek ulang langsung ke
    DB (bukan hanya cache). Cache bisa stale bila setting dibuat di luar
    proses ini (migration, worker lain, sesi paralel) — tanpa re-check,
    auto-configure akan INSERT duplikat yang memicu UniqueViolation pada
    key yang sama (kasus: migration cp0000000012 menambah setting
    KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI setelah cache startup termuat).
    """
    if not _cache:
        _load_all_settings(db)
    if _cache.get(key):
        return _cache[key]
    # Re-check langsung ke DB — cache in-memory bisa stale.
    existing = db.query(SettingAkun).filter(SettingAkun.key == key).first()
    if existing is not None:
        _cache[key] = existing.akun_perkiraan_id
        return _cache[key]
    akun_id = _find_fallback_akun(db, key)
    if not akun_id:
        return None
    label = KNOWN_SETTING_LABELS.get(key, key)
    db.add(SettingAkun(key=key, label=label, akun_perkiraan_id=akun_id))
    db.flush()
    _cache[key] = akun_id
    logger.debug(f"Setting akun '{key}' auto-configured ke akun {akun_id}")
    return akun_id


def ensure_fallback_settings(db: Session) -> list[str]:
    """Coba auto-configure semua key yang punya kandidat fallback.

    Dipanggil dari startup hook aplikasi (idempotent). COMMIT dilakukan
    oleh pemanggil.
    Return: list key yang BENAR-BENAR baru dikonfigurasi (yang sudah ada
    di-skip, supaya log tidak berisik).
    """
    configured: list[str] = []
    for key in SETTING_KEY_FALLBACK_CANDIDATES:
        sudah = _cache.get(key) if _cache else None
        if not sudah:
            _load_all_settings(db)
            sudah = _cache.get(key)
        if sudah:
            continue
        if auto_configure_missing(db, key):
            configured.append(key)
    return configured


def get_setting_akun_id_optional(db: Session, key: str) -> Optional[UUID]:
    """Update #03 — baca setting akun TANPA raise dan TANPA auto-configure.

    Dipakai untuk pemilihan akun penampung arah-spesifik: bila setting belum
    ada, pemanggil boleh jatuh ke fallback lain (mis. key gabungan Update #02)
    tanpa memicu auto-configure yang bisa memilih kandidat salah arah.
    Cache lokal dibaca/diisi agar tidak query berulang dalam satu request.
    """
    if key in _cache:
        return _cache[key] or None
    row = db.query(SettingAkun.akun_perkiraan_id).filter(SettingAkun.key == key).first()
    _cache[key] = row[0] if row else None
    return _cache[key]


def get_akun_id_or_raise(db: Session, key: str, context: str = "") -> UUID:
    """Ambil UUID akun perkiraan, raise ValueError jika tidak ditemukan.

    Parameter:
        key: Kunci setting (misal 'PENDAPATAN_PENJUALAN')
        context: Keterangan tambahan untuk error message

    ASAHI #006: bila belum dikonfigurasi, coba auto-configure dulu via
    kandidat fallback COA (mis. PPH23_DIBAYAR_DIMUKA → akun 116003) baru
    raise. Pesan error mengarahkan ke menu Pengaturan → Setting Akun,
    bukan lagi ke seeder legacy phase3 yang tidak relevan dgn COA v2.

    Return:
        UUID akun_perkiraan

    Raises:
        ValueError: Jika akun belum di-configure dan tidak ada kandidat
    """
    akun_id = get_akun_id(db, key)
    if not akun_id:
        akun_id = auto_configure_missing(db, key)
    if not akun_id:
        ctx = f" ({context})" if context else ""
        raise ValueError(
            f"Setting akun '{key}' belum di-configure dan tidak ditemukan "
            f"kandidat akun otomatis di COA. Konfigurasikan melalui menu "
            f"Pengaturan \u2192 Setting Akun (key: {key}).{ctx}"
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