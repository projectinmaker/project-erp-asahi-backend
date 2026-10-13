"""Update minor form Penawaran / ROP / PO (update ASAHI #003).

Catatan update:
1. `penawaran` + `customer_po_number` (No. PO customer) + `customer_code`
   (singkatan customer) — keduanya dibawa ke Sales Order saat konversi
   (SO sudah punya kolom yang sama), sehingga SO hasil konversi langsung
   lengkap: no. PO customer terisi dan nomor SO memakai segmen singkatan.
2. `pelanggan` + `singkatan` — master singkatan nama perusahaan (mis. TBI);
   auto-fill di form Penawaran/SO, dipakai segmen nomor dokumen.
3. `request_order_part_item`:
   - `qs` Numeric → String(100) (tipe data seperti ID PART — kode, bukan angka);
   - `keterangan` dipecah dua: `keterangan_order` + `keterangan_stok`
     (header tabel: NO..SUPPLIER sejajar, KETERANGAN dibagi kolom ORDER/STOK).
4. Enum `statusrequestorderpart`: nilai 'DISETUJUI' → 'DIBUAT' — workflow ROP
   baru tanpa persetujuan: Simpan (DRAFT) → tombol "Buat ROP" (final DIBUAT).
"""
import sqlalchemy as sa
from alembic import op

revision = 'cp0000000009'
down_revision = 'cp0000000008'
branch_labels = None
depends_on = None

ROP_ITEM = 'request_order_part_item'
STATUS_ENUM = 'statusrequestorderpart'


def upgrade() -> None:
    # 1. Penawaran — No. PO customer + singkatan (dibawa ke SO saat konversi)
    op.add_column('penawaran', sa.Column('customer_po_number', sa.String(length=50), nullable=True))
    op.add_column('penawaran', sa.Column('customer_code', sa.String(length=20), nullable=True))

    # 2. Master pelanggan — singkatan nama perusahaan
    op.add_column('pelanggan', sa.Column('singkatan', sa.String(length=20), nullable=True))

    # 3. ROP item — QS jadi teks (seperti ID PART) + keterangan dipecah ORDER/STOK
    #    "4.00" (numeric) dibersihkan jadi "4"; nilai desimal tetap dipertahankan.
    op.execute(
        f"ALTER TABLE {ROP_ITEM} ALTER COLUMN qs TYPE varchar(100) "
        "USING (CASE WHEN qs = TRUNC(qs) THEN TRUNC(qs)::text ELSE qs::text END)"
    )
    op.execute(f"ALTER TABLE {ROP_ITEM} ALTER COLUMN qs DROP NOT NULL")
    op.execute(f"ALTER TABLE {ROP_ITEM} RENAME COLUMN keterangan TO keterangan_order")
    op.add_column(ROP_ITEM, sa.Column('keterangan_stok', sa.String(length=500), nullable=True))

    # 4. Status ROP: DISETUJUI → DIBUAT (workflow baru: Simpan → Buat ROP)
    op.execute(f"ALTER TYPE {STATUS_ENUM} RENAME VALUE 'DISETUJUI' TO 'DIBUAT'")


def downgrade() -> None:
    # 4. Balikkan nama status enum
    op.execute(f"ALTER TYPE {STATUS_ENUM} RENAME VALUE 'DIBUAT' TO 'DISETUJUI'")

    # 3. Gabung kembali keterangan; QS kembali numeric (teks non-angka → 0)
    op.drop_column(ROP_ITEM, 'keterangan_stok')
    op.execute(f"ALTER TABLE {ROP_ITEM} RENAME COLUMN keterangan_order TO keterangan")
    op.execute(
        f"ALTER TABLE {ROP_ITEM} ALTER COLUMN qs TYPE numeric(18,2) "
        "USING (CASE WHEN TRIM(qs) ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN TRIM(qs)::numeric ELSE 0 END)"
    )
    op.execute(f"ALTER TABLE {ROP_ITEM} ALTER COLUMN qs SET NOT NULL")

    # 2. Hapus singkatan pelanggan
    op.drop_column('pelanggan', 'singkatan')

    # 1. Hapus kolom penawaran
    op.drop_column('penawaran', 'customer_code')
    op.drop_column('penawaran', 'customer_po_number')
