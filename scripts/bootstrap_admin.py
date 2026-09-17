"""Bootstrap initial admin user untuk ASAHI ERP.

Endpoint /auth/init-admin dinonaktifkan (HTTP 410 Gone) untuk keamanan.
Script ini menggantikan fungsi tersebut — jalankan setelah alembic upgrade
head selesai, sebelum bisa login ke API.

Usage:
    cd Backend
    python3 scripts/bootstrap_admin.py
    # atau dengan parameter
    python3 scripts/bootstrap_admin.py --username admin --password secret --email admin@asahi.com

Default credentials (kalau tanpa parameter):
    username: admin
    password: Admin@123
    email: admin@asahi.local
    role: ADMINISTRATOR

⚠️  Ubah password default SETELEH login pertama via PUT /pengguna/{id}.
"""
import sys
import os
import argparse
from pathlib import Path

# Tambahkan root project ke path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.master.pengguna import Pengguna, RolePengguna


def create_admin(
    username: str,
    password: str,
    email: str,
    nama_lengkap: str,
    role: RolePengguna,
) -> Pengguna:
    """Buat admin user. Idempotent — skip kalau username/email sudah ada."""
    db: Session = SessionLocal()
    try:
        # Cek existing
        existing = (
            db.query(Pengguna)
            .filter((Pengguna.username == username) | (Pengguna.email == email))
            .first()
        )
        if existing:
            print(f"[SKIP] User sudah ada: username={existing.username}, email={existing.email}")
            print(f"       Kalau mau recreate, hapus manual: DELETE FROM pengguna WHERE username='{username}';")
            return existing

        # Hash password
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # Create
        user = Pengguna(
            username=username,
            nama_lengkap=nama_lengkap,
            email=email,
            password_hash=password_hash,
            role=role,
            status="AKTIF",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        print(f"[OK] Admin user created:")
        print(f"     Username : {user.username}")
        print(f"     Email    : {user.email}")
        print(f"     Nama     : {user.nama_lengkap}")
        print(f"     Role     : {user.role.value}")
        print(f"     Status   : {user.status}")
        print(f"\n⚠️  Ubah password default setelah login pertama!")
        return user

    except Exception as e:
        db.rollback()
        print(f"[ERROR] Failed to create admin: {e}")
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Bootstrap admin user untuk ASAHI ERP")
    parser.add_argument("--username", default="admin", help="Username (default: admin)")
    parser.add_argument("--password", default="Admin@123", help="Password (default: Admin@123)")
    parser.add_argument("--email", default="admin@asahi.local", help="Email (default: admin@asahi.local)")
    parser.add_argument("--nama", default="Administrator", help="Nama lengkap (default: Administrator)")
    parser.add_argument(
        "--role",
        default="ADMINISTRATOR",
        choices=[r.value for r in RolePengguna],
        help="Role (default: ADMINISTRATOR)",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  ASAHI ERP - Bootstrap Admin User")
    print("=" * 60)

    create_admin(
        username=args.username,
        password=args.password,
        email=args.email,
        nama_lengkap=args.nama,
        role=RolePengguna(args.role),
    )

    print("\n" + "=" * 60)
    print("  Done. Sekarang bisa login via POST /api/v1/auth/login")
    print("=" * 60)


if __name__ == "__main__":
    main()
