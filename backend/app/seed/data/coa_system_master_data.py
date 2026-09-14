"""Auto-generated COA system master data module.

Source: COA_ASAHI_FINAL_REVISI_SIAP_UPLOAD_V2.xlsx
Re-generated via: scripts/extract_coa_workbook_to_seed.py

Implementation: load from JSON file at import time (more robust than inline
Python literals — avoids null/None, true/True, false/False syntax mismatch).

JSON file `coa_system_master_data.json` contains both:
    - coa_system_master: list[dict] of 151 COA records (sheet COA_SYSTEM_MASTER)
    - migration_map:     list[dict] of 17 migration items (sheet MIGRATION_MAP)

Exposes:
    COA_SYSTEM_MASTER: list[dict] of 151 COA records
    MIGRATION_MAP:     list[dict] of 17 migration items
"""

import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent
_JSON_FILE = _DATA_DIR / "coa_system_master_data.json"


def _load_combined() -> dict:
    """Load JSON file (dict with both COA_SYSTEM_MASTER and MIGRATION_MAP)."""
    if not _JSON_FILE.exists():
        raise FileNotFoundError(
            f"Data file not found: {_JSON_FILE}. "
            "Re-generate via scripts/extract_coa_workbook_to_seed.py"
        )
    with _JSON_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


_combined = _load_combined()

COA_SYSTEM_MASTER: list = _combined.get("coa_system_master", [])
MIGRATION_MAP: list = _combined.get("migration_map", [])

__all__ = ["COA_SYSTEM_MASTER", "MIGRATION_MAP"]