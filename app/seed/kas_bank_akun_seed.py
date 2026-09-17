"""Asahi ERP - Seed KasBankAkun

Seed data untuk tabel kas_bank_akun, mengaitkan KAS & BANK
ke akun perkiraan (COA) yang sesuai.

Idempotent: skip jika data sudah ada.

Usage:
    python -m app.seed.kas_bank_akun_seed
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from decimal import Decimal
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank
from app.models.akun_perkiraan import AkunPerkiraan


def seed_kas_bank_akun():
    db: Session = SessionLocal()
    try:
        # Cek jika data sudah ada
        if db.query(KasBankAkun).count() > 0:
            print("KasBankAkun already exists, skipping seed.")
            return

        # NOTE: Kas Kecil & Kas Besar dihapus per Phase 1 (meeting 25 Agt 2026)
        kas_bank_data = [
            ("110.000.001", "BK-001", "Bank BNI 2762", JenisKasBank.BANK),
            ("110.000.002", "BK-002", "Bank Mandiri 7269", JenisKasBank.BANK),
            ("110.000.003", "BK-003", "Bank BSI 7032", JenisKasBank.BANK),
            ("110.000.004", "BK-004", "Bank BRI 2307", JenisKasBank.BANK),
            ("110.000.005", "BK-005", "Bank BCA 7777", JenisKasBank.BANK),
            ("110.000.006", "BK-006", "Bank BSI 4562", JenisKasBank.BANK),
        ]

        inserted = 0
        for kode_coa, kode_kb, nama, jenis in kas_bank_data:
            coa = db.query(AkunPerkiraan).filter_by(kode=kode_coa).first()
            if not coa:
                print(f"  WARNING: COA {kode_coa} tidak ditemukan, skip {nama}")
                continue

            db.add(KasBankAkun(
                kode=kode_kb,
                nama=nama,
                jenis=jenis,
                akun_perkiraan_id=coa.id,
                saldo=Decimal("0"),
                status="AKTIF",
            ))
            inserted += 1
            print(f"  + {kode_kb} - {nama} ({jenis.value}) -> COA {kode_coa}")

        db.commit()
        print(f"\nInserted {inserted} KasBankAkun records.")

    except Exception as e:
        db.rollback()
        print(f"Error seeding KasBankAkun: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_kas_bank_akun()
