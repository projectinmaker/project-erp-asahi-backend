from datetime import datetime
from uuid import UUID
from decimal import Decimal
from typing import Optional, List

from app.models.akun_perkiraan import HeaderCOA, SaldoNormal, TingkatAkun
from app.schemas.base import BaseSchema


# ==========================================
# Constants — subledger_type & system_account_type yang dikenal sistem.
# Dipakai untuk validasi di schema + dokumentasi FE.
# ==========================================
KNOWN_SUBLEDGER_TYPES = {"AR", "AP", "INVENTORY", "BANK_TRANSFER"}
KNOWN_SYSTEM_ACCOUNT_TYPES = {
    "AR_CONTROL", "AP_CONTROL", "BANK_CLEARING", "CURRENT_EARNINGS",
    "RETAINED_EARNINGS", "COGS_FINISHED_GOODS", "INVENTORY_RAW",
    "INVENTORY_AUX", "INVENTORY_WIP", "INVENTORY_FINISHED",
    "LEGACY_COGS_PURCHASE", "DIVIDEND", "CASH_BANK", "FIXED_ASSET",
    "ACCUM_DEPR", "DEPRECIATION_EXPENSE", "VAT_INPUT", "VAT_OUTPUT",
    "SALES", "OTHER_INCOME", "OTHER_EXPENSE", "SELLING_EXPENSE",
    "ADMIN_EXPENSE", "FACTORY_OVERHEAD", "INCOME_TAX",
    "CORPORATE_INCOME_TAX", "OTHER_AR",
}

KNOWN_ACCOUNT_CLASSES = {"ASSET", "LIABILITY", "EQUITY", "REVENUE", "COGS", "EXPENSE"}
KNOWN_FINANCIAL_STATEMENTS = {"NERACA", "LABA RUGI"}


class COABase(BaseSchema):
    """Field wajib untuk create/update COA (legacy + new fields optional)."""
    kode: str
    nama: str
    header: HeaderCOA
    tingkat: TingkatAkun
    induk_id: Optional[UUID] = None
    induk_kode: Optional[str] = None
    saldo_normal: SaldoNormal
    status: str = "AKTIF"


class COACreate(COABase):
    """Schema untuk membuat COA baru.

    Field tambahan (opsional):
    - jenis_kas_bank: Jika COA detail di bawah AKTIVA (Kas dan Setara Kas),
      isi 'KAS' atau 'BANK' untuk auto-membuat KasBankAkun.
    - saldo: Saldo awal akun (default 0).
    - tanggal: Tanggal mulai aktif / penempatan saldo awal.

    Field baru (ASAHI COA Revisi v2):
    - account_class: ASSET/LIABILITY/EQUITY/REVENUE/COGS/EXPENSE.
      Jika None, akan di-derive dari `header` (AKTIVA->ASSET, dst).
    - account_subclass: CASH_BANK/ACCOUNTS_RECEIVABLE/INVENTORY_*/...
    - allow_system_posting: default True ( False = lock untuk auto-posting)
    - allow_manual_posting: default True (False = lock untuk jurnal manual)
    - is_control_account: default False (True = control account, posting
      manual dibatasi)
    - subledger_type: AR/AP/INVENTORY/BANK_TRANSFER
    - financial_statement: NERACA/LABA RUGI
    - report_group: CURRENT_ASSET/COGS/...
    - system_account_type: AR_CONTROL/AP_CONTROL/BANK_CLEARING/CURRENT_EARNINGS/...
    - reconciliation_required: default False
    - active: default True (False = akun di-hide dari transaksi baru, tapi
      tetap terbaca di laporan historis)
    """
    jenis_kas_bank: Optional[str] = None  # 'KAS' atau 'BANK'
    saldo: Decimal = Decimal("0")
    tanggal: Optional[datetime] = None

    # === New fields — ASAHI COA Revisi v2 ===
    account_class: Optional[str] = None
    account_subclass: Optional[str] = None
    allow_system_posting: Optional[bool] = None
    allow_manual_posting: Optional[bool] = None
    is_control_account: Optional[bool] = None
    subledger_type: Optional[str] = None
    financial_statement: Optional[str] = None
    report_group: Optional[str] = None
    system_account_type: Optional[str] = None
    reconciliation_required: Optional[bool] = None
    active: Optional[bool] = None


class COAUpdate(BaseSchema):
    """Schema untuk update COA (semua field opsional)."""

    nama: Optional[str] = None
    induk_id: Optional[UUID] = None
    induk_kode: Optional[str] = None
    saldo: Optional[Decimal] = None
    tanggal: Optional[datetime] = None
    status: Optional[str] = None

    # === New fields — ASAHI COA Revisi v2 ===
    account_class: Optional[str] = None
    account_subclass: Optional[str] = None
    allow_system_posting: Optional[bool] = None
    allow_manual_posting: Optional[bool] = None
    is_control_account: Optional[bool] = None
    subledger_type: Optional[str] = None
    financial_statement: Optional[str] = None
    report_group: Optional[str] = None
    system_account_type: Optional[str] = None
    reconciliation_required: Optional[bool] = None
    active: Optional[bool] = None


