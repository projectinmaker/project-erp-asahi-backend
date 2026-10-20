"""Update AUD-12 — Foundation multi-company: dimensi perusahaan pada dokumen.

Temuan audit AUD-12: isolasi multi-company tidak didukung — hanya
``jurnal_umum`` & ``gudang`` yang punya ``company_id`` (semua NULL);
``organization_unit`` kosong (tidak pernah di-seed); dokumen transaksi
(SI/PI/retur/kas/transfer/penyesuaian) dan master tanpa dimensi perusahaan;
filter laporan opt-in tanpa enforcement.

Update ini meletakkan fondasi (fase 1) supaya arsitektur "siap multi-company"
tanpa mengubah perilaku single-company yang ada:

1. **Seed perusahaan default** ``organization_unit`` kind=COMPANY, code
   ``ASAHI`` (fixed UUID, idempotent, ON CONFLICT DO NOTHING). Lifespan
   startup ikut memastikan unit ini ada (self-healing bila baris terhapus).

2. **Kolom ``company_id``** (UUID, FK ``organization_unit.id``, nullable,
   server_default = UUID perusahaan default) pada tabel dokumen transaksi:
   ``sales_order``, ``sales_invoice``, ``sales_retur``, ``purchase_order``,
   ``purchase_invoice``, ``purchase_retur``, ``penerimaan_kas``,
   ``pembayaran_kas``, ``transfer_bank``, ``penyesuaian_stok``.
   Baris baru otomatis terisi default (tanpa perubahan service); baris lama
   di-backfill. Jurnal lama juga di-backfill supaya laporan per-dimensi
   konsisten.

3. Endpoint list dokumen utama mendapat query param opsional ``companyId``
   (filter per perusahaan) — pola ``report_scope`` yang sudah ada.

Pembatasan fase 1 (dikenali eksplisit): master (pelanggan/supplier/barang/
kas-bank-akun) & setting akun masih lintas-perusahaan; tidak ada binding
user→perusahaan; enforcement wajib menyusul di fase 2 bersama master
per-company.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'cp0000000020'
down_revision = 'cp0000000019'
branch_labels = None
depends_on = None

# Fixed UUID perusahaan default — deterministic supaya server_default kolom
# dan backfill menghasilkan identitas yang sama di semua environment.
DEFAULT_COMPANY_ID = '0e9f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b'
DEFAULT_COMPANY_CODE = 'ASAHI'
DEFAULT_COMPANY_NAME = 'PT Asahi Sukses Industri'

# Tabel dokumen transaksi yang mendapat kolom company_id.
_DOC_TABLES = [
    'sales_order', 'sales_invoice', 'sales_retur',
    'purchase_order', 'purchase_invoice', 'purchase_retur',
    'penerimaan_kas', 'pembayaran_kas', 'transfer_bank', 'penyesuaian_stok',
]


def _company_exists() -> bool:
    res = op.get_bind().execute(sa.text(
        "SELECT 1 FROM organization_unit WHERE id = :cid"), {"cid": DEFAULT_COMPANY_ID})
    return res.first() is not None


def upgrade() -> None:
    # 1) Perusahaan default (idempotent).
    op.execute(
        f"""
        INSERT INTO organization_unit (id, kind, code, name, parent_id, status, created_at, updated_at)
        SELECT '{DEFAULT_COMPANY_ID}', 'COMPANY', '{DEFAULT_COMPANY_CODE}',
               '{DEFAULT_COMPANY_NAME}', NULL, 'AKTIF', now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM organization_unit WHERE kind = 'COMPANY' AND code = '{DEFAULT_COMPANY_CODE}')
        ON CONFLICT (kind, code) DO NOTHING
        """
    )

    # 2) Kolom company_id pada dokumen transaksi (idempotent bila kolom sudah ada).
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in _DOC_TABLES:
        columns = {c['name'] for c in insp.get_columns(table)}
        if 'company_id' in columns:
            continue
        op.add_column(
            table,
            sa.Column(
                'company_id',
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey('organization_unit.id'),
                nullable=True,
                server_default=sa.text(f"'{DEFAULT_COMPANY_ID}'::uuid"),
            ),
        )
        op.create_index(f'ix_{table}_company_id', table, ['company_id'])
        # 3) Backfill baris lama → perusahaan default.
        op.execute(
            f"UPDATE {table} SET company_id = '{DEFAULT_COMPANY_ID}' WHERE company_id IS NULL"
        )

    # Backfill jurnal lama (kolom company_id sudah ada sejak migration q8r9).
    op.execute(
        f"UPDATE jurnal_umum SET company_id = '{DEFAULT_COMPANY_ID}' WHERE company_id IS NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in reversed(_DOC_TABLES):
        columns = {c['name'] for c in insp.get_columns(table)}
        if 'company_id' not in columns:
            continue
        op.drop_index(f'ix_{table}_company_id', table_name=table)
        op.drop_column(table, 'company_id')
    # Perusahaan default hanya dihapus bila tidak direferensikan jurnal lagi.
    op.execute(
        f"""
        DELETE FROM organization_unit
        WHERE id = '{DEFAULT_COMPANY_ID}'
          AND NOT EXISTS (SELECT 1 FROM jurnal_umum WHERE company_id = '{DEFAULT_COMPANY_ID}')
        """
    )
