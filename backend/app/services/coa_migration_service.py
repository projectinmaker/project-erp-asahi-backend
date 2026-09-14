"""coa_migration_service.py

Service untuk apply migration map dari workbook COA_ASAHI_FINAL_REVISI_SIAP_UPLOAD_V2.

Mendukung 4 action:
- INSERT                  : tambah akun baru (kalau belum ada)
- UPDATE_CONTROL_RULE     : update flag control account / posting rules
- UPDATE_NAME_NOTE       : update nama akun + system_account_type
- UPDATE_LOCK_LEGACY     : lock akun legacy (511xxx) supaya tidak bisa
                            dipakai transaksi baru, tapi tetap muncul
                            di laporan historis

Sumber data: app.seed.data.coa_system_master_data (auto-generated dari workbook).
"""
from typing import List, Optional
from decimal import Decimal
from datetime import datetime

from loguru import logger
from sqlalchemy.orm import Session

from app.models.akun_perkiraan import (
    AkunPerkiraan,
    HeaderCOA,
    SaldoNormal,
    TingkatAkun,
    HEADER_TO_ACCOUNT_CLASS,
    NODE_TYPE_TO_TINGKAT,
)
from app.seed.data.coa_system_master_data import (
    COA_SYSTEM_MASTER,
    MIGRATION_MAP,
)
from app.services.accounting_control import atomic_accounting_write


# ==========================================
# Helpers
# ==========================================
def _lookup_master_by_code(account_code: str) -> Optional[dict]:
    """Cari record di COA_SYSTEM_MASTER by account_code. Return dict atau None."""
    for rec in COA_SYSTEM_MASTER:
        if rec["account_code"] == account_code:
            return rec
    return None


def _lookup_master_by_parent_code(parent_code: Optional[str]) -> Optional[dict]:
    """Cari record di COA_SYSTEM_MASTER by parent_code (for INSERT chain)."""
    if not parent_code:
        return None
    for rec in COA_SYSTEM_MASTER:
        if rec.get("parent_code") == parent_code:
            return rec
    return None


def _resolve_parent_id(db: Session, parent_code: Optional[str]) -> Optional[str]:
    """Cari id (UUID string) akun induk di DB by kode. None kalau gak ketemu."""
    if not parent_code:
        return None
    parent = (
        db.query(AkunPerkiraan.id)
        .filter(AkunPerkiraan.kode == parent_code)
        .first()
    )
    return str(parent[0]) if parent else None


def _header_enum_for_class(account_class: str) -> HeaderCOA:
    """Reverse map: account_class -> HeaderCOA enum (legacy compat)."""
    reverse = {v: k for k, v in HEADER_TO_ACCOUNT_CLASS.items()}
    return reverse.get(account_class.upper(), HeaderCOA.AKTIVA)