# --- Saldo Awal ---
class SaldoAwalItem(BaseSchema):
    akun_perkiraan_id: UUID
    kode_akun: str
    nama_akun: str
    saldo_normal: str  # "DEBIT" / "KREDIT"
    debit: Decimal = Decimal("0")
    kredit: Decimal = Decimal("0")


class SaldoAwalRequest(BaseSchema):
    tanggal: str  # YYYY-MM-DD
    items: List[SaldoAwalItem]


class NextKodeResponse(BaseSchema):
    kode: str


class SaldoAwalResponse(BaseSchema):
    sudah_diset: bool
    tanggal: Optional[str] = None
    items: List[SaldoAwalItem] = []
    total_debit: Decimal = Decimal("0")
    total_kredit: Decimal = Decimal("0")
    selisih: Decimal = Decimal("0")


class COAResponse(COABase):
    """Schema untuk response API ke Frontend.

    Field baru (ASAHI COA Revisi v2) — frontend WAJIB adaptasi:
    - account_class, account_subclass, financial_statement, report_group
    - allow_system_posting, allow_manual_posting, is_control_account
    - subledger_type, system_account_type, reconciliation_required
    - active (canonical), is_legacy_locked (derived), is_postable_for_manual
      (derived), is_postable_for_system (derived), is_system_account (derived)

    Catatan: field `active` menggantikan `status` sebagai canonical. Field
    `status` tetap dikembalikan untuk backward compatibility, tapi frontend
    sebaiknya migrasi ke `active`.
    """

    id: UUID
    saldo: Decimal
    tanggal: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    # Opsi kas/bank info (hanya diisi jika COA ini punya relasi KasBankAkun)
    jenis_kas_bank: Optional[str] = None
    is_subledger: bool = False

    # === New fields — ASAHI COA Revisi v2 ===
    account_class: Optional[str] = None
    account_subclass: Optional[str] = None
    allow_system_posting: bool = True
    allow_manual_posting: bool = True
    is_control_account: bool = False
    subledger_type: Optional[str] = None
    financial_statement: Optional[str] = None
    report_group: Optional[str] = None
    system_account_type: Optional[str] = None
    reconciliation_required: bool = False
    active: bool = True

    # === Derived helper flags (read-only) ===
    is_postable_for_manual: bool = False
    is_postable_for_system: bool = False
    is_legacy_locked: bool = False
    is_system_account: bool = False

    class Config:
        json_schema_extra = {
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "kode": "112000",
                "nama": "Piutang Usaha",
                "header": "AKTIVA",
                "tingkat": "DETAIL",
                "indukId": "ec35cd2a-0bc9-4dd7-bded-c2930e895c41",
                "indukKode": "110000",
                "saldoNormal": "DEBIT",
                "saldo": 5000000,
                "tanggal": "2026-01-01T00:00:00",
                "status": "AKTIF",
                "jenisKasBank": None,
                "accountClass": "ASSET",
                "accountSubclass": "ACCOUNTS_RECEIVABLE",
                "allowSystemPosting": True,
                "allowManualPosting": False,
                "isControlAccount": True,
                "subledgerType": "AR",
                "financialStatement": "NERACA",
                "reportGroup": "CURRENT_ASSET",
                "systemAccountType": "AR_CONTROL",
                "reconciliationRequired": True,
                "active": True,
                "isPostableForManual": False,
                "isPostableForSystem": True,
                "isLegacyLocked": False,
                "isSystemAccount": False,
                "createdAt": "2026-08-11T16:00:00.000000",
                "updatedAt": "2026-08-11T16:00:00.000000",
            }
        }


# ==========================================
# Migration runner request/response
# ==========================================
class MigrationApplyRequest(BaseSchema):
    """Trigger apply migration map (sheet MIGRATION_MAP dari workbook).

    - dry_run=True: hanya return preview tanpa menulis DB.
    - action_filter: list action yang mau di-apply saja
      (INSERT / UPDATE_CONTROL_RULE / UPDATE_NAME_NOTE / UPDATE_LOCK_LEGACY).
      Default = apply semua.
    """
    dry_run: bool = False
    action_filter: Optional[List[str]] = None


class MigrationItemResult(BaseSchema):
    action: str
    account_code: str
    old_name: Optional[str] = None
    new_name: Optional[str] = None
    applied: bool = False
    message: str = ""


class MigrationApplyResponse(BaseSchema):
    dry_run: bool
    total_items: int
    applied_count: int
    skipped_count: int
    results: List[MigrationItemResult] = []
