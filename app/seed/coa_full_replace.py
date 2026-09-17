"""Replace semua COA di DB dengan COA dari workbook ASAHI Revisi v2.

Modes:
  --preview      (default) Tampilkan report tanpa write ke DB.
                 Aman di-run berapa kali pun.

  --apply-safe   Hapus akun yang TIDAK match workbook DAN tidak punya dependency
                 (belum dipakai jurnal / subledger / pelanggan / supplier / dll).
                 Akun yang TIDAK match workbook TAPI punya dependency:
                 rename + "[LEGACY]" + active=False + lock posting.

  --apply-force  BERBAHAYA. Hanya jalan kalau belum ada jurnal_detail sama sekali.
                 Wipe SEMUA akun_perkiraan (cleanup FK referensi dulu), lalu
                 reseed 151 record workbook + apply migration map.

Pemakaian:
    cd backend
    python3 -m app.seed.coa_full_replace --preview
    python3 -m app.seed.coa_full_replace --apply-safe
    python3 -m app.seed.coa_full_replace --apply-force

WAJIB BACKUP DATABASE sebelum run --apply-safe atau --apply-force.
"""
import sys
import os
import argparse
from decimal import Decimal
from uuid import UUID

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from app.database import SessionLocal
from app.models.akun_perkiraan import (
    AkunPerkiraan, HeaderCOA, SaldoNormal, TingkatAkun,
    HEADER_TO_ACCOUNT_CLASS, NODE_TYPE_TO_TINGKAT,
)
from app.seed.data.coa_system_master_data import COA_SYSTEM_MASTER


# ==========================================
# Helpers
# ==========================================

def _normalize_kode(kode: str) -> str:
    """Normalize kode COA untuk matching.
    
    Hapus semua titik dan strip, jadi '111.200.001' dan '111200001'
    keduanya jadi '111200001'. Lalu bandingkan dengan workbook yang
    sudah dinormalize juga.
    """
    if not kode:
        return ""
    return kode.replace(".", "").replace("-", "").strip()


def _workbook_kode_set() -> set:
    """Set of normalized kode dari workbook (151 record)."""
    return {_normalize_kode(r["account_code"]) for r in COA_SYSTEM_MASTER if r.get("account_code")}


def _header_enum_for_class(account_class: str) -> HeaderCOA:
    """Reverse map: account_class -> HeaderCOA enum."""
    reverse = {v: k for k, v in HEADER_TO_ACCOUNT_CLASS.items()}
    return reverse.get((account_class or "ASSET").upper(), HeaderCOA.AKTIVA)


def _discover_fks_to_akun_perkiraan(db: Session) -> list:
    """Discover semua FK yang reference akun_perkiraan.id via information_schema.
    
    Return list of dict: {table_name, column_name, is_nullable, row_count}
    """
    sql = text("""
        SELECT 
            tc.table_name,
            kcu.column_name,
            c.is_nullable,
            (SELECT COUNT(*) FROM information_schema.columns c2 
             WHERE c2.table_name = tc.table_name) as col_count
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu 
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu 
            ON tc.constraint_name = ccu.constraint_name
            AND tc.table_schema = ccu.table_schema
        JOIN information_schema.columns c 
            ON c.table_name = tc.table_name 
            AND c.column_name = kcu.column_name
            AND c.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
            AND ccu.table_name = 'akun_perkiraan'
            AND ccu.column_name = 'id'
            AND tc.table_schema = 'public'
        ORDER BY tc.table_name
    """)
    result = db.execute(sql)
    fks = []
    for row in result:
        # Get row count for this table
        try:
            count_result = db.execute(text(f"SELECT COUNT(*) FROM {row.table_name}"))
            row_count = count_result.scalar() or 0
        except Exception:
            row_count = 0
        fks.append({
            "table_name": row.table_name,
            "column_name": row.column_name,
            "is_nullable": row.is_nullable == "YES",
            "row_count": row_count,
        })
    return fks


