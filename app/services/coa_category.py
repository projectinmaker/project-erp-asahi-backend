"""Klasifikasi kategori COA untuk fitur Saldo Awal (modul Akun Perkiraan).

Sesuai catatan update:
- Input saldo awal cukup SATU nilai + tanggal; sisi debit/kredit otomatis
  mengikuti saldo normal akun.
- Fitur saldo awal hanya berlaku untuk 9 kategori:
  Kas dan Bank, Aset Lancar Lainnya, Kewajiban Lainnya, Modal, Pendapatan,
  HPP, Beban, Pendapatan Lainnya, Beban Lainnya.
- Kategori lain dialihkan ke modul terkait (Piutang -> Pelanggan, Persediaan
  -> modul Stok, Aset Tetap -> modul Aset Tetap, Hutang Usaha -> Supplier,
  saldo laba -> dihitung sistem, dll).
- Selisih total debit/kredit jurnal saldo awal dipampangkan otomatis ke akun
  "Selisih Saldo Awal" (system_account_type=OPENING_BALANCE_DIFF) di Modal.

Klasifikasi memakai atribut akun dengan prioritas:
1. system_account_type (override spesifik: AR/AP control, clearing,
   saldo laba/tahun berjalan, legacy COGS, akun penampung).
2. account_subclass (CASH_BANK, INVENTORY_*, FIXED_ASSET_COST, CONTRA_ASSET).
3. Prefiks kode akun (akun sendiri dulu, lalu kode induk) — fallback untuk
   akun tanpa subclass (mis. 115xxx Uang Muka, 213xxx Hutang Pajak).
"""

# System account type akun penampung selisih saldo awal
OPENING_BALANCE_DIFF_SYSTEM_TYPE = "OPENING_BALANCE_DIFF"

# ── Kunci kategori ────────────────────────────────────────────────────────
KAS_DAN_BANK = "KAS_DAN_BANK"
PIUTANG_USAHA = "PIUTANG_USAHA"
PIUTANG_LAIN_LAIN = "PIUTANG_LAIN_LAIN"
PERSEDIAAN = "PERSEDIAAN"
ASET_LANCAR_LAINNYA = "ASET_LANCAR_LAINNYA"
ASET_TETAP = "ASET_TETAP"
HUTANG_USAHA = "HUTANG_USAHA"
KEWAJIBAN_LAINNYA = "KEWAJIBAN_LAINNYA"
KEWAJIBAN_JANGKA_PANJANG = "KEWAJIBAN_JANGKA_PANJANG"
MODAL = "MODAL"
MODAL_SISTEM = "MODAL_SISTEM"
PENDAPATAN = "PENDAPATAN"
PENDAPATAN_LAINNYA = "PENDAPATAN_LAINNYA"
HPP = "HPP"
HPP_LEGACY = "HPP_LEGACY"
BEBAN = "BEBAN"
BEBAN_LAINNYA = "BEBAN_LAINNYA"
CLEARING = "CLEARING"
PENAMPUNG_SELISIH = "PENAMPUNG_SELISIH"