# ==========================================
# Action handlers
# ==========================================
def _action_insert(db: Session, item: dict) -> dict:
    """INSERT akun baru. Skip kalau kode sudah ada (idempotent)."""
    code = item["account_code"]
    new_name = item.get("new_account_name")

    existing = (
        db.query(AkunPerkiraan)
        .filter(AkunPerkiraan.kode == code)
        .first()
    )
    if existing:
        return {
            "action": "INSERT",
            "account_code": code,
            "new_name": new_name,
            "applied": False,
            "message": f"Akun {code} sudah ada di DB, skip INSERT (idempotent).",
        }

    # Cari master record untuk ambil atribut lengkap
    master = _lookup_master_by_code(code)
    if not master:
        # INSERT tanpa master record — pakai minimal info dari migration map
        # (misalnya 530000 group HPP, tidak punya subclass/system_account_type di map)
        # Kita ambil defaults konservatif.
        logger.warning(
            f"INSERT {code} tidak ditemukan di COA_SYSTEM_MASTER, pakai default minimal"
        )
        master = {
            "account_code": code,
            "account_name": new_name,
            "parent_code": None,
            "level": 3,
            "account_class": "COGS" if "HPP" in (new_name or "").upper() else "EXPENSE",
            "account_subclass": None,
            "normal_balance": "DEBIT",
            "node_type": "GROUP",
            "allow_system_posting": False,
            "allow_manual_posting": False,
            "is_control_account": False,
            "subledger_type": None,
            "financial_statement": "LABA RUGI",
            "report_group": "COGS",
            "system_account_type": None,
            "reconciliation_required": False,
            "active": True,
        }

    # Resolve parent & induk_kode
    parent_code = master.get("parent_code")
    induk_id = _resolve_parent_id(db, parent_code)
    induk_kode = parent_code  # denormalized

    # Resolve enums
    account_class = (master.get("account_class") or "ASSET").upper()
    header = _header_enum_for_class(account_class)
    saldo_normal = SaldoNormal((master.get("normal_balance") or "DEBIT").upper())
    tingkat = NODE_TYPE_TO_TINGKAT.get(
        (master.get("node_type") or "DETAIL").upper(), TingkatAkun.DETAIL
    )

    # Default saldo 0 (INSERT migration tidak set saldo awal — itu via saldo_awal endpoint)
    new_coa = AkunPerkiraan(
        kode=code,
        nama=master.get("account_name") or new_name,
        header=header,
        tingkat=tingkat,
        induk_id=induk_id,
        induk_kode=induk_kode,
        saldo_normal=saldo_normal,
        saldo=Decimal("0"),
        tanggal=None,
        status="AKTIF",
        is_subledger=False,
        account_class=account_class,
        account_subclass=master.get("account_subclass"),
        financial_statement=master.get("financial_statement"),
        report_group=master.get("report_group"),
        system_account_type=master.get("system_account_type"),
        allow_system_posting=bool(master.get("allow_system_posting", True)),
        allow_manual_posting=bool(master.get("allow_manual_posting", True)),
        is_control_account=bool(master.get("is_control_account", False)),
        subledger_type=master.get("subledger_type"),
        reconciliation_required=bool(master.get("reconciliation_required", False)),
        active=bool(master.get("active", True)),
    )
    db.add(new_coa)
    db.flush()
    logger.info(
        f"COA INSERT: {code} - {new_coa.nama} (class={account_class}, "
        f"subledger={new_coa.subledger_type}, control={new_coa.is_control_account})"
    )
    return {
        "action": "INSERT",
        "account_code": code,
        "new_name": new_name,
        "applied": True,
        "message": f"Akun baru {code} - {new_coa.nama} berhasil dibuat.",
    }


def _action_update_control_rule(db: Session, item: dict) -> dict:
    """UPDATE_CONTROL_RULE: set is_control_account, subledger_type,
    allow_system_posting, allow_manual_posting, reconciliation_required,
    system_account_type. Tidak ubah nama.
    """
    code = item["account_code"]
    coa = (
        db.query(AkunPerkiraan)
        .filter(AkunPerkiraan.kode == code)
        .first()
    )
    if not coa:
        return {
            "action": "UPDATE_CONTROL_RULE",
            "account_code": code,
            "new_name": item.get("new_account_name"),
            "applied": False,
            "message": f"Akun {code} tidak ditemukan di DB, skip.",
        }

    master = _lookup_master_by_code(code)
    if master:
        coa.is_control_account = bool(master.get("is_control_account", False))
        coa.subledger_type = master.get("subledger_type")
        coa.allow_system_posting = bool(master.get("allow_system_posting", True))
        coa.allow_manual_posting = bool(master.get("allow_manual_posting", True))
        coa.reconciliation_required = bool(master.get("reconciliation_required", False))
        coa.system_account_type = master.get("system_account_type")
        if master.get("account_class"):
            coa.account_class = master["account_class"]
        if master.get("account_subclass"):
            coa.account_subclass = master["account_subclass"]
        if master.get("financial_statement"):
            coa.financial_statement = master["financial_statement"]
        if master.get("report_group"):
            coa.report_group = master["report_group"]
    else:
        # Fallback: set minimal dari mapping note
        coa.is_control_account = True
        # mapping field menyimpan key seperti AR_CONTROL/AP_CONTROL/INVENTORY_*
        mapping = (item.get("account_mapping") or "").upper()
        if mapping in ("AR_CONTROL", "AP_CONTROL", "INVENTORY_RAW", "INVENTORY_AUX",
                        "INVENTORY_WIP", "INVENTORY_FINISHED", "BANK_CLEARING",
                        "CURRENT_EARNINGS"):
            coa.system_account_type = mapping
        if mapping == "AR_CONTROL":
            coa.subledger_type = "AR"
            coa.allow_manual_posting = False
        elif mapping == "AP_CONTROL":
            coa.subledger_type = "AP"
            coa.allow_manual_posting = False
        elif mapping.startswith("INVENTORY_"):
            coa.subledger_type = "INVENTORY"
        elif mapping == "BANK_CLEARING":
            coa.subledger_type = "BANK_TRANSFER"
        elif mapping == "CURRENT_EARNINGS":
            coa.allow_system_posting = False
            coa.allow_manual_posting = False

    db.add(coa)
    db.flush()
    logger.info(
        f"COA UPDATE_CONTROL_RULE: {code} -> control={coa.is_control_account}, "
        f"subledger={coa.subledger_type}, manual_post={coa.allow_manual_posting}, "
        f"system_post={coa.allow_system_posting}, sat={coa.system_account_type}"
    )
    return {
        "action": "UPDATE_CONTROL_RULE",
        "account_code": code,
        "new_name": item.get("new_account_name"),
        "applied": True,
        "message": f"Control rule {code} updated: control={coa.is_control_account}, subledger={coa.subledger_type}.",
    }


