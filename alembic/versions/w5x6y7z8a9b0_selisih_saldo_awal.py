"""Akun penampung "Selisih Saldo Awal" (340000) di Modal.

Jurnal saldo awal kini menerima SATU nilai per akun (sisi debit/kredit
otomatis mengikuti saldo normal akun) dan selisih total dipampangkan
otomatis ke akun ini, sehingga user tidak perlu memilih akun lawan.
Idempotent: tidak jalan bila akun sudah ada (by system_account_type / kode).
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = 'w5x6y7z8a9b0'
down_revision = 'v4w5x6y7z8a9'
branch_labels = None
depends_on = None

SYSTEM_TYPE = 'OPENING_BALANCE_DIFF'


def upgrade():
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT id FROM akun_perkiraan WHERE system_account_type = :t"),
        {"t": SYSTEM_TYPE},
    ).scalar_one_or_none()
    if existing:
        return
    induk = bind.execute(
        sa.text("SELECT id FROM akun_perkiraan WHERE kode = '300000'")
    ).scalar_one_or_none()
    if not induk:
        # Header EKUITAS tidak ditemukan — skip (service akan get-or-create).
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO akun_perkiraan (
                id, kode, nama, header, tingkat, induk_id, induk_kode,
                saldo_normal, saldo, tanggal, status, is_subledger,
                account_class, account_subclass, financial_statement,
                report_group, system_account_type, allow_system_posting,
                allow_manual_posting, is_control_account, subledger_type,
                reconciliation_required, active, created_at, updated_at
            ) VALUES (
                :id, '340000', 'Selisih Saldo Awal', 'MODAL', 'DETAIL',
                :induk_id, '300000',
                'KREDIT', 0, NULL, 'AKTIF', false,
                'EQUITY', NULL, 'NERACA', 'EQUITY', :t, true,
                false, false, NULL, false, true, now(), now()
            )
            """
        ),
        {"id": str(uuid.uuid4()), "induk_id": str(induk), "t": SYSTEM_TYPE},
    )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM akun_perkiraan WHERE system_account_type = :t"),
        {"t": SYSTEM_TYPE},
    )
