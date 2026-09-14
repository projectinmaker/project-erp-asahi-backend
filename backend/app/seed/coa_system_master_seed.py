"""Seed: Import COA System Master dari workbook ke DB.

Pakai data dari app.seed.data.coa_system_master_data.COA_SYSTEM_MASTER.

Pemakaian:
    cd backend
    python3 -m app.seed.coa_system_master_seed

Sifat:
- Idempotent: skip akun yang kode-nya sudah ada di DB.
- Tidak menghapus akun existing.
- Set saldo=0 (saldo awal di-set terpisah via endpoint /coa/saldo-awal).

Setelah seed selesai, jalankan juga:
    python3 -m app.seed.phase3_setting_akun_seed   (kalau belum)
untuk inisialisasi setting_akun keys yang baru (BANK_CLEARING, dll).
"""
import sys
import os
from decimal import Decimal

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from loguru import logger

from app.database import SessionLocal
from app.models.akun_perkiraan import (
    AkunPerkiraan, HeaderCOA, SaldoNormal, TingkatAkun,
    HEADER_TO_ACCOUNT_CLASS, NODE_TYPE_TO_TINGKAT,
)
from app.seed.data.coa_system_master_data import COA_SYSTEM_MASTER


def _header_enum_for_class(account_class: str) -> HeaderCOA:
    """Reverse map: account_class -> HeaderCOA enum."""
    reverse = {v: k for k, v in HEADER_TO_ACCOUNT_CLASS.items()}
    return reverse.get((account_class or "ASSET").upper(), HeaderCOA.AKTIVA)


def _resolve_parent_id(db: Session, parent_code):
    if not parent_code:
        return None
    parent = (
        db.query(AkunPerkiraan.id)
        .filter(AkunPerkiraan.kode == parent_code)
        .first()
    )
    return str(parent[0]) if parent else None


def seed_coa_system_master():
    """Import 151 record COA dari workbook ke DB.

    Idempotent: skip kalau kode sudah ada.
    """
    db: Session = SessionLocal()
    inserted = 0
    skipped = 0
    errors = 0

    try:
        for rec in COA_SYSTEM_MASTER:
            code = rec["account_code"]
            existing = (
                db.query(AkunPerkiraan)
                .filter(AkunPerkiraan.kode == code)
                .first()
            )
            if existing:
                skipped += 1
                continue

            try:
                account_class = (rec.get("account_class") or "ASSET").upper()
                header = _header_enum_for_class(account_class)
                saldo_normal = SaldoNormal(
                    (rec.get("normal_balance") or "DEBIT").upper()
                )
                node_type = (rec.get("node_type") or "DETAIL").upper()
                tingkat = NODE_TYPE_TO_TINGKAT.get(node_type, TingkatAkun.DETAIL)
                parent_code = rec.get("parent_code")
                induk_id = _resolve_parent_id(db, parent_code)

                coa = AkunPerkiraan(
                    kode=code,
                    nama=rec.get("account_name") or f"COA {code}",
                    header=header,
                    tingkat=tingkat,
                    induk_id=induk_id,
                    induk_kode=parent_code,
                    saldo_normal=saldo_normal,
                    saldo=Decimal("0"),
                    tanggal=None,
                    status="AKTIF",
                    is_subledger=False,
                    account_class=account_class,
                    account_subclass=rec.get("account_subclass"),
                    financial_statement=rec.get("financial_statement"),
                    report_group=rec.get("report_group"),
                    system_account_type=rec.get("system_account_type"),
                    allow_system_posting=bool(rec.get("allow_system_posting", True)),
                    allow_manual_posting=bool(rec.get("allow_manual_posting", True)),
                    is_control_account=bool(rec.get("is_control_account", False)),
                    subledger_type=rec.get("subledger_type"),
                    reconciliation_required=bool(rec.get("reconciliation_required", False)),
                    active=bool(rec.get("active", True)),
                )
                db.add(coa)
                db.flush()
                inserted += 1
                logger.info(
                    f"COA seeded: {code} - {coa.nama} (class={account_class}, "
                    f"control={coa.is_control_account}, subledger={coa.subledger_type})"
                )
            except Exception as e:
                errors += 1
                logger.error(f"Failed to seed COA {code}: {e}")
                db.rollback()
                continue

        db.commit()
        logger.info(
            f"Seed complete: inserted={inserted}, skipped={skipped}, errors={errors}"
        )
        print(f"\n=== Seed COA System Master ===")
        print(f"  Inserted: {inserted}")
        print(f"  Skipped (already exists): {skipped}")
        print(f"  Errors: {errors}")
        print(f"  Total in workbook: {len(COA_SYSTEM_MASTER)}")

    finally:
        db.close()


if __name__ == "__main__":
    seed_coa_system_master()
