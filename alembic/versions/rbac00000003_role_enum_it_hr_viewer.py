"""Update #072 — enum role: tambah IT_ADMIN, HR_ADMIN, MANAGEMENT_VIEWER.

Template RBAC v2 ketiganya sudah lama ada (IT_ADMIN, HR_ADMIN,
MANAGEMENT_VIEWER di app/services/access_registry.py — ROLE_TEMPLATES),
tetapi enum legacy `rolepengguna` di PostgreSQL tidak punya nilai tsb.
sehingga role itu tidak bisa dipilih saat membuat/mengubah pengguna di
halaman Pengguna (hanya bisa di-assign lewat UI Role & Akses).

Migration ini menambah tiga nilai enum (pola rbac00000002 / Update #018):

    ALTER TYPE rolepengguna ADD VALUE 'IT_ADMIN';
    ALTER TYPE rolepengguna ADD VALUE 'HR_ADMIN';
    ALTER TYPE rolepengguna ADD VALUE 'MANAGEMENT_VIEWER';

Setelah apply + restart backend:
- POST/PUT /pengguna menerima role baru → _sync_role_template mengaitkan
  user ke template RBAC terkait otomatis (via LEGACY_TEMPLATE, Update #072);
- frontend (Update #072) menampilkan pilihan baru di halaman Pengguna
  (bersama rename tampilan "Staff Akuntansi" → "Staf Keuangan").

Catatan teknis:
- PostgreSQL 16 mengizinkan ALTER TYPE ... ADD VALUE di dalam transaksi
  selama nilai barunya tidak dipakai di transaksi yang sama.
- IF NOT EXISTS membuat migration idempotent terhadap eksekusi ganda.
- Downgrade: PostgreSQL tidak mendukung DROP VALUE dari enum; downgrade
  adalah no-op (aman — kolom role tidak memakai nilai baru lainnya).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'rbac00000003'
down_revision = 'cp0000000039'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE rolepengguna ADD VALUE IF NOT EXISTS 'IT_ADMIN'")
    op.execute("ALTER TYPE rolepengguna ADD VALUE IF NOT EXISTS 'HR_ADMIN'")
    op.execute("ALTER TYPE rolepengguna ADD VALUE IF NOT EXISTS 'MANAGEMENT_VIEWER'")


def downgrade() -> None:
    # No-op: PostgreSQL tidak mendukung penghapusan nilai enum.
    # (Kolom tetap kompatibel — nilai lama tidak berubah.)
    pass
