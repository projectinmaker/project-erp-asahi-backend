"""update071_approval_config

=== Update #071 — konfigurasi persetujuan per jenis dokumen ===

Maker-checker (workflow persetujuan) tidak lagi di-hardcode per endpoint /
role. Tiap jenis dokumen kini punya konfigurasi yang diatur Super Admin
lewat modul Preferensi ▸ Persetujuan:

- ``approval_config``   — saklar per jenis dokumen (document_type unik):
  ``enabled`` = dokumen jenis ini menunggu persetujuan approver sebelum
  final; non-aktif = langsung final saat dibuat (auto-final).
- ``approval_approver`` — daftar approver per jenis dokumen: penunjuk USER
  (user_id) ATAU ROLE (role_code) — satu per baris (CheckConstraint
  num_nonnulls = 1). Salah satu saja approver menyetujui = final.

Aturan main (Update #071):
1. Persetujuan 1 tahap — sekali approve → final + posting/eksekusi
   otomatis (tanpa syarat nominal, tanpa tahapan berjenjang).
2. Default: SEMUA jenis dokumen non-aktif (tidak ada seed) — setelah
   update diterapkan semua dokumen langsung final; Super Admin
   mengaktifkan selektif di Preferensi ▸ Persetujuan.
3. Mengaktifkan jenis dokumen mensyaratkan minimal 1 approver
   (divalidasi di service, bukan constraint DB).
4. SUPER_ADMIN tetap fallback: boleh menyetujui dokumen apa pun
   (termasuk buatannya sendiri) bila approver berhalangan.
5. Dokumen yang sudah menunggu (PENDING) saat aturan diubah TIDAK
   difinalisasi otomatis — tetap menunggu persetujuan manual.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "cp0000000039"
down_revision: Union[str, Sequence[str], None] = "cp0000000038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'approval_config',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('document_type', sa.String(40), nullable=False, index=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_by', UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('document_type', name='uq_approval_config_document_type'),
    )
    op.create_table(
        'approval_approver',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('document_type', sa.String(40), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('pengguna.id'), nullable=True),
        sa.Column('role_code', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('num_nonnulls(user_id, role_code) = 1', name='ck_approval_approver_target'),
        sa.UniqueConstraint('document_type', 'user_id', name='uq_approval_approver_user'),
        sa.UniqueConstraint('document_type', 'role_code', name='uq_approval_approver_role'),
    )


def downgrade() -> None:
    op.drop_table('approval_approver')
    op.drop_table('approval_config')
