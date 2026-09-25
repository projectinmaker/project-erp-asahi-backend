"""fix_relasi_coa_module_settings

Data migration — perbaiki relasi modul ↔ akun perkiraan (COA) yang tidak
konsisten (temuan audit COA-relations):

1. Setting PERSEDIAAN_* (fallback akun persediaan untuk barang TANPA mapping
   per-barang) masih menunjuk akun legacy 131100001-131100004 yang tidak
   ber-account_subclass INVENTORY_* — akun itu tidak muncul di dropdown
   mapping barang & tidak tercakup laporan rekonsiliasi persediaan per akun.
   → pindahkan ke akun kanonis 114001/114002/114003/114004.

2. Setting LABA_RUGI_BERJALAN (dipakai jurnal penutupan periode bulanan di
   app/services/penutupan_periode_service.py, wajib menunjuk akun MODAL)
   belum dikonfigurasi → tutup periode akan gagal saat eksekusi.
   → buat/arahkan ke akun 322000 'Laba (Rugi) Tahun Berjalan'.

3. Akun 600100006 'Beban Penyusutan' belum ber-system_account_type
   DEPRECIATION_EXPENSE — satu-satunya sys-type yang difilter dropdown
   "Akun Beban Penyusutan" pada form Kategori Aset → dropdown kosong,
   form tidak bisa disimpan.
   → set system_account_type = 'DEPRECIATION_EXPENSE'.

Idempotent: hanya menjalankan bila kondisi lama masih terpenuhi; akun
dicari berdasarkan KODE (stabil antar environment), bukan UUID.

Revision ID: h9i0j1k2l3m4
Revises: f4g5h6i7j8k9
Create Date: 2026-09-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "h9i0j1k2l3m4"
down_revision: Union[str, Sequence[str], None] = "f4g5h6i7j8k9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# key setting → (kode akun lama/legacy yang boleh dioverride, kode akun baru, label)
SETTING_FIXES = [
    ("PERSEDIAAN_BAHAN_BAKU", ("131100001",), "114001", "Persediaan Bahan Baku"),
    ("PERSEDIAAN_BAHAN_PEMBANTU", ("131100004",), "114002", "Persediaan Bahan Pembantu"),
    ("PERSEDIAAN_WIP", ("131100002",), "114003", "Persediaan Barang Dalam Proses (WIP)"),
    ("PERSEDIAAN_BARANG_JADI", ("131100003",), "114004", "Persediaan Barang Jadi"),
    ("LABA_RUGI_BERJALAN", (), "322000", "Laba Rugi Berjalan"),
]


def _akun_id_by_kode(bind, kode):
    row = bind.execute(
        sa.text("SELECT id FROM akun_perkiraan WHERE kode = :kode LIMIT 1"),
        {"kode": kode},
    ).first()
    return row[0] if row else None


def _current_setting(bind, key):
    return bind.execute(
        sa.text("SELECT akun_perkiraan_id FROM setting_akun WHERE key = :k LIMIT 1"),
        {"k": key},
    ).first()


def upgrade() -> None:
    bind = op.get_bind()

    for key, legacy_kodes, new_kode, label in SETTING_FIXES:
        new_id = _akun_id_by_kode(bind, new_kode)
        if new_id is None:
            # COA target tidak ada di environment ini — skip (tidak fatal).
            print(f"[h9i0j1k2l3m4] SKIP {key}: akun {new_kode} tidak ditemukan")
            continue

        cur = _current_setting(bind, key)
        if cur is None:
            # Key belum ada → buat baris setting baru.
            bind.execute(
                sa.text(
                    "INSERT INTO setting_akun (id, key, label, akun_perkiraan_id, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :k, :label, :akun_id, now(), now())"
                ),
                {"k": key, "label": label, "akun_id": new_id},
            )
            print(f"[h9i0j1k2l3m4] INSERT setting {key} -> akun {new_kode}")
            continue

        # Key sudah ada — timpa HANYA bila masih menunjuk akun legacy/kosong.
        cur_kode_row = bind.execute(
            sa.text("SELECT kode FROM akun_perkiraan WHERE id = :id LIMIT 1"),
            {"id": cur[0]},
        ).first()
        cur_kode = cur_kode_row[0] if cur_kode_row else None
        if cur_kode in legacy_kodes or cur_kode is None:
            bind.execute(
                sa.text("UPDATE setting_akun SET akun_perkiraan_id = :akun_id, updated_at = now() WHERE key = :k"),
                {"akun_id": new_id, "k": key},
            )
            print(f"[h9i0j1k2l3m4] UPDATE setting {key}: {cur_kode} -> {new_kode}")
        else:
            print(f"[h9i0j1k2l3m4] SKIP {key}: sudah menunjuk akun {cur_kode} (bukan legacy)")

    # 3) sys-type DEPRECIATION_EXPENSE untuk akun beban penyusutan umum
    res = bind.execute(
        sa.text(
            "UPDATE akun_perkiraan SET system_account_type = 'DEPRECIATION_EXPENSE', updated_at = now() "
            "WHERE kode = '600100006' AND nama ILIKE '%penyusutan%' "
            "AND (system_account_type IS NULL OR system_account_type = '')"
        )
    )
    print(f"[h9i0j1k2l3m4] sys-type DEPRECIATION_EXPENSE 600100006: {res.rowcount} baris")


def downgrade() -> None:
    # Data fix tidak di-reverse (nilai lama tidak deterministik antar environment);
    # pengembalian setting dilakukan manual via UI Setting Akun bila diperlukan.
    pass