def _check_dependencies(db: Session, coa_id) -> dict:
    """Cek apakah akun punya dependency. Return dict {dep_name: count}.
    
    Pakai dynamic FK discovery supaya tidak hardcoded.
    """
    deps = {}
    
    # 1. Sub-akun (anak dari akun ini via induk_id self-ref)
    sub_count = db.query(AkunPerkiraan).filter(AkunPerkiraan.induk_id == coa_id).count()
    if sub_count > 0:
        deps["sub_akun"] = sub_count
    
    # 2. Dynamic: scan semua FK ke akun_perkiraan
    fks = _discover_fks_to_akun_perkiraan(db)
    for fk in fks:
        try:
            # Use raw SQL for safety (some tables might not be in ORM)
            sql = text(f"SELECT COUNT(*) FROM {fk['table_name']} WHERE {fk['column_name']} = :coa_id")
            count = db.execute(sql, {"coa_id": coa_id}).scalar() or 0
            if count > 0:
                deps[fk["table_name"]] = count
        except Exception as e:
            logger.warning(f"Failed to count {fk['table_name']}.{fk['column_name']}: {e}")
    
    return deps


# ==========================================
# Mode: preview
# ==========================================

def mode_preview(db: Session) -> int:
    """Tampilkan report tanpa write."""
    workbook_kodes = _workbook_kode_set()
    all_db_coa = db.query(AkunPerkiraan).order_by(AkunPerkiraan.kode).all()
    
    matched = []          # kode cocok dengan workbook
    orphan_no_deps = []   # kode TIDAK cocok workbook, tidak ada dependency → bisa dihapus
    orphan_with_deps = [] # kode TIDAK cocok workbook, ada dependency → harus jadi LEGACY
    
    for coa in all_db_coa:
        norm = _normalize_kode(coa.kode)
        if norm in workbook_kodes:
            matched.append(coa)
        else:
            deps = _check_dependencies(db, coa.id)
            if deps:
                orphan_with_deps.append((coa, deps))
            else:
                orphan_no_deps.append(coa)
    
    print("\n" + "=" * 70)
    print("  PREVIEW MODE — No changes will be made to DB")
    print("=" * 70)
    
    print(f"\n📊 Summary:")
    print(f"  Total COA in DB      : {len(all_db_coa)}")
    print(f"  Match workbook       : {len(matched)}   (akan di-KEEP)")
    print(f"  Orphan (no deps)     : {len(orphan_no_deps)}   (akan di-DELETE pada --apply-safe)")
    print(f"  Orphan (has deps)    : {len(orphan_with_deps)}   (akan di-LEGACY pada --apply-safe)")
    print(f"  Workbook records     : {len(COA_SYSTEM_MASTER)}")
    
    if orphan_no_deps:
        print(f"\n🗑️  Orphan akun yang akan DIHAPUS (--apply-safe):")
        for coa in orphan_no_deps[:50]:
            print(f"     {coa.kode:<20} {coa.nama[:60]}")
        if len(orphan_no_deps) > 50:
            print(f"     ... dan {len(orphan_no_deps) - 50} lainnya")
    
    if orphan_with_deps:
        print(f"\n📦 Orphan akun yang akan di-LEGACY-rename (--apply-safe):")
        for coa, deps in orphan_with_deps[:50]:
            dep_str = ", ".join(f"{k}={v}" for k, v in deps.items())
            print(f"     {coa.kode:<20} {coa.nama[:40]:<40} deps: {dep_str}")
        if len(orphan_with_deps) > 50:
            print(f"     ... dan {len(orphan_with_deps) - 50} lainnya")
    
    print("\n" + "=" * 70)
    if orphan_no_deps or orphan_with_deps:
        print("  Run --apply-safe untuk execute. WAJIB BACKUP DB dulu!")
    else:
        print("  ✅ DB sudah clean, semua akun match workbook. Tidak ada yang perlu diubah.")
    print("=" * 70)
    return 0


# ==========================================
# Mode: apply-safe
# ==========================================

