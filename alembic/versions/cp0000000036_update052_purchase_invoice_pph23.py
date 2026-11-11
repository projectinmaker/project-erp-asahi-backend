"""update052_purchase_invoice_pph23

=== Update #052 — kolom pilihan pajak PPN/PPh23 pada Invoice Pembelian ===

Invoice Pembelian selama ini tidak punya kolom pilihan pajak (hanya PPN
wajib). Form FE sudah lama mengirim ``pph23``/``pph23Applicable`` (AUD-09)
tetapi backend MENGABAIKANNYA — grand total tersimpan TIDAK sesuai yang
dilihat user di form. Migrasi ini menambahkan kolom yang sama seperti
Purchase Order / Sales Invoice:

- ``ppn_applicable``  (bool, default true)  — PPN bisa dimatikan per invoice
- ``pph23_applicable`` (bool, default false) — PPh23 dipilih per invoice
- ``pph23``           (numeric 5,2, default 2) — tarif PPh23 (editable per
  invoice karena bergantung jenis jasa supplier: 2%/4%/6%)
- ``total_pph23``     (numeric 18,2, default 0) — nilai PPh23 (memotong
  grand total; dikredit ke akun Hutang PPh 23 saat posting)

``refresh_totals`` (document_totals) sudah generik memakai getattr atas
keempat nama ini sehingga total otomatis dihitung ulang tanpa perubahan.

Idempotent: kolom hanya ditambahkan bila belum ada (guard inspeksi).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cp0000000036"
down_revision: Union[str, Sequence[str], None] = "cp0000000035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "purchase_invoice"

# (nama kolom, definisi) — mirror purchase_order (cp: Update ASAHI PPh23/PPN).
_COLUMNS = (
    ("ppn_applicable", sa.Column("ppn_applicable", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
    ("pph23_applicable", sa.Column("pph23_applicable", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
    ("pph23", sa.Column("pph23", sa.Numeric(5, 2), nullable=False, server_default=sa.text("2"))),
    ("total_pph23", sa.Column("total_pph23", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0"))),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = {c["name"] for c in insp.get_columns(_TABLE)}
    added = []
    with op.batch_alter_table(_TABLE) as batch:
        for name, column in _COLUMNS:
            if name not in existing:
                batch.add_column(column)
                added.append(name)
    if added:
        print(f"[update052] kolom ditambahkan ke {_TABLE}: {', '.join(added)}")
    else:
        print(f"[update052] kolom pajak sudah ada di {_TABLE} — tidak ada perubahan")


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = {c["name"] for c in insp.get_columns(_TABLE)}
    with op.batch_alter_table(_TABLE) as batch:
        for name, _column in _COLUMNS:
            if name in existing:
                batch.drop_column(name)
