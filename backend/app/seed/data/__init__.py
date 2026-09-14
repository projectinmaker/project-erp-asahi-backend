"""Seed data package for ASAHI COA Revisi v2.

Berisi data COA_SYSTEM_MASTER (151 record) dan MIGRATION_MAP (17 items)
yang auto-generated dari workbook COA_ASAHI_FINAL_REVISI_SIAP_UPLOAD_V2.xlsx.

Dipakai oleh:
- app.seed.coa_system_master_seed  (import 151 record COA ke DB)
- app.services.coa_migration_service  (apply 17 migration items)

Re-generate via: scripts/extract_coa_workbook_to_seed.py
"""
