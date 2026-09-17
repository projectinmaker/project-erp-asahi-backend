"""Pure, read-only planning for the reviewed COA revision. No database imports."""
import hashlib
import json

CLASS_HEADERS = {"ASSET": "AKTIVA", "LIABILITY": "KEWAJIBAN", "EQUITY": "MODAL", "REVENUE": "PENDAPATAN", "COGS": "HPP", "EXPENSE": "BEBAN"}
RULE_FIELDS = ("account_class", "account_subclass", "financial_statement", "report_group", "system_account_type", "allow_system_posting", "allow_manual_posting", "is_control_account", "subledger_type", "reconciliation_required", "active")
BOOL_FIELDS = ("allow_system_posting", "allow_manual_posting", "is_control_account", "reconciliation_required", "active")
ACTIONS = {"INSERT", "UPDATE_CONTROL_RULE", "UPDATE_NAME_NOTE", "UPDATE_LOCK_LEGACY"}
NEW_SETTINGS = {
    "BANK_CLEARING": ("111200", "Akun Clearing / Ayat Silang", "BANK_CLEARING"),
    "HPP_PRODUK_JADI": ("531001", "HPP Produk Jadi", "COGS_FINISHED_GOODS"),
    "LABA_RUGI_TAHUN_BERJALAN": ("322000", "Laba (Rugi) Tahun Berjalan", "CURRENT_EARNINGS"),
    "LABA_DITAHAN": ("321000", "Laba (Rugi) Ditahan", "RETAINED_EARNINGS"),
}


def validate_source(master, migration):
    indexed = {}
    if not master:
        raise ValueError("COA_SYSTEM_MASTER kosong")
    for row in master:
        code = row.get("account_code")
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit() or code in indexed:
            raise ValueError(f"Kode akun tidak valid/duplikat: {code}")
        if not row.get("account_name") or len(row["account_name"]) > 200:
            raise ValueError(f"Nama akun tidak valid: {code}")
        if row.get("account_class") not in CLASS_HEADERS or row.get("normal_balance") not in {"DEBIT", "KREDIT"} or row.get("node_type") not in {"HEADER", "GROUP", "DETAIL"}:
            raise ValueError(f"Klasifikasi akun tidak valid: {code}")
        for field in BOOL_FIELDS:
            if type(row.get(field)) is not bool:
                raise ValueError(f"{code}.{field} wajib boolean; jangan gunakan bool('NO')")
        if row["node_type"] != "DETAIL" and (row["allow_system_posting"] or row["allow_manual_posting"]):
            raise ValueError(f"HEADER/GROUP tidak boleh posting: {code}")
        if row["is_control_account"] and not row.get("subledger_type"):
            raise ValueError(f"Control account tanpa subledger: {code}")
        indexed[code] = row
    ordered, visiting, visited = [], set(), set()
    def visit(code):
        if code in visiting:
            raise ValueError(f"Siklus induk akun: {code}")
        if code in visited:
            return
        visiting.add(code)
        parent = indexed[code].get("parent_code")
        if parent:
            if parent not in indexed or indexed[parent]["node_type"] == "DETAIL":
                raise ValueError(f"Induk tidak valid: {code} -> {parent}")
            visit(parent)
        visiting.remove(code)
        visited.add(code)
        ordered.append(indexed[code])
    for code in indexed:
        visit(code)
    seen_actions = set()
    for item in migration:
        code = item.get("account_code")
        if item.get("migration_action") not in ACTIONS or code not in indexed or code in seen_actions:
            raise ValueError(f"Migration map tidak valid/duplikat: {code}")
        seen_actions.add(code)
        if item.get("new_account_name") != indexed[code]["account_name"]:
            raise ValueError(f"Nama migration map berbeda dari master: {code}")
    return ordered


def target_fields(row):
    return {**{key: row.get(key) for key in RULE_FIELDS}, "nama": row["account_name"], "header": CLASS_HEADERS[row["account_class"]], "tingkat": row["node_type"], "saldo_normal": row["normal_balance"], "induk_kode": row.get("parent_code"), "status": "AKTIF" if row["active"] else "NONAKTIF"}


