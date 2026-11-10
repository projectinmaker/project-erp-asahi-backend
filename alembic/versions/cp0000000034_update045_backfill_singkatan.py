"""update045_backfill_singkatan_master

=== Update #045 — backfill singkatan Pelanggan/Supplier lama ===

Mengapa "ambil otomatis ringkasan perusahaan belum berfungsi": fitur
auto-singkatan (#043) hanya berjalan saat master PELANGGAN/SUPPLIER dibuat
atau di-update — data lama yang belum pernah disimpan ulang masih punya
``singkatan`` NULL, sehingga form SO/Penawaran menarik nilai kosong.

Backfill ini mengisi singkatan untuk seluruh baris pelanggan & supplier
yang kosong, memakai ATURAN YANG SAMA dengan ``generate_singkatan``
(app/utils/nomor_dokumen.py #043):
- UPPERCASE, tokenisasi spasi/titik/koma, buang token generik
  (PT, CV, UD, TBK, &, DAN, dll.).
- Inisial tiap token inti ("PT. TOYOTA BOSHOKU INDONESIA" → "TBI").
- Satu token inti → 3 karakter pertama; semua generik → 3 karakter
  pertama nama bersih; maksimal 20 karakter.

Downgrade: singkatan hasil backfill TIDAK dihapus (tidak dibedakan dari
isian user) — kolom dibiarkan apa adanya.
"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cp0000000034"
down_revision: Union[str, Sequence[str], None] = "cp0000000033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirror SINGKATAN_TOKEN_GENERIK (app/utils/nomor_dokumen.py #043).
SINGKATAN_TOKEN_GENERIK = {
    "PT", "CV", "UD", "PMA", "PMDN", "TBK", "PJ", "PK", "CP", "FA", "CO",
    "LTD", "INC", "TBK.", "PERUSAHAAN", "KOPERASI", "&", "AND", "DAN",
}


def _generate_singkatan(nama) -> str:
    """Aturan identik dengan app.utils.nomor_dokumen.generate_singkatan."""
    bersih = (nama or "").strip().upper()
    tokens = [t for t in re.split(r"[\s.,]+", bersih) if t]
    inti = [t for t in tokens if t not in SINGKATAN_TOKEN_GENERIK]
    if inti:
        if len(inti) == 1:
            hasil = inti[0][:3]
        else:
            hasil = "".join(t[0] for t in inti)
    else:
        hasil = re.sub(r"[^A-Z0-9]", "", bersih)[:3]
    return (hasil or "")[:20]


def _backfill(table: str) -> None:
    """Isi singkatan kosong dari nama (aturan #043) untuk tabel master."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f"SELECT id, nama FROM {table} WHERE singkatan IS NULL OR singkatan = ''")  # noqa: E501
    ).fetchall()
    for row in rows:
        singkatan = _generate_singkatan(row.nama)
        if singkatan:
            conn.execute(
                sa.text(f"UPDATE {table} SET singkatan = :s, updated_at = now() WHERE id = :id"),  # noqa: E501
                {"s": singkatan, "id": row.id},
            )


def upgrade() -> None:
    _backfill("pelanggan")
    _backfill("supplier")


def downgrade() -> None:
    # Singkatan hasil backfill tidak dibedakan dari isian user — dibiarkan.
    pass
