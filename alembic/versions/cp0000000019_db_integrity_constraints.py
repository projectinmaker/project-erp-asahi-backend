"""Update AUD2-C-minor — Jaring pengaman integritas level database.

Temuan audit ronde-2 (C-minor): guard anti double-posting hanya hidup di
level aplikasi (guard query ``ref_module+ref_id+tipe_transaksi`` +
advisory lock di ``posting_service.py:143-153``) dan aturan balance hanya
divalidasi di ``validate_entries`` — tidak ada constraint database yang
menjamin invariant yang sama bila baris ditulis dari jalur lain (script,
migration, worker, restore parsial).

Update ini menambah tiga constraint fisik (idempotent, aman untuk data
existing yang sudah lolos verifikasi):

1. **Partial unique index** ``uq_jurnal_ref_source`` pada
   ``jurnal_umum (ref_module, ref_id, tipe_transaksi)`` dengan kondisi
   ``ref_id IS NOT NULL`` — semantik identik dengan guard aplikasi
   (jurnal pembalik aman: bertipe REVERSAL + modul MANUAL, tidak pernah
   bertabrakan dengan pasangan sumber).

2. **CHECK** ``ck_jurnal_detail_one_side`` pada ``jurnal_detail``: tiap
   baris harus berisi tepat satu sisi (debit atau kredit) dan tak boleh
   negatif — rule 2/3 ``validate_entries`` di level fisik.

3. **CHECK** ``ck_jurnal_posted_balanced`` pada ``jurnal_umum``: jurnal
   POSTED wajib balance (``total_debit = total_kredit``); DRAFT tetap
   boleh sementara tidak balance (masih diedit).
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'cp0000000019'
down_revision = 'cp0000000018'
branch_labels = None
depends_on = None

INDEX_NAME = 'uq_jurnal_ref_source'
DETAIL_CK = 'ck_jurnal_detail_one_side'
POSTED_CK = 'ck_jurnal_posted_balanced'


def _index_names(bind, table: str) -> set:
    return {ix['name'] for ix in sa.inspect(bind).get_indexes(table)}


def _constraint_names(bind, table: str) -> set:
    return {c['name'] for c in sa.inspect(bind).get_check_constraints(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Partial unique index — anti double-posting jurnal sumber.
    if INDEX_NAME not in _index_names(bind, 'jurnal_umum'):
        op.create_index(
            INDEX_NAME,
            'jurnal_umum',
            ['ref_module', 'ref_id', 'tipe_transaksi'],
            unique=True,
            postgresql_where=sa.text('ref_id IS NOT NULL'),
        )

    # 2) CHECK — baris jurnal tepat satu sisi, tidak negatif.
    if DETAIL_CK not in _constraint_names(bind, 'jurnal_detail'):
        op.create_check_constraint(
            DETAIL_CK,
            'jurnal_detail',
            '(debit > 0 AND kredit = 0) OR (kredit > 0 AND debit = 0)',
        )

    # 3) CHECK — jurnal POSTED wajib balance (DRAFT dikecualikan).
    if POSTED_CK not in _constraint_names(bind, 'jurnal_umum'):
        op.create_check_constraint(
            POSTED_CK,
            'jurnal_umum',
            "status <> 'POSTED' OR (total_debit = total_kredit)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if POSTED_CK in _constraint_names(bind, 'jurnal_umum'):
        op.drop_constraint(POSTED_CK, 'jurnal_umum', type_='check')
    if DETAIL_CK in _constraint_names(bind, 'jurnal_detail'):
        op.drop_constraint(DETAIL_CK, 'jurnal_detail', type_='check')
    if INDEX_NAME in _index_names(bind, 'jurnal_umum'):
        op.drop_index(INDEX_NAME, table_name='jurnal_umum')
