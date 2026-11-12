"""update067_po_pic_supplier

=== Update #067 — kolom PIC Supplier & No. HP Supplier pada Purchase Order ===

Permintaan user: "PIC dan No HP supplier dibuat field aktif saat pembuatan
di PO" — field PIC/No. HP yang ada selama ini (pic_gudang /
pic_gudang_telepon, Update ASAHI #002) adalah PIC INTERNAL (gudang tujuan)
dan TETAP seperti versi sebelumnya; yang ditambahkan sekarang adalah field
kontak PIHAK SUPPLIER:

- ``pic_supplier``         (varchar 150) — nama PIC di sisi supplier untuk
  PO ini ( Kontak Person master Supplier, boleh disunting per PO)
- ``pic_supplier_telepon`` (varchar 30)  — No. HP / telepon PIC supplier

Keduanya nullable (PO lama tetap valid), disimpan sebagai SNAPSHOT saat
input PO — mirror pola pic_gudang. Lebar kolom mengikuti master Supplier
(kontak_person 150 / telepon 30).

Cetakan PO memakai snapshot ini bila terisi; PO lama (kolom kosong) tetap
fallback ke Kontak Person/Telepon live dari master supplier — tampilan
cetakan dokumen lama tidak berubah.

Idempotent: kolom hanya ditambahkan bila belum ada (guard inspeksi).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cp0000000038"
down_revision: Union[str, Sequence[str], None] = "cp0000000037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "purchase_order"

# (nama kolom, definisi) — mirror pic_gudang (cp0000000008), lebar kolom
# mengikuti master Supplier: kontak_person varchar(150), telepon varchar(30).
_COLUMNS = (
    ("pic_supplier", sa.Column("pic_supplier", sa.String(length=150), nullable=True)),
    ("pic_supplier_telepon", sa.Column("pic_supplier_telepon", sa.String(length=30), nullable=True)),
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
        print(f"[update067] kolom ditambahkan ke {_TABLE}: {', '.join(added)}")
    else:
        print(f"[update067] kolom PIC Supplier sudah ada di {_TABLE} — tidak ada perubahan")


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = {c["name"] for c in insp.get_columns(_TABLE)}
    with op.batch_alter_table(_TABLE) as batch:
        for name, _column in _COLUMNS:
            if name in existing:
                batch.drop_column(name)
