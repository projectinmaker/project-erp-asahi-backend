"""Compatibility entrypoint for the non-destructive COA revision importer.

Default: preview. For explicit apply, follow COA_IMPORT_RUNBOOK.md.
"""
from app.seed.coa_revision_import import main


def seed_coa_system_master():
    return main()


if __name__ == "__main__":
    raise SystemExit(main())
