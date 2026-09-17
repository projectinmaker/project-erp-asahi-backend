"""COA revision import: read-only preview and atomic, history-preserving apply.

The full master importer and the 17-item migration endpoint share one planner.
No journal, balance, customer, supplier, or existing account ID is rewritten.
"""
from decimal import Decimal
from sqlalchemy import text
from app.models.akun_perkiraan import AkunPerkiraan
from app.models.master.setting_akun import SettingAkun
from app.seed.data.coa_system_master_data import COA_SYSTEM_MASTER, MIGRATION_MAP
from app.services.accounting_control import accounting_lock
from app.services.coa_import_plan import RULE_FIELDS, build_plan


def _snapshot(db, full_master):
    accounts = db.query(AkunPerkiraan).all()
    by_code = {row.kode: row for row in accounts}
    by_id = {row.id: row.kode for row in accounts}
    fields = ("id", "kode", "nama", "header", "tingkat", "saldo_normal", "saldo", "tanggal", "is_subledger", "induk_kode", "status", "created_at", "updated_at", *RULE_FIELDS)
    existing = []
    for row in accounts:
        record = {key: getattr(getattr(row, key), "value", getattr(row, key)) for key in fields}
        # Include the actual FK in the fingerprint; catch inconsistent denormalized parents.
        record["induk_id"] = row.induk_id
        parent_code = by_id.get(row.induk_id) if row.induk_id else None
        if parent_code != row.induk_kode:
            raise ValueError(f"Relasi induk akun {row.kode} tidak konsisten; periksa induk_id/induk_kode sebelum import")
        existing.append(record)
    settings = []
    if full_master:
        settings = [{"key": row.key, "id": row.id, "account_code": by_id.get(row.akun_perkiraan_id), "akun_perkiraan_id": row.akun_perkiraan_id} for row in db.query(SettingAkun).all()]
    return by_code, existing, settings


def _run(db, *, dry_run, full_master, action_filter=None, expected_fingerprint=None):
    if db.new or db.dirty or db.deleted:
        raise ValueError("Gunakan sesi bersih untuk import COA; ada perubahan lain yang belum disimpan")
    try:
        if not dry_run:
            accounting_lock(db)
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("LOCK TABLE akun_perkiraan IN SHARE ROW EXCLUSIVE MODE"))
                if full_master:
                    db.execute(text("LOCK TABLE setting_akun IN SHARE ROW EXCLUSIVE MODE"))
        with db.no_autoflush:
            by_code, existing, settings = _snapshot(db, full_master)
            plan = build_plan(COA_SYSTEM_MASTER, MIGRATION_MAP, existing, full_master=full_master, action_filter=action_filter, settings=settings)
        if dry_run:
            # No add/flush/commit/savepoint here, including when called from GET.
            return plan
        if plan["blockers"]:
            raise ValueError("Import diblokir: " + "; ".join(plan["blockers"]))
        if expected_fingerprint is not None and expected_fingerprint != plan["fingerprint"]:
            raise ValueError("Data/source berubah sejak preview. Buat dan review preview baru sebelum apply")
        for result in plan["results"]:
            if not result["applied"]:
                continue
            code = result["account_code"]
            fields = dict(result["changes"])
            account = by_code.get(code)
            if account is None:
                parent_code = fields.get("induk_kode")
                parent = by_code.get(parent_code) if parent_code else None
                if parent_code and parent is None:
                    raise ValueError(f"Induk akun {code} tidak ditemukan: {parent_code}")
                account = AkunPerkiraan(kode=code, induk_id=parent.id if parent else None, saldo=Decimal("0"), tanggal=None, is_subledger=False, **fields)
                db.add(account)
                db.flush()  # Resolve parent IDs for subsequent children.
                by_code[code] = account
            else:
                for key, value in fields.items():
                    setattr(account, key, value)
            result["message"] = "Diterapkan: " + ", ".join(fields)
        for setting in plan["setting_changes"]:
            db.add(SettingAkun(key=setting["key"], label=setting["label"], akun_perkiraan_id=by_code[setting["account_code"]].id))
        db.flush()
        if full_master:
            from app.services.setting_akun_service import clear_cache
            clear_cache()
        db.commit()
        plan["dry_run"] = False
        return plan
    except Exception:
        if not dry_run:
            db.rollback()
        raise


def apply_migration(db, dry_run=False, action_filter=None):
    """Compatibility endpoint: only the workbook's migration-map accounts."""
    plan = _run(db, dry_run=dry_run, full_master=False, action_filter=action_filter)
    if dry_run and plan["blockers"]:
        raise ValueError("Preview diblokir: " + "; ".join(plan["blockers"]))
    if dry_run:
        for item in plan["results"]:
            if item["changes"]:
                item["message"] += ": " + ", ".join(f"{key}: {item['before'].get(key)} -> {value}" for key, value in item["changes"].items())
    return plan


def import_system_master(db, *, dry_run=True, expected_fingerprint=None):
    """Full 151-account upsert; preview is the default."""
    if not dry_run and not expected_fingerprint:
        raise ValueError("Apply master wajib menggunakan fingerprint hasil preview yang telah direview")
    return _run(db, dry_run=dry_run, full_master=True, expected_fingerprint=expected_fingerprint)


def get_migration_preview():
    return list(MIGRATION_MAP)


def get_system_master_preview():
    return list(COA_SYSTEM_MASTER)