def mode_apply_safe(db: Session) -> int:
    """Hapus orphan tanpa dependency. Mark legacy untuk orphan dengan dependency."""
    workbook_kodes = _workbook_kode_set()
    all_db_coa = db.query(AkunPerkiraan).order_by(AkunPerkiraan.kode).all()
    
    deleted_count = 0
    legacy_count = 0
    error_count = 0
    
    print(f"\n🚀 Apply-safe mode: {len(all_db_coa)} akun diproses")
    
    for coa in all_db_coa:
        norm = _normalize_kode(coa.kode)
        if norm in workbook_kodes:
            continue  # match workbook, skip
        
        deps = _check_dependencies(db, coa.id)
        
        if not deps:
            # Bisa dihapus
            try:
                db.delete(coa)
                db.flush()
                deleted_count += 1
                print(f"  🗑️  DELETED: {coa.kode} - {coa.nama[:50]}")
            except Exception as e:
                db.rollback()
                error_count += 1
                print(f"  ❌ ERROR delete {coa.kode}: {e}")
        else:
            # Punya dependency → mark legacy
            try:
                old_nama = coa.nama
                if not old_nama.startswith("[LEGACY]"):
                    coa.nama = f"[LEGACY] {old_nama}"
                coa.active = False
                coa.status = "NONAKTIF"
                coa.allow_system_posting = False
                coa.allow_manual_posting = False
                if not coa.system_account_type:
                    coa.system_account_type = "LEGACY_OTHER"
                db.add(coa)
                db.flush()
                legacy_count += 1
                dep_str = ", ".join(f"{k}={v}" for k, v in deps.items())
                print(f"  📦 LEGACY : {coa.kode} - {coa.nama[:50]} (deps: {dep_str})")
            except Exception as e:
                db.rollback()
                error_count += 1
                print(f"  ❌ ERROR legacy {coa.kode}: {e}")
    
    db.commit()
    
    print(f"\n{'=' * 70}")
    print(f"  APPLY-SAFE COMPLETE")
    print(f"  Deleted (no deps)        : {deleted_count}")
    print(f"  Marked as LEGACY         : {legacy_count}")
    print(f"  Errors                   : {error_count}")
    print(f"  Workbook-matched (skip)  : {len(all_db_coa) - deleted_count - legacy_count - error_count}")
    print(f"{'=' * 70}")
    return 0 if error_count == 0 else 1


# ==========================================
# Mode: apply-force (BERBAHAYA)
# ==========================================

