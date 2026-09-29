"""
ItemTypePolicy — registry terpusat jenis item UI (Task 27-c).

Sesuai spesifikasi REVISI_DYNAMIC_FORM_BARANG_JASA_ASAHI.md §1/§3/§5:
jenis item menentukan field frontend, validasi backend, dan izin transaksi.
Backend tetap sumber kebenaran — menyembunyikan field di UI tidak cukup.

Empat pilihan UI (spec §1):
- PERSEDIAAN    → item_type BARANG_DAGANG/JADI/BAKU/BANTU + stock_item True
- NONPERSEDIAAN → item_type BARANG_DAGANG + stock_item False
- JASA          → item_type JASA + stock_item False
- GRUP          → non-stock sales bundle; engine bundle belum ada →
                  available=False / coming soon (fase 1 spec §1: jangan
                  mengaktifkan transaksi grup sebelum engine & UAT tersedia).

Mapping memakai kolom yang SUDAH ADA di model Barang (item_type, stock_item) —
tanpa perubahan schema/DB.
"""

from typing import List, Optional, Tuple

from app.models.master.barang import ItemTypeBarang

# ── Konstanta UI type ────────────────────────────────────────────────────────
UI_TYPE_PERSEDIAAN = 'PERSEDIAAN'
UI_TYPE_NONPERSEDIAAN = 'NONPERSEDIAAN'
UI_TYPE_JASA = 'JASA'
UI_TYPE_GRUP = 'GRUP'

# Rincian jenis yang hanya berlaku untuk item persediaan (stock-tracked).
RINCIAN_PERSEDIAAN = (
    ItemTypeBarang.BARANG_DAGANG,
    ItemTypeBarang.BARANG_JADI,
    ItemTypeBarang.BARANG_BAKU,
    ItemTypeBarang.BARANG_BANTU,
)

# ── Registry policy per UI type ──────────────────────────────────────────────
ITEM_TYPE_POLICY = {
    UI_TYPE_PERSEDIAAN: dict(
        label='Persediaan',
        available=True,
        stock_tracked=True,
        valuation_enabled=True,
        supports_warehouse=True,
        can_purchase=True,
        can_sell=True,
        visible_tabs=['umum', 'jual_beli', 'stok', 'akun', 'gambar', 'lainnya'],
        account_fields=['akun_persediaan_id', 'akun_hpp_id', 'akun_penjualan_id'],
        stock_fields=['metode_valuasi', 'stok_minimum'],
        description='Barang fisik yang stok-nya dilacak (kartu stok, valuasi, gudang).',
    ),
    UI_TYPE_NONPERSEDIAAN: dict(
        label='Nonpersediaan',
        available=True,
        stock_tracked=False,
        valuation_enabled=False,
        supports_warehouse=False,
        can_purchase=True,
        can_sell=True,
        visible_tabs=['umum', 'jual_beli', 'akun', 'gambar', 'lainnya'],
        account_fields=['akun_hpp_id', 'akun_penjualan_id'],
        stock_fields=[],
        description='Barang/jasa terjual-terbeli tanpa pelacakan stok (tanpa kartu stok/valuasi).',
    ),
    UI_TYPE_JASA: dict(
        label='Jasa',
        available=True,
        stock_tracked=False,
        valuation_enabled=False,
        supports_warehouse=False,
        can_purchase=True,
        can_sell=True,
        visible_tabs=['umum', 'jual_beli', 'akun', 'gambar', 'lainnya'],
        account_fields=['akun_penjualan_id'],
        stock_fields=[],
        description='Item jasa — pendapatan/beban tanpa gerakan stok, gudang, atau valuasi.',
    ),
    UI_TYPE_GRUP: dict(
        label='Grup',
        available=False,
        stock_tracked=False,
        valuation_enabled=False,
        supports_warehouse=False,
        can_purchase=False,
        can_sell=False,
        visible_tabs=['umum'],
        account_fields=[],
        stock_fields=[],
        coming_soon=True,
        description='Non-stock sales bundle — engine bundle belum tersedia (fase 1).',
    ),
}


def derive_ui_type(item_type, stock_item) -> str:
    """Turunkan UI type dari nilai model (item_type + stock_item).

    Aturan (Task 27-c):
    - item_type == JASA                  → 'JASA'
    - stock_item truthy                  → 'PERSEDIAAN'
    - selain itu (termasuk legacy None)  → 'NONPERSEDIAAN'
    """
    if item_type == ItemTypeBarang.JASA or item_type == 'JASA':
        return UI_TYPE_JASA
    if stock_item:
        return UI_TYPE_PERSEDIAAN
    return UI_TYPE_NONPERSEDIAAN


def ui_type_to_model(ui_type: str, rincian_jenis=None) -> Tuple[Optional[ItemTypeBarang], bool]:
    """Konversi UI type → (item_type enum, stock_item bool) untuk model Barang.

    rincian_jenis hanya dipakai untuk PERSEDIAAN (BARANG_DAGANG/JADI/BAKU/BANTU);
    default BARANG_DAGANG bila kosong/tidak dikenal.
    """
    if ui_type == UI_TYPE_PERSEDIAAN:
        rincian = rincian_jenis if rincian_jenis in RINCIAN_PERSEDIAAN else ItemTypeBarang.BARANG_DAGANG
        return rincian, True
    if ui_type == UI_TYPE_NONPERSEDIAAN:
        return ItemTypeBarang.BARANG_DAGANG, False
    if ui_type == UI_TYPE_JASA:
        return ItemTypeBarang.JASA, False
    if ui_type == UI_TYPE_GRUP:
        raise ValueError('Jenis item Grup belum tersedia (coming soon — engine bundle belum diimplementasikan)')
    raise ValueError(f'Jenis item tidak dikenal: {ui_type!r}')


def get_policy(ui_type: str) -> dict:
    """Ambil policy untuk sebuah UI type. Unknown → policy kosong unavailable."""
    return ITEM_TYPE_POLICY.get(ui_type, dict(label=ui_type, available=False, stock_tracked=False,
                                              valuation_enabled=False, supports_warehouse=False,
                                              can_purchase=False, can_sell=False, visible_tabs=['umum'],
                                              account_fields=[], stock_fields=[]))


def types_payload() -> List[dict]:
    """Payload camelCase untuk GET /master/barang/types (spec §6: items/types)."""
    result = []
    for ui_type in (UI_TYPE_PERSEDIAAN, UI_TYPE_NONPERSEDIAAN, UI_TYPE_JASA, UI_TYPE_GRUP):
        p = ITEM_TYPE_POLICY[ui_type]
        entry = {
            'uiType': ui_type,
            'label': p['label'],
            'available': p['available'],
            'stockTracked': p['stock_tracked'],
            'valuationEnabled': p['valuation_enabled'],
            'supportsWarehouse': p['supports_warehouse'],
            'canPurchase': p['can_purchase'],
            'canSell': p['can_sell'],
            'visibleTabs': list(p['visible_tabs']),
            'accountFields': list(p['account_fields']),
            'stockFields': list(p['stock_fields']),
            'description': p.get('description'),
        }
        if p.get('coming_soon'):
            entry['comingSoon'] = True
        result.append(entry)
    return result
