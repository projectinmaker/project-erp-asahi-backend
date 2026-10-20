"""AUD-02 — Integritas jurnal di level database.

Temuan audit 7-a: validasi balance & pencegahan double-posting jurnal hanya
ditegakkan di application layer (validate_entries + dup-check ref dokumen),
tanpa constraint database. Bila ada penulisan yang lolos jalur non-standar,
jurnal tidak-balance / duplikat bisa tersimpan.

Migrasi ini menegakkan invariant yang sama di PostgreSQL:

1. ``ck_jurnal_detail_nonneg``   — debit & kredit tidak boleh negatif.
2. ``ck_jurnal_detail_one_sided``— tiap baris hanya satu sisi (debit ATAU
   kredit), persis aturan ``validate_entries``.
3. ``ck_jurnal_umum_balanced``   — header jurnal selalu balance
   (total_debit = total_kredit).
4. ``uq_jurnal_umum_ref_doc``    — partial UNIQUE index (ref_module, ref_id,
   tipe_transaksi) WHERE ref_id IS NOT NULL — dup-guard yang sama dengan
   ``auto_posting_jurnal`` (retur tetap boleh 2 jurnal karena tipe berbeda:
   finansial vs RETUR_STOCK/RETUR_HPP; reversal memakai tipe REVERSAL).
5. Index pendukung performa GL: jurnal_detail(jurnal_umum_id) dan
   jurnal_detail(akun_perkiraan_id).

Data existing telah diverifikasi bersih sebelum migrasi (audit 2026-10-15:
0 baris negatif / dua-sisi / nol-kedua-sisi; 0 duplikat kombinasi
ref_module+ref_id+tipe_transaksi; 0 header tidak-balance).

Downgrade bersifat reversibel penuh (drop constraint/index).
"""
from alembic import op
import sqlalchemy as sa

revision = 'cp0000000014'
down_revision = 'cp0000000013'
branch_labels = None
depends_on = None


_DETAIL = 'jurnal_detail'
_HEADER = 'jurnal_umum'


def _constraint_exists(bind, name: str, table: str) -> bool:
    return any(c.get('name') == name for c in sa.inspect(bind).get_check_constraints(table))


def _index_exists(bind, name: str, table: str) -> bool:
    return any(ix.get('name') == name for ix in sa.inspect(bind).get_indexes(table))


def upgrade():
    bind = op.get_bind()

    if not _constraint_exists(bind, 'ck_jurnal_detail_nonneg', _DETAIL):
        op.create_check_constraint(
            'ck_jurnal_detail_nonneg', _DETAIL, 'debit >= 0 AND kredit >= 0')
    if not _constraint_exists(bind, 'ck_jurnal_detail_one_sided', _DETAIL):
        op.create_check_constraint(
            'ck_jurnal_detail_one_sided', _DETAIL,
            '(debit > 0 AND kredit = 0) OR (kredit > 0 AND debit = 0)')
    if not _constraint_exists(bind, 'ck_jurnal_umum_balanced', _HEADER):
        op.create_check_constraint(
            'ck_jurnal_umum_balanced', _HEADER, 'total_debit = total_kredit')

    if not _index_exists(bind, 'uq_jurnal_umum_ref_doc', _HEADER):
        op.create_index(
            'uq_jurnal_umum_ref_doc', _HEADER,
            ['ref_module', 'ref_id', 'tipe_transaksi'], unique=True,
            postgresql_where=sa.text('ref_id IS NOT NULL'))

    if not _index_exists(bind, 'ix_jurnal_detail_jurnal_umum_id', _DETAIL):
        op.create_index('ix_jurnal_detail_jurnal_umum_id', _DETAIL, ['jurnal_umum_id'])
    if not _index_exists(bind, 'ix_jurnal_detail_akun_perkiraan_id', _DETAIL):
        op.create_index('ix_jurnal_detail_akun_perkiraan_id', _DETAIL, ['akun_perkiraan_id'])


def downgrade():
    bind = op.get_bind()

    for name in ('ix_jurnal_detail_akun_perkiraan_id', 'ix_jurnal_detail_jurnal_umum_id'):
        if _index_exists(bind, name, _DETAIL):
            op.drop_index(name, table_name=_DETAIL)
    if _index_exists(bind, 'uq_jurnal_umum_ref_doc', _HEADER):
        op.drop_index('uq_jurnal_umum_ref_doc', table_name=_HEADER)

    if _constraint_exists(bind, 'ck_jurnal_umum_balanced', _HEADER):
        op.drop_constraint('ck_jurnal_umum_balanced', _HEADER)
    if _constraint_exists(bind, 'ck_jurnal_detail_one_sided', _DETAIL):
        op.drop_constraint('ck_jurnal_detail_one_sided', _DETAIL)
    if _constraint_exists(bind, 'ck_jurnal_detail_nonneg', _DETAIL):
        op.drop_constraint('ck_jurnal_detail_nonneg', _DETAIL)
