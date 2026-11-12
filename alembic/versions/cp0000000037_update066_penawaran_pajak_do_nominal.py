"""update066_penawaran_pajak_do_nominal

=== Update #066 — dua set kolom baru ===

1. PPN & PPh23 opsional pada PENAWARAN (mirror Purchase Order, request user:
   "tambah cekbox PPN dan PPh23 di penawaran"):
   - ``ppn``            (numeric 5,2, default 11)
   - ``ppn_applicable`` (bool, default true)
   - ``pph23``          (numeric 5,2, default 2)
   - ``pph23_applicable`` (bool, default false)
   - ``total_ppn``      (numeric 18,2, default 0) — menambah grand_total
   - ``total_pph23``    (numeric 18,2, default 0) — memotong grand_total

   ``document_totals.refresh_totals`` sudah generik (hasattr/getattr) sehingga
   total penawaran otomatis menghitung pajak begitu kolomnya ada — perilaku
   dokumen lama TIDAK berubah karena ppn_applicable default true + ppn lama
   memang tidak tersimpan... CATATAN: penawaran lama (#042) selalu tanpa PPN;
   default kolom membuat penawaran BARU ber-PPN 11% bila frontend tidak
   mengirim flag. Frontend update ini selalu mengirim flag sesuai checkbox.

2. Harga & diskon per baris DELIVERY ORDER / pengiriman (request user:
   "field quantity dan nominal di DO"):
   - ``pengiriman_barang_detail.harga``  (numeric 18,2, default 0) — snapshot
     harga jual dari line SO saat tarik data
   - ``pengiriman_barang_detail.diskon`` (numeric 5,2, default 0) — snapshot
     diskon % line SO

   Nominal baris = harga x qty x (1 - diskon%). Murni informatif (kolom
   nominal di form/daftar/detail DO) — tidak memengaruhi stok/HPP.

Idempotent: kolom hanya ditambahkan bila belum ada (guard inspeksi).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cp0000000037"
down_revision: Union[str, Sequence[str], None] = "cp0000000036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (tabel, nama kolom, definisi)
_COLUMNS = (
    ("penawaran", "ppn", sa.Column("ppn", sa.Numeric(5, 2), nullable=False, server_default=sa.text("11"))),
    ("penawaran", "ppn_applicable", sa.Column("ppn_applicable", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
    ("penawaran", "pph23", sa.Column("pph23", sa.Numeric(5, 2), nullable=False, server_default=sa.text("2"))),
    ("penawaran", "pph23_applicable", sa.Column("pph23_applicable", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
    ("penawaran", "total_ppn", sa.Column("total_ppn", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0"))),
    ("penawaran", "total_pph23", sa.Column("total_pph23", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0"))),
    ("pengiriman_barang_detail", "harga", sa.Column("harga", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0"))),
    ("pengiriman_barang_detail", "diskon", sa.Column("diskon", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0"))),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = {t: {c["name"] for c in insp.get_columns(t)} for t, _n, _c in _COLUMNS}
    added = []
    for table, name, column in _COLUMNS:
        if name not in existing[table]:
            with op.batch_alter_table(table) as batch:
                batch.add_column(column)
            added.append(f"{table}.{name}")
    if added:
        print(f"[update066] kolom ditambahkan: {', '.join(added)}")
    else:
        print("[update066] semua kolom sudah ada — tidak ada perubahan")


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table, name, _column in _COLUMNS:
        existing = {c["name"] for c in insp.get_columns(table)}
        if name in existing:
            with op.batch_alter_table(table) as batch:
                batch.drop_column(name)
