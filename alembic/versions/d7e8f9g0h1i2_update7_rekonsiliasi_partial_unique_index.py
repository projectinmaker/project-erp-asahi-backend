"""Rekonsiliasi bank: unique constraint → partial unique index (Update #7, B-01).

Masalah: constraint uq_rekonsiliasi_bank_kas_tanggal menjamin keunikan
(kas_bank_akun_id, tanggal_akhir) untuk SEMUA status. Pembatalan (void)
rekonsiliasi hanya mengubah status menjadi 'BATAL' (baris dipertahankan
sebagai audit trail), sehingga periode tersebut terkunci permanen —
membuat rekonsiliasi baru untuk periode yang sama selalu gagal dengan
IntegrityError (HTTP 500) karena baris BATAL tetap dilindungi constraint.

Fix: ganti constraint menjadi partial unique index
uq_rekonsiliasi_bank_kas_tanggal_active yang hanya berlaku untuk baris
aktif (WHERE status <> 'BATAL'). Semantik baru:
- DRAFT/SELESAI tetap unik per (kas_bank_akun_id, tanggal_akhir);
- BATAL tidak lagi memblokir periode — rekonsiliasi periode yang sama
  bisa dibuat ulang setelah void.
Model ORM RekonsiliasiBank ikut disesuaikan, dan pre-check duplikat di
rekonsiliasi_bank_service.create_rekonsiliasi diperluas ke DRAFT/SELESAI.

Idempotent: setiap langkah di-guard via introspeksi (op.get_bind() +
sa.inspect), meniru pola guard di w5x6y7z8a9b0_selisih_saldo_awal.py.

Revision ID: d7e8f9g0h1i2
Revises: c2d3e4f5g6h7
Create Date: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9g0h1i2"
down_revision: str | Sequence[str] | None = "c2d3e4f5g6h7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "rekonsiliasi_bank"
OLD_CONSTRAINT = "uq_rekonsiliasi_bank_kas_tanggal"
NEW_INDEX = "uq_rekonsiliasi_bank_kas_tanggal_active"


def _unique_constraint_names(bind) -> set:
    """Kumpulan nama unique constraint pada tabel rekonsiliasi_bank."""
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_unique_constraints(TABLE) if c.get("name")}


def _index_names(bind) -> set:
    """Kumpulan nama index (non-PK) pada tabel rekonsiliasi_bank."""
    insp = sa.inspect(bind)
    return {i["name"] for i in insp.get_indexes(TABLE) if i.get("name")}


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Drop constraint lama (guard: hanya bila masih ada).
    if OLD_CONSTRAINT in _unique_constraint_names(bind):
        op.drop_constraint(OLD_CONSTRAINT, TABLE, type_="unique")

    # Safety net: bila constraint sudah tidak ada tetapi tertinggal index
    # bernama sama (state tidak konsisten), drop index-nya juga.
    if OLD_CONSTRAINT in _index_names(bind):
        op.drop_index(OLD_CONSTRAINT, table_name=TABLE)

    # 2. Buat partial unique index untuk status aktif (guard: bila belum ada).
    if NEW_INDEX not in _index_names(bind):
        op.create_index(
            NEW_INDEX,
            TABLE,
            ["kas_bank_akun_id", "tanggal_akhir"],
            unique=True,
            postgresql_where=sa.text("status <> 'BATAL'"),
        )


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Drop partial unique index (guard).
    if NEW_INDEX in _index_names(bind):
        op.drop_index(NEW_INDEX, table_name=TABLE)

    # 2. Kembalikan constraint penuh (guard: bila belum ada).
    #    CATATAN: gagal bila sudah ada >1 baris BATAL untuk periode sama.
    if OLD_CONSTRAINT not in _unique_constraint_names(bind):
        op.create_unique_constraint(
            OLD_CONSTRAINT,
            TABLE,
            ["kas_bank_akun_id", "tanggal_akhir"],
        )