def mode_apply_force(db: Session) -> int:
    """Wipe semua akun_perkiraan, lalu reseed dari workbook + apply migration.
    
    Pre-check: discover semua tabel dengan FK ke akun_perkiraan. Kalau ada
    tabel transaksi (jurnal_detail, pembayaran_rincian, aset_tetap, dll) yang
    NOT NULL dan punya rows → ABORT, suggest --apply-safe.
    """
    # === Pre-check: dynamic FK discovery ===
    print("\n🔍 Pre-check: scan semua FK ke akun_perkiraan...")
    fks = _discover_fks_to_akun_perkiraan(db)
    
    blocking_tables = []  # NOT NULL + has rows → blocks wipe
    nullable_tables = []  # nullable → can SET NULL
    notnull_empty = []    # NOT NULL but no rows → safe to DELETE
    
    for fk in fks:
        label = f"{fk['table_name']}.{fk['column_name']}"
        if fk["row_count"] > 0:
            if fk["is_nullable"]:
                nullable_tables.append((fk, label))
            else:
                blocking_tables.append((fk, label))
        else:
            notnull_empty.append((fk, label))
    
    print(f"   Found {len(fks)} FK references:")
    print(f"     - {len(nullable_tables)} nullable (can SET NULL):")
    for fk, label in nullable_tables:
        print(f"         • {label}: {fk['row_count']} rows")
    print(f"     - {len(notnull_empty)} NOT NULL but empty (safe to ignore):")
    for fk, label in notnull_empty:
        print(f"         • {label}")
    print(f"     - {len(blocking_tables)} NOT NULL with rows (BLOCKS wipe):")
    for fk, label in blocking_tables:
        print(f"         • {label}: {fk['row_count']} rows")
    
    if blocking_tables:
        print(f"\n❌ ABORT: Ada {len(blocking_tables)} tabel dengan FK NOT NULL yang masih punya rows.")
        print(f"   --apply-force tidak bisa dijalankan karena akan kehilangan data transaksi.")
        print(f"\n   Tabel yang memblok:")
        for fk, label in blocking_tables:
            print(f"     • {label}: {fk['row_count']} rows")
        print(f"\n   PILIHAN:")
        print(f"   1. Jalankan --apply-safe sebagai gantinya.")
        print(f"      Akun yang tidak match workbook akan di-rename '[LEGACY]' + inactive.")
        print(f"      Semua transaksi tetap utuh, hanya akun-akun lama yang di-mark legacy.")
        print(f"   2. Hapus manual data transaksi test (pembayaran, jurnal, aset) lalu run --apply-force lagi.")
        print(f"      Contoh: DELETE FROM pembayaran_rincian; DELETE FROM pembayaran_kas; ...")
        print(f"   3. Restore database dari backup dan ulangi dengan --apply-safe.")
        return 1
    
    print("\n⚠️  APLY-FORCE MODE — BERBAHAYA")
    print("   Semua akun_perkiraan akan dihapus dan di-reseed ulang.")
    print("   Semua FK referensi akan di-cleaned (SET NULL atau DELETE rows).")
    print()
    confirm = input("   Ketik 'WIPE' untuk konfirmasi: ").strip()
    if confirm != "WIPE":
        print("   Aborted.")
        return 1
    
    # === Step 1: Bersihkan FK referensi (dynamic) ===
    print("\n📝 Step 1: Bersihkan FK referensi (dynamic discovery)...")
    
    total_cleaned = 0
    for fk, label in nullable_tables + notnull_empty:
        table = fk["table_name"]
        column = fk["column_name"]
        is_nullable = fk["is_nullable"]
        
        try:
            nested = db.begin_nested()
            try:
                if is_nullable:
                    # SET NULL
                    sql = text(f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL")
                    result = db.execute(sql)
                    affected = result.rowcount or 0
                    nested.commit()
                    total_cleaned += affected
                    print(f"   ✓ {label}: {affected} rows NULL-ed")
                else:
                    # NOT NULL but empty → nothing to do
                    nested.rollback()
                    print(f"   ✓ {label}: 0 rows (empty, no action needed)")
            except Exception as e:
                nested.rollback()
                print(f"   ⚠️  Skip {label}: {e}")
        except Exception as e:
            print(f"   ⚠️  Skip {label}: {e}")
    
    # Also handle NOT NULL tables: DELETE FROM (kalau ada rows dari blocking_tables, udah di-abort di pre-check)
    # Tapi kalau ada NOT NULL table yang punya rows yang bukan blocking (misal setting_akun),
    # kita DELETE karena akan di-reseed oleh phase3_setting_akun_seed.
    notnull_to_delete = [
        ("setting_akun", "Setting akun (akan di-reseed oleh phase3_setting_akun_seed)"),
        ("kas_bank_akun", "Kas bank akun (akan di-reseed manual via modul Kas/Bank)"),
    ]
    for table, reason in notnull_to_delete:
        try:
            # Check if exists in our FK discovery (only delete if it has FK to akun_perkiraan)
            in_fks = any(fk["table_name"] == table for fk, _ in nullable_tables + notnull_empty + blocking_tables)
            if not in_fks:
                continue
            
            nested = db.begin_nested()
            try:
                count_sql = text(f"SELECT COUNT(*) FROM {table}")
                count = db.execute(count_sql).scalar() or 0
                if count == 0:
                    nested.rollback()
                    print(f"   ✓ {table}: 0 rows (skip)")
                    continue
                
                del_sql = text(f"DELETE FROM {table}")
                result = db.execute(del_sql)
                affected = result.rowcount or 0
                nested.commit()
                total_cleaned += affected
                print(f"   ✓ {table}: {affected} rows DELETED ({reason})")
            except Exception as e:
                nested.rollback()
                print(f"   ⚠️  Skip {table}: {e}")
                print(f"      Reason: {reason}")
                print(f"      Solution: hapus manual rows di tabel yang reference {table} lalu run ulang.")
        except Exception as e:
            print(f"   ⚠️  Skip {table}: {e}")
    
    db.commit()
    print(f"\n   Total rows cleaned: {total_cleaned}")
    
    # === Step 2: Wipe akun_perkiraan ===
    print("\n📝 Step 2: Wipe akun_perkiraan...")
    try:
        total_before = db.query(AkunPerkiraan).count()
        db.query(AkunPerkiraan).delete()
        db.flush()
        db.commit()
        print(f"   ✓ Deleted {total_before} akun_perkiraan")
    except Exception as e:
        db.rollback()
        print(f"\n❌ FAILED to wipe akun_perkiraan: {e}")
        print(f"   Masih ada FK yang belum di-clean. Daftar FK yang masih reference:")
        # Re-check remaining dependencies
        remaining_fks = _discover_fks_to_akun_perkiraan(db)
        for fk in remaining_fks:
            if fk["row_count"] > 0:
                print(f"     • {fk['table_name']}.{fk['column_name']}: {fk['row_count']} rows (nullable={fk['is_nullable']})")
        print(f"\n   SOLUSI:")
        print(f"   1. Jalankan --apply-safe sebagai gantinya (preserve transaksi).")
        print(f"   2. Atau hapus manual rows di tabel-tabel di atas, lalu run --apply-force lagi.")
        return 1
    
    # === Step 3: Reseed dari workbook (151 record) ===
    print("\n📝 Step 3: Reseed dari workbook (151 record)...")
    from app.seed.coa_system_master_seed import _resolve_parent_id
    
    inserted = 0
    for rec in COA_SYSTEM_MASTER:
        code = rec["account_code"]
        try:
            account_class = (rec.get("account_class") or "ASSET").upper()
            header = _header_enum_for_class(account_class)
            saldo_normal = SaldoNormal((rec.get("normal_balance") or "DEBIT").upper())
            tingkat = NODE_TYPE_TO_TINGKAT.get(
                (rec.get("node_type") or "DETAIL").upper(), TingkatAkun.DETAIL
            )
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
        except Exception as e:
            db.rollback()
            print(f"   ❌ Failed insert {code}: {e}")
            continue
    
    db.commit()
    print(f"   ✓ Inserted {inserted} / {len(COA_SYSTEM_MASTER)} records")
    
    # === Step 4: Apply migration map ===
    print("\n📝 Step 4: Apply migration map (control account + legacy lock)...")
    try:
        from app.services.coa_migration_service import apply_migration
        result = apply_migration(db, dry_run=False)
        print(f"   ✓ Migration applied: {result['applied_count']} applied, {result['skipped_count']} skipped")
    except Exception as e:
        print(f"   ⚠️  Migration runner failed: {e}")
        print(f"   Anda bisa run `python3 -m app.seed.coa_system_master_seed` + migration apply terpisah.")
    
    db.commit()
    
    print(f"\n{'=' * 70}")
    print(f"  APPLY-FORCE COMPLETE")
    print(f"  Wiped       : {total_before} akun lama")
    print(f"  Inserted    : {inserted} akun workbook")
    print(f"  Migration   : applied (control account + legacy lock)")
    print(f"{'=' * 70}")
    print(f"\n  ⚠️  PENTING:")
    print(f"  - Setting akun (PIUTANG_USAHA, dll) sekarang kosong. Jalankan:")
    print(f"      python3 -m app.seed.phase3_setting_akun_seed")
    print(f"  - Master data pelanggan/supplier akun_piutang/akun_hutang juga NULL.")
    print(f"    Re-link manual via PUT /master/pelanggan/{{id}} atau re-create pelanggan/supplier.")
    return 0


# ==========================================
# Main
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Replace COA di DB dengan workbook ASAHI Revisi v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m app.seed.coa_full_replace --preview
  python3 -m app.seed.coa_full_replace --apply-safe
  python3 -m app.seed.coa_full_replace --apply-force

⚠️  WAJIB BACKUP DATABASE sebelum --apply-safe atau --apply-force!
        """
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", default=True,
                       help="Tampilkan report tanpa write (default)")
    mode.add_argument("--apply-safe", action="store_true",
                       help="Hapus orphan tanpa deps, mark legacy untuk orphan dengan deps")
    mode.add_argument("--apply-force", action="store_true",
                       help="BERBAHAYA: wipe semua akun + reseed (hanya kalau belum ada jurnal)")
    
    args = parser.parse_args()
    
    db: Session = SessionLocal()
    try:
        if args.apply_force:
            return mode_apply_force(db)
        elif args.apply_safe:
            return mode_apply_safe(db)
        else:
            return mode_preview(db)
    except Exception as e:
        db.rollback()
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
