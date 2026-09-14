"""Preview/apply COA revision. Default is read-only; see COA_IMPORT_RUNBOOK.md."""
import argparse
import json
from pathlib import Path
from app.seed.data.coa_system_master_data import COA_SYSTEM_MASTER, MIGRATION_MAP
from app.services.coa_import_plan import validate_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true", help="Validate bundled workbook data without database access")
    mode.add_argument("--preview", action="store_true", help="Read database and produce a plan (default)")
    mode.add_argument("--apply", action="store_true", help="Apply the reviewed plan in one transaction")
    parser.add_argument("--expected-fingerprint", help="Exact fingerprint from the reviewed preview")
    parser.add_argument("--output", type=Path, help="Save detailed before/after audit JSON")
    args = parser.parse_args()
    if args.apply and (not args.expected_fingerprint or not args.output):
        parser.error("--apply requires --expected-fingerprint and --output")
    validate_source(COA_SYSTEM_MASTER, MIGRATION_MAP)
    if args.validate_only:
        result = {"source_valid": True, "account_count": len(COA_SYSTEM_MASTER), "migration_count": len(MIGRATION_MAP), "database_accessed": False}
    else:
        # Lazy imports ensure --validate-only never loads credentials or an engine.
        from app.database import SessionLocal
        from app.services.coa_migration_service import import_system_master
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Prove the audit destination is writable before changing the database.
            with args.output.open("x", encoding="utf-8") as file:
                json.dump({"status": "started", "apply": args.apply, "expected_fingerprint": args.expected_fingerprint}, file)
        with SessionLocal() as db:
            try:
                result = import_system_master(db, dry_run=not args.apply, expected_fingerprint=args.expected_fingerprint)
            finally:
                # A preview's read transaction is always closed without commit.
                if not args.apply:
                    db.rollback()
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return 2 if result.get("blockers") else 0


if __name__ == "__main__":
    raise SystemExit(main())