def build_plan(master, migration, existing, *, full_master=False, action_filter=None, settings=()):
    ordered = validate_source(master, migration)
    if action_filter is not None and set(action_filter) - ACTIONS:
        raise ValueError("Action filter tidak dikenal")
    indexed = {row["kode"]: row for row in existing}
    if len(indexed) != len(existing):
        raise ValueError("Database berisi kode akun duplikat")
    mapping = {row["account_code"]: row for row in migration}
    targets = ordered if full_master else [row for row in ordered if row["account_code"] in mapping and (not action_filter or mapping[row["account_code"]]["migration_action"] in action_filter)]
    results, blockers, warnings = [], [], []
    available = set(indexed)
    if full_master and existing and not (set(indexed) & {row["account_code"] for row in master}):
        blockers.append("Kode COA database tidak cocok dengan workbook; diperlukan mapping kode lama ke baru. Tidak ada matching berdasarkan nama.")
    for row in targets:
        code = row["account_code"]
        old = indexed.get(code)
        action = mapping.get(code, {}).get("migration_action", "UPSERT_MASTER")
        desired = target_fields(row)
        problems = []
        changes = {}
        if old is None:
            if not full_master and action != "INSERT":
                problems.append("Akun belum ada. Jalankan preview import master lengkap terlebih dahulu.")
            if row.get("parent_code") and row["parent_code"] not in available:
                problems.append(f"Induk {row['parent_code']} belum ada")
            changes = desired
        else:
            if old.get("is_subledger"):
                problems.append("Kode dipakai akun subledger; relasi historis tidak boleh ditimpa")
            for key in ("header", "tingkat", "saldo_normal", "induk_kode"):
                if old.get(key) != desired[key]:
                    problems.append(f"Perbedaan struktur {key}: {old.get(key)} -> {desired[key]}; perlu review histori")
            allowed_names = {row["account_name"], mapping.get(code, {}).get("old_account_name"), mapping.get(code, {}).get("new_account_name")}
            if old.get("nama") not in allowed_names:
                problems.append("Nama akun existing berbeda; pastikan kode tidak dipakai untuk arti lain")
            # Structural changes are never emitted for existing accounts.
            changes = {key: value for key, value in desired.items() if key not in {"header", "tingkat", "saldo_normal", "induk_kode"} and old.get(key) != value}
        if problems:
            blockers.extend(f"{code}: {message}" for message in problems)
        else:
            available.add(code)
        results.append({"action": action if not full_master else ("INSERT" if old is None else "UPDATE"), "account_code": code, "old_name": old.get("nama") if old else None, "new_name": row["account_name"], "applied": bool(changes) and not problems, "message": "; ".join(problems) if problems else ("Perubahan siap diterapkan" if changes else "Sudah sesuai; tidak ada perubahan"), "changes": changes, "before": {key: old.get(key) for key in changes} if old else {}, "existing_id": old.get("id") if old else None})
    setting_changes = []
    if full_master:
        current_settings = {item["key"]: item for item in settings}
        for key, (code, label, system_type) in NEW_SETTINGS.items():
            old = current_settings.get(key)
            if old:
                if old.get("account_code") != code:
                    warnings.append(f"Setting {key} tetap menunjuk {old.get('account_code')}; rekomendasi {code}. Mapping existing tidak ditimpa.")
            elif code not in available:
                blockers.append(f"Setting {key}: akun {code} belum tersedia")
            else:
                setting_changes.append({"key": key, "account_code": code, "label": label})
        for item in settings:
            target = next((r for r in master if r["account_code"] == item.get("account_code")), None)
            if target and not target["allow_system_posting"] and item["key"] not in {"LABA_RUGI_TAHUN_BERJALAN", "LABA_DITAHAN", "LABA_RUGI_BERJALAN"}:
                warnings.append(f"Review setting {item['key']}: akun {item.get('account_code')} tidak menerima posting sistem setelah import.")
    fingerprint_data = {"master": master, "migration": migration, "existing": sorted(existing, key=lambda r: r["kode"]), "settings": sorted(settings, key=lambda r: r["key"]), "full_master": full_master, "action_filter": sorted(action_filter or [])}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True, default=str).encode()).hexdigest()
    count = sum(row["applied"] for row in results)
    return {"dry_run": True, "total_items": len(results), "applied_count": count, "skipped_count": len(results)-count, "results": results, "blockers": blockers, "warnings": warnings, "setting_changes": setting_changes, "fingerprint": fingerprint}
