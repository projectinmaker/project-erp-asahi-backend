"""
Helper untuk mapping RefModule enum → label & group yang ramah pengguna
(frontend display).

Dipakai oleh:
- Endpoint GET /api/v1/jurnal/ref-modules (frontend dropdown filter)
- Bisa juga dipakai langsung untuk display di list jurnal response (kalau
  nanti ingin menambahkan field `ref_module_label` di schema response)
"""

from typing import Optional
from app.models.transaksi.jurnal import RefModule


# Mapping RefModule -> (label, group, is_legacy)
# - label: tampilan ramah pengguna (Indonesia, Title Case)
# - group: kategori untuk dropdown grouping di frontend
# - is_legacy: True kalau enum ini legacy (sudah tidak dipakai untuk transaksi
#   baru, tapi masih ada di DB untuk historical data). Frontend bisa pakai
#   flag ini untuk hide dari dropdown filter "transaksi baru" tapi tetap
#   show di dropdown "filter jurnal historis".
REF_MODULE_INFO: dict[RefModule, tuple[str, str, bool]] = {
    # === ACCOUNTING / GL ===
    RefModule.MANUAL:            ("Jurnal Manual",        "Accounting",        False),
    RefModule.SALDO_AWAL:        ("Saldo Awal",            "Accounting",        False),
    RefModule.PENUTUPAN_PERIODE: ("Penutupan Periode",    "Accounting",        False),

    # === PENJUALAN (SALES) ===
    RefModule.SALES_ORDER:       ("Sales Order",           "Penjualan",         False),
    RefModule.SALES_DELIVERY:    ("Pengiriman Barang",     "Penjualan",         False),
    RefModule.SALES_INVOICE:     ("Faktur Penjualan",     "Penjualan",         False),
    RefModule.SALES_RETUR:      ("Retur Penjualan",       "Penjualan",         False),
    RefModule.AR_SETTLEMENT:     ("Pelunasan Piutang",    "Penjualan",         False),

    # === PEMBELIAN (PURCHASE) ===
    RefModule.PURCHASE_ORDER:    ("Purchase Order",        "Pembelian",         False),
    RefModule.PURCHASE_RECEIPT:  ("Penerimaan Barang",    "Pembelian",         False),
    RefModule.PURCHASE_INVOICE:  ("Faktur Pembelian",      "Pembelian",         False),
    RefModule.PURCHASE_RETUR:   ("Retur Pembelian",       "Pembelian",         False),
    RefModule.AP_SETTLEMENT:     ("Pelunasan Hutang",     "Pembelian",         False),

    # === INVENTORY ===
    RefModule.INVENTORY_ADJUSTMENT: ("Penyesuaian Stok",  "Persediaan",        False),
    RefModule.INVENTORY_TRANSFER:   ("Pemindahan Barang", "Persediaan",        False),

    # === ASET TETAP (FIXED ASSET) ===
    RefModule.ASSET_CAPITALIZATION: ("Kapitalisasi Aset", "Aset Tetap",       False),
    RefModule.ASSET_DEPRECIATION:   ("Penyusutan Aset",   "Aset Tetap",       False),
    RefModule.ASSET_DISPOSAL:       ("Penghentian Aset",  "Aset Tetap",       False),

    # === KAS & BANK ===
    RefModule.BANK_TRANSFER:     ("Transfer Bank",        "Kas & Bank",       False),
    RefModule.BANK_RECONCILIATION: ("Rekonsiliasi Bank",  "Kas & Bank",       False),

    # === LEGACY (jangan dipakai untuk transaksi baru) ===
    RefModule.PEMBAYARAN:        ("Pembayaran (Legacy)",       "Legacy",  True),
    RefModule.PENERIMAAN:        ("Penerimaan (Legacy)",       "Legacy",  True),
    RefModule.TRANSFER_BANK:     ("Transfer Bank (Legacy)",   "Legacy",  True),
    RefModule.PENYESUAIAN_STOK:  ("Penyesuaian Stok (Legacy)", "Legacy",  True),
    RefModule.PENYUSUTAN:         ("Penyusutan (Legacy)",       "Legacy",  True),
    RefModule.REKONSILIASI_BANK: ("Rekonsiliasi Bank (Legacy)","Legacy",  True),
}


def get_ref_module_label(ref_module: Optional[RefModule]) -> str:
    """Return label ramah pengguna untuk RefModule.
    Return "—" kalau ref_module None, atau "(Unknown)" kalau enum tidak dikenal.
    """
    if ref_module is None:
        return "—"
    info = REF_MODULE_INFO.get(ref_module)
    return info[0] if info else f"(Unknown: {ref_module.value})"


def get_ref_module_group(ref_module: Optional[RefModule]) -> str:
    """Return group/kategori untuk RefModule (mis. 'Penjualan', 'Pembelian')."""
    if ref_module is None:
        return "Lainnya"
    info = REF_MODULE_INFO.get(ref_module)
    return info[1] if info else "Lainnya"


def is_legacy_ref_module(ref_module: Optional[RefModule]) -> bool:
    """True kalau enum ini legacy (sudah tidak dipakai untuk transaksi baru)."""
    if ref_module is None:
        return False
    info = REF_MODULE_INFO.get(ref_module)
    return info[2] if info else False


def list_ref_modules(include_legacy: bool = True) -> list[dict]:
    """Return list of RefModule info dicts for frontend dropdown.

    Format per item:
        {
            "value": "SALES_DELIVERY",
            "label": "Pengiriman Barang",
            "group": "Penjualan",
            "is_legacy": false
        }
    """
    result = []
    for ref_module in RefModule:
        info = REF_MODULE_INFO.get(ref_module)
        if info is None:
            continue
        label, group, is_legacy = info
        if not include_legacy and is_legacy:
            continue
        result.append({
            "value": ref_module.value,
            "label": label,
            "group": group,
            "is_legacy": is_legacy,
        })
    return result