def _action_update_name_note(db: Session, item: dict) -> dict:
    """UPDATE_NAME_NOTE: update nama akun (rename dengan catatan)."""
    code = item["account_code"]
    new_name = item.get("new_account_name")
    coa = (
        db.query(AkunPerkiraan)
        .filter(AkunPerkiraan.kode == code)
        .first()
    )
    if not coa:
        return {
            "action": "UPDATE_NAME_NOTE",
            "account_code": code,
            "new_name": new_name,
            "applied": False,
            "message": f"Akun {code} tidak ditemukan di DB, skip.",
        }
    if not new_name:
        return {
            "action": "UPDATE_NAME_NOTE",
            "account_code": code,
            "new_name": new_name,
            "applied": False,
            "message": f"new_account_name kosong untuk {code}, skip.",
        }

    old_name = coa.nama
    coa.nama = new_name

    # Update system_account_type kalau mapping diisi
    mapping = item.get("account_mapping")
    if mapping:
        coa.system_account_type = mapping

    db.add(coa)
    db.flush()
    logger.info(f"COA UPDATE_NAME_NOTE: {code} '{old_name}' -> '{new_name}'")
    return {
        "action": "UPDATE_NAME_NOTE",
        "account_code": code,
        "old_name": old_name,
        "new_name": new_name,
        "applied": True,
        "message": f"Akun {code} renamed: '{old_name}' -> '{new_name}'.",
    }


def _action_update_lock_legacy(db: Session, item: dict) -> dict:
    """UPDATE_LOCK_LEGACY: lock akun legacy (511xxx) supaya tidak bisa
    dipakai transaksi baru.

    Action:
    - allow_system_posting = False
    - allow_manual_posting = False
    - active tetap True (supaya tetap muncul di laporan historis)
    - system_account_type = 'LEGACY_COGS_PURCHASE' (kalau mapping diisi)
    """
    code = item["account_code"]
    new_name = item.get("new_account_name")
    coa = (
        db.query(AkunPerkiraan)
        .filter(AkunPerkiraan.kode == code)
        .first()
    )
    if not coa:
        return {
            "action": "UPDATE_LOCK_LEGACY",
            "account_code": code,
            "new_name": new_name,
            "applied": False,
            "message": f"Akun {code} tidak ditemukan di DB, skip.",
        }

    old_name = coa.nama
    coa.allow_system_posting = False
    coa.allow_manual_posting = False
    # active tetap True — kunci di posting rule, bukan di visibility
    coa.active = True
    # sync status
    coa.status = "AKTIF"

    if new_name:
        coa.nama = new_name  # rename dengan suffix [LEGACY - ...]
    if item.get("account_mapping"):
        coa.system_account_type = item["account_mapping"]

    db.add(coa)
    db.flush()
    logger.info(
        f"COA UPDATE_LOCK_LEGACY: {code} '{old_name}' -> '{coa.nama}' "
        f"(locked: system_post=False, manual_post=False, active=True, "
        f"sat={coa.system_account_type})"
    )
    return {
        "action": "UPDATE_LOCK_LEGACY",
        "account_code": code,
        "old_name": old_name,
        "new_name": coa.nama,
        "applied": True,
        "message": f"Akun {code} dikunci untuk transaksi baru (legacy). Histori tetap terbaca.",
    }