# ── Metadata kategori ─────────────────────────────────────────────────────
# saldo_normal di sini adalah pemetaan kategori sesuai catatan update
# (Kas dan Bank/Aset Lancar Lainnya/HPP/Beban/Beban Lainnya = DEBIT;
#  Kewajiban Lainnya/Modal/Pendapatan/Pendapatan Lainnya = KREDIT).
# CATATAN: penentuan sisi jurnal tetap memakai saldo normal AKUN
# (account.saldo_normal) supaya akun kontra (mis. Dividen D di Modal)
# tetap diposting ke sisi yang benar.
CATEGORY_INFO = {
    KAS_DAN_BANK: {
        "label": "Kas dan Bank",
        "saldo_normal": "DEBIT",
        "saldo_awal": True,
    },
    PIUTANG_USAHA: {
        "label": "Piutang Usaha",
        "saldo_normal": "DEBIT",
        "saldo_awal": False,
        "hint": "Saldo awal piutang diatur melalui modul Pelanggan (piutang per pelanggan).",
    },
    PIUTANG_LAIN_LAIN: {
        "label": "Piutang Lain-lain",
        "saldo_normal": "DEBIT",
        "saldo_awal": False,
        "hint": "Piutang lain-lain tidak didukung saldo awal langsung.",
    },
    PERSEDIAAN: {
        "label": "Persediaan",
        "saldo_normal": "DEBIT",
        "saldo_awal": False,
        "hint": "Saldo awal persediaan diatur melalui modul Persediaan (stok awal / penyesuaian per barang).",
    },
    ASET_LANCAR_LAINNYA: {
        "label": "Aset Lancar Lainnya",
        "saldo_normal": "DEBIT",
        "saldo_awal": True,
    },
    ASET_TETAP: {
        "label": "Aset Tetap",
        "saldo_normal": "DEBIT",
        "saldo_awal": False,
        "hint": "Saldo awal aset tetap diatur melalui modul Aset Tetap (nilai perolehan & akumulasi awal).",
    },
    HUTANG_USAHA: {
        "label": "Hutang Usaha",
        "saldo_normal": "KREDIT",
        "saldo_awal": False,
        "hint": "Saldo awal hutang diatur melalui modul Supplier (hutang per supplier).",
    },
    KEWAJIBAN_LAINNYA: {
        "label": "Kewajiban Lainnya",
        "saldo_normal": "KREDIT",
        "saldo_awal": True,
    },
    KEWAJIBAN_JANGKA_PANJANG: {
        "label": "Kewajiban Jangka Panjang",
        "saldo_normal": "KREDIT",
        "saldo_awal": False,
        "hint": "Hutang jangka panjang tidak didukung saldo awal langsung.",
    },
    MODAL: {
        "label": "Modal",
        "saldo_normal": "KREDIT",
        "saldo_awal": True,
    },
    MODAL_SISTEM: {
        "label": "Modal (Saldo Laba)",
        "saldo_normal": "KREDIT",
        "saldo_awal": False,
        "hint": "Nilai laba ditahan / laba tahun berjalan dihitung otomatis oleh sistem.",
    },
    PENDAPATAN: {
        "label": "Pendapatan",
        "saldo_normal": "KREDIT",
        "saldo_awal": True,
    },
    PENDAPATAN_LAINNYA: {
        "label": "Pendapatan Lainnya",
        "saldo_normal": "KREDIT",
        "saldo_awal": True,
    },
    HPP: {
        "label": "HPP",
        "saldo_normal": "DEBIT",
        "saldo_awal": True,
    },
    HPP_LEGACY: {
        "label": "HPP (Legacy)",
        "saldo_normal": "DEBIT",
        "saldo_awal": False,
        "hint": "Akun legacy terkunci dan tidak bisa dipakai transaksi baru.",
    },
    BEBAN: {
        "label": "Beban",
        "saldo_normal": "DEBIT",
        "saldo_awal": True,
    },
    BEBAN_LAINNYA: {
        "label": "Beban Lainnya",
        "saldo_normal": "DEBIT",
        "saldo_awal": True,
    },
    CLEARING: {
        "label": "Akun Clearing",
        "saldo_normal": "DEBIT",
        "saldo_awal": False,
        "hint": "Akun clearing harus selalu nol — tidak didukung saldo awal.",
    },
    PENAMPUNG_SELISIH: {
        "label": "Penampung Selisih Saldo Awal",
        "saldo_normal": "KREDIT",
        "saldo_awal": False,
        "hint": "Akun penampung selisih saldo awal — dikelola otomatis oleh sistem.",
    },
}

