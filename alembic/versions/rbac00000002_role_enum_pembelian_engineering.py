"""Update ASAHI #018 — enum role: tambah STAFF_PEMBELIAN & STAFF_ENGINEERING.

Template RBAC v2-nya sudah lama ada (PURCHASING_STAFF & ENGINEERING_STAFF di
app/services/access_registry.py), tetapi enum legacy `rolepengguna` di
PostgreSQL hanya punya 5 nilai sehingga role tsb. tidak bisa dipilih saat
membuat pengguna. Migration ini menambah dua nilai enum baru:

    ALTER TYPE rolepengguna ADD VALUE 'STAFF_PEMBELIAN';
    ALTER TYPE rolepengguna ADD VALUE 'STAFF_ENGINEERING';

Setelah apply + restart backend:
- seed_access() menjalankan auto-link user lama (tanpa mengubah user lain);
- POST /pengguna menerima role baru → _sync_role_template mengaitkan user ke
  template PURCHASING_STAFF / ENGINEERING_STAFF otomatis (via LEGACY_TEMPLATE);
- frontend (Update #018) menampilkan pilihan baru di halaman Pengguna.

Catatan teknis:
- PostgreSQL 16 (docker-compose: postgres:16-alpine) mengizinkan
  ALTER TYPE ... ADD VALUE di dalam transaksi selama nilai barunya tidak
  dipakai di transaksi yang sama — di sini hanya menambah, tidak memakai.
- IF NOT EXISTS membuat migration idempotent terhadap eksekusi ganda.
- Downgrade: PostgreSQL tidak mendukung DROP VALUE dari enum; downgrade
  adalah no-op (aman — kolom role tidak punya nilai baru lainnya).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'rbac00000002'
down_revision = 'cp0000000022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE rolepengguna ADD VALUE IF NOT EXISTS 'STAFF_PEMBELIAN'")
    op.execute("ALTER TYPE rolepengguna ADD VALUE IF NOT EXISTS 'STAFF_ENGINEERING'")


def downgrade() -> None:
    # No-op: PostgreSQL tidak mendukung penghapusan nilai enum.
    # (Kolom tetap kompatibel — nilai lama tidak berubah.)
    pass