_ACTION_HANDLERS = {
    "INSERT": _action_insert,
    "UPDATE_CONTROL_RULE": _action_update_control_rule,
    "UPDATE_NAME_NOTE": _action_update_name_note,
    "UPDATE_LOCK_LEGACY": _action_update_lock_legacy,
}


# ==========================================
# Public API
# ==========================================
@atomic_accounting_write
def apply_migration(
    db: Session,
    dry_run: bool = False,
    action_filter: Optional[List[str]] = None,
) -> dict:
    """Apply migration map dari workbook ke DB.

    Parameter:
        dry_run: True = hanya return preview, tidak commit. Default False.
        action_filter: list action yang mau di-apply saja (subset dari
            INSERT/UPDATE_CONTROL_RULE/UPDATE_NAME_NOTE/UPDATE_LOCK_LEGACY).
            None = apply semua.

    Return: dict dengan key:
        - dry_run: bool
        - total_items: int (jumlah item di migration map yang match filter)
        - applied_count: int
        - skipped_count: int
        - results: List[dict] (satu per item, berisi applied/message)
    """
    # Kalau dry_run, kita jangan pakai @atomic_accounting_write commit.
    # Trick: kalau dry_run, kita raise exception di akhir supaya transaksi
    # di-rollback. Tapi lebih bersih: dry_run tidak pakai decorator ini.
    # Untuk simplicity, kita handle dengan nested transaction manual.
    if dry_run:
        # Dry run: jangan pakai transaction context decorator — handle manual
        return _apply_migration_dry_run(db, action_filter)

    results = []
    for item in MIGRATION_MAP:
        action = item["migration_action"]
        if action_filter and action not in action_filter:
            continue
        handler = _ACTION_HANDLERS.get(action)
        if not handler:
            results.append({
                "action": action,
                "account_code": item["account_code"],
                "new_name": item.get("new_account_name"),
                "applied": False,
                "message": f"Action {action} tidak dikenal, skip.",
            })
            continue
        results.append(handler(db, item))

    applied_count = sum(1 for r in results if r["applied"])
    skipped_count = len(results) - applied_count
    logger.info(
        f"Migration apply: total={len(results)} applied={applied_count} "
        f"skipped={skipped_count} (dry_run=False)"
    )
    return {
        "dry_run": False,
        "total_items": len(results),
        "applied_count": applied_count,
        "skipped_count": skipped_count,
        "results": results,
    }


def _apply_migration_dry_run(db: Session, action_filter: Optional[List[str]]) -> dict:
    """Preview tanpa write. Pakai SAVEPOINT yang di-rollback di akhir."""
    from sqlalchemy.orm import begin_nested
    results = []
    try:
        nested = begin_nested(db)
        try:
            for item in MIGRATION_MAP:
                action = item["migration_action"]
                if action_filter and action not in action_filter:
                    continue
                handler = _ACTION_HANDLERS.get(action)
                if not handler:
                    results.append({
                        "action": action,
                        "account_code": item["account_code"],
                        "new_name": item.get("new_account_name"),
                        "applied": False,
                        "message": f"Action {action} tidak dikenal, skip.",
                    })
                    continue
                results.append(handler(db, item))
            nested.rollback()  # discard semua changes
        except Exception:
            nested.rollback()
            raise
    except Exception as e:
        logger.error(f"Dry run error: {e}")
        raise

    applied_count = sum(1 for r in results if r["applied"])
    skipped_count = len(results) - applied_count
    logger.info(
        f"Migration preview (dry_run=True): total={len(results)} "
        f"would_apply={applied_count} would_skip={skipped_count}"
    )
    return {
        "dry_run": True,
        "total_items": len(results),
        "applied_count": applied_count,
        "skipped_count": skipped_count,
        "results": results,
    }


def get_migration_preview() -> List[dict]:
    """Return migration map as-is (tanpa apply). Untuk dokumentasi FE."""
    return list(MIGRATION_MAP)


def get_system_master_preview() -> List[dict]:
    """Return COA_SYSTEM_MASTER as-is (tanpa apply). Untuk dokumentasi FE."""
    return list(COA_SYSTEM_MASTER)