# Prefiks kode 3 digit -> kategori (dicek sebelum prefiks 2 digit)
_PREFIX3_MAP = {
    "111": KAS_DAN_BANK,
    "112": PIUTANG_USAHA,
    "113": PIUTANG_LAIN_LAIN,
    "114": PERSEDIAAN,
    "115": ASET_LANCAR_LAINNYA,
    "116": ASET_LANCAR_LAINNYA,
    "117": ASET_LANCAR_LAINNYA,
    "121": ASET_TETAP,
    "122": ASET_TETAP,
    "211": HUTANG_USAHA,
}

# Prefiks kode 2 digit -> kategori (fallback)
_PREFIX2_MAP = {
    "11": ASET_LANCAR_LAINNYA,
    "12": ASET_TETAP,
    "21": KEWAJIBAN_LAINNYA,
    "22": KEWAJIBAN_JANGKA_PANJANG,
    "31": MODAL,
    "32": MODAL_SISTEM,
    "33": MODAL,
    "41": PENDAPATAN,
    "42": PENDAPATAN_LAINNYA,
    "51": HPP_LEGACY,
    "52": HPP,
    "53": HPP,
    "61": BEBAN,
    "62": BEBAN,
    "63": BEBAN_LAINNYA,
    "71": BEBAN_LAINNYA,
}

_INVENTORY_SUBCLASSES = {
    "INVENTORY_RAW",
    "INVENTORY_AUX",
    "INVENTORY_WIP",
    "INVENTORY_FINISHED",
}


def classify_coa_category(
    kode=None,
    induk_kode=None,
    account_subclass=None,
    system_account_type=None,
):
    """Klasifikasikan akun ke salah satu kategori COA (atau None bila tak dikenali)."""
    # 1. Override berdasarkan system_account_type
    if system_account_type:
        if system_account_type == "AR_CONTROL":
            return PIUTANG_USAHA
        if system_account_type == "AP_CONTROL":
            return HUTANG_USAHA
        if system_account_type == "BANK_CLEARING":
            return CLEARING
        if system_account_type in ("RETAINED_EARNINGS", "CURRENT_EARNINGS"):
            return MODAL_SISTEM
        if system_account_type == "LEGACY_COGS_PURCHASE":
            return HPP_LEGACY
        if system_account_type == OPENING_BALANCE_DIFF_SYSTEM_TYPE:
            return PENAMPUNG_SELISIH

    # 2. Klasifikasi berdasarkan account_subclass
    if account_subclass:
        if account_subclass == "CASH_BANK":
            return KAS_DAN_BANK
        if account_subclass in _INVENTORY_SUBCLASSES:
            return PERSEDIAAN
        if account_subclass in ("FIXED_ASSET_COST", "CONTRA_ASSET"):
            return ASET_TETAP

    # 3. Fallback prefiks kode (akun sendiri dulu, lalu induk)
    for source in (kode, induk_kode):
        if not source:
            continue
        text = str(source).strip()
        if len(text) < 2 or not text[:2].isdigit():
            continue
        prefix3 = text[:3]
        if prefix3 in _PREFIX3_MAP:
            return _PREFIX3_MAP[prefix3]
        prefix2 = text[:2]
        if prefix2 in _PREFIX2_MAP:
            return _PREFIX2_MAP[prefix2]

    return None


def classify_account(account):
    """Klasifikasikan objek AkunPerkiraan (atau dict ber atribut sama)."""
    return classify_coa_category(
        kode=getattr(account, "kode", None),
        induk_kode=getattr(account, "induk_kode", None),
        account_subclass=getattr(account, "account_subclass", None),
        system_account_type=getattr(account, "system_account_type", None),
    )


def get_category_info(category):
    """Metadata kategori (label, saldo normal, kelayakan saldo awal, hint)."""
    return CATEGORY_INFO.get(category) if category else None


def is_saldo_awal_eligible(category):
    """True hanya untuk 9 kategori yang didukung fitur saldo awal."""
    info = CATEGORY_INFO.get(category) if category else None
    return bool(info and info.get("saldo_awal"))
