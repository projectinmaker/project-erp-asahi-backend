"""update047_seed_rate_pajak

=== Update #047 — seed pengaturan tarif pajak RATE_PPN / RATE_PPH23 ===

Diagnosis error 404 di konsol browser:

    GET /api/v1/master/app-setting/RATE_PPN   → 404
    GET /api/v1/master/app-setting/RATE_PPH23 → 404

Rute endpoint-nya ADA (tanpa login balas 403 = rute ditemukan), artinya
404 terjadi karena BARIS setting-nya belum pernah dibuat di database —
halaman Referensi → Perpajakan belum pernah disimpan. Form invoice yang
menarik tarif terkunci (Update #046) pun akhirnya memakai fallback 11/2,
bukan nilai pengaturan.

Migrasi ini menambahkan baris RATE_PPN=11 dan RATE_PPH23=2 (hanya bila
belum ada) supaya GET mengembalikan 200 dan 404 hilang. DEFENSIF:
- nama tabel dicari di kandidat (app_setting / app_settings);
- kolom kunci & nilai dideteksi (key/value atau kunci/nilai);
- bila ada kolom wajib lain tanpa default (di luar id), seed DILEWATI
  (no-op) — pengguna cukup menyimpan sekali lewat halaman
  Referensi → Perpajakan untuk membuat barisnya.
Nilai di migrasi hanyalah default awal — tetap bisa diubah kapan saja
lewat halaman Perpajakan.
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "cp0000000035"
down_revision: Union[str, Sequence[str], None] = "cp0000000034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kandidat nama tabel & kolom (deteksi — tidak mengarang struktur).
_TABLE_CANDIDATES = ("app_setting", "app_settings")
_KEY_CANDIDATES = ("key", "kunci", "name")
_VALUE_CANDIDATES = ("value", "nilai")
_SEED_ROWS = (("RATE_PPN", "11"), ("RATE_PPH23", "2"))


def _find_setting_table(insp) -> tuple[str | None, str | None, str | None, str | None]:
    """Cari tabel setting + nama kolom kunci/nilai + kebutuhan kolom id.

    Return (table, key_col, value_col, id_mode) — id_mode: None | "uuid" |
    "string". None bila tabel/struktur tidak dikenali (seed akan di-skip).
    """
    for table in _TABLE_CANDIDATES:
        if table not in insp.get_table_names():
            continue
        cols = {c["name"]: c for c in insp.get_columns(table)}
        key_col = next((c for c in _KEY_CANDIDATES if c in cols), None)
        value_col = next((c for c in _VALUE_CANDIDATES if c in cols), None)
        if not key_col or not value_col:
            continue
        # Kolom wajib lain tanpa default (selain id) → struktur tidak
        # dikenali; jangan memaksa insert (biarkan halaman Perpajakan
        # yang membuat barisnya).
        blockers = [
            c["name"]
            for c in cols.values()
            if c["name"] not in (key_col, value_col, "id")
            and not c.get("nullable", True)
            and c.get("default") is None
        ]
        if blockers:
            print(
                f"[update047] Tabel {table}: kolom wajib tanpa default "
                f"({blockers}) — seed RATE_PPN/RATE_PPH23 dilewati. "
                "Simpan sekali lewat Referensi → Perpajakan untuk membuat baris."
            )
            return None, None, None, None
        id_col = cols.get("id")
        id_mode = None
        if id_col is not None and not id_col.get("nullable", True) and id_col.get("default") is None:
            type_name = id_col["type"].__class__.__name__.upper()
            id_mode = "uuid" if "UUID" in type_name else "string"
        return table, key_col, value_col, id_mode
    return None, None, None, None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    table, key_col, value_col, id_mode = _find_setting_table(insp)
    if table is None:
        print(
            "[update047] Tabel app-setting tidak dikenali/ditemukan — seed "
            "RATE_PPN/RATE_PPH23 dilewati (aman). Simpan sekali lewat "
            "Referensi → Perpajakan untuk membuat barisnya."
        )
        return

    for key, value in _SEED_ROWS:
        id_select = ""
        bind_iid = {}
        if id_mode == "uuid":
            id_select = "gen_random_uuid(), "
        elif id_mode == "string":
            id_select = ":iid, "
            bind_iid = {"iid": str(uuid.uuid4())}
        conn.execute(
            sa.text(
                f"""
                INSERT INTO "{table}" ({"id, " if id_mode else ""}"{key_col}", "{value_col}")
                SELECT {id_select}:k, :v
                WHERE NOT EXISTS (
                    SELECT 1 FROM "{table}" WHERE "{key_col}" = :k
                )
                """
            ),
            {"k": key, "v": value, **bind_iid},
        )
        print(f"[update047] Seed {key}={value} di {table} (bila belum ada).")


def downgrade() -> None:
    # Seed hanya default awal — jangan menghapus nilai yang mungkin sudah
    # diubah user lewat halaman Perpajakan. Downgrade = no-op.
    pass
