"""Compatibility entrypoint for the non-destructive COA revision importer.

Default: preview. For explicit apply, follow COA_IMPORT_RUNBOOK.md.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.akun_perkiraan import AkunPerkiraan
from app.seed.coa_revision_import import main


def _normalize_kode(kode: Optional[str]) -> str:
    """Normalize kode COA untuk matching.

    Hapus semua titik dan strip, jadi '111.200.001' dan '111200001'
    keduanya jadi '111200001'.
    """
    if not kode:
        return ""
    return str(kode).replace(".", "").strip()


def _resolve_parent_id(db: Session, parent_code: Optional[str]) -> Optional[str]:
    """Resolve induk_id dari parent_code (normalized matching).

    Return None kalau parent_code kosong atau tidak ketemu (root account).
    Dipakai coa_full_replace --apply-force saat reseed workbook.
    """
    if not parent_code:
        return None
    normalized = _normalize_kode(parent_code)
    for row in db.query(AkunPerkiraan.kode, AkunPerkiraan.id).all():
        if _normalize_kode(row.kode) == normalized:
            return row.id
    return None


def seed_coa_system_master():
    return main()


if __name__ == "__main__":
    raise SystemExit(main())
