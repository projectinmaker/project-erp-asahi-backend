"""accounting_health_service.py

Aggregate reconciliation status untuk Accounting Health Dashboard
(Master Roadmap §27).

Sesuai Roadmap §27, Accounting Health harus visible dengan status:

    Trial Balance        MATCH
    Balance Sheet        MATCH
    AR vs GL             MATCH
    AP vs GL             MATCH
    Inventory vs GL      MATCH
    Fixed Asset vs GL    MATCH
    Cash/Bank vs GL      MATCH
    GRNI vs GL           MATCH

Service ini aggregate semua reconciliation status ke dalam satu response
supaya frontend bisa tampilkan di dashboard dengan satu API call.

Dipakai oleh:
- Endpoint GET /laporan/accounting-health
- Hypercare daily reconciliation (Roadmap §68)
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Dict, List
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail
from app.services import setting_akun_service as sa_cfg
from app.services import laporan_service
from app.services import reporting_ledger as gl
from app.services.financial_reconciliation_service import (
    get_grni_reconciliation,
    get_cash_flow_vs_balance_sheet_reconciliation,
    get_equity_vs_balance_sheet_reconciliation,
)


def get_accounting_health(
    db: Session,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Aggregate semua reconciliation status untuk Accounting Health Dashboard.

    Parameter:
        db: SQLAlchemy Session
        as_of: Tanggal as-of untuk reconciliation (default: now)

    Return:
        {
            "as_of": "2026-09-15T...",
            "overall_status": "HEALTHY" | "ISSUES_FOUND",
            "reconciliations": [
                {
                    "name": "Trial Balance",
                    "status": "MATCH" | "MISMATCH" | "NOT_CONFIGURED",
                    "selisih": "0.00",
                    "detail": "Debit = Kredit"
                },
                ...
            ],
            "summary": {
                "total_checks": 11,
                "match_count": 10,
                "mismatch_count": 1,
                "not_configured_count": 0
            }
        }
    """
    if as_of is None:
        from app.services.reporting_ledger import local_datetime
        as_of = local_datetime(datetime.utcnow())

    # Untuk periode-based reconciliation (cash flow, equity), pakai awal tahun sampai as_of
    year = as_of.year
    date_from = datetime(year, 1, 1, tzinfo=as_of.tzinfo)
    date_to = as_of

    reconciliations: List[Dict] = []

    # === 1. Trial Balance Debit = Credit ===
    tb = laporan_service.get_neraca_saldo(db, date_from, date_to)
    tb_selisih = Decimal(str(tb.get("selisih", 0)))
    tb_saldo_selisih = Decimal(str(tb.get("total_saldo_debit", 0))) - Decimal(str(tb.get("total_saldo_kredit", 0)))
    tb_match = tb_selisih == 0 and tb_saldo_selisih == 0
    reconciliations.append({
        "name": "Trial Balance",
        "status": "MATCH" if tb_match else "MISMATCH",
        "selisih": str(tb_selisih),
        "detail": f"Mutasi selisih: {tb_selisih}, Saldo selisih: {tb_saldo_selisih}",
    })

    # === 2. Balance Sheet (Assets = Liabilities + Equity) ===
    bs = laporan_service.get_neraca(db, as_of)
    bs_selisih = Decimal(str(bs.get("selisih", 0)))
    bs_match = bs_selisih == 0
    reconciliations.append({
        "name": "Balance Sheet",
        "status": "MATCH" if bs_match else "MISMATCH",
        "selisih": str(bs_selisih),
        "detail": f"Aset - Kewajiban - Ekuitas = {bs_selisih}",
    })

    # === 3. AR Aging = AR GL ===
    ar_status = _get_ar_ap_reconciliation(db, "AR", as_of)
    reconciliations.append(ar_status)

    # === 4. AP Aging = AP GL ===
    ap_status = _get_ar_ap_reconciliation(db, "AP", as_of)
    reconciliations.append(ap_status)

    # === 5. Inventory Valuation = Inventory GL ===
    inv_status = _get_inventory_reconciliation(db, as_of)
    reconciliations.append(inv_status)

    # === 6. Fixed Asset Register = FA GL ===
    asset_status = _get_asset_register_reconciliation(db, as_of)
    reconciliations.append(asset_status)

    # === 7. Cash/Bank Ledger = Cash/Bank GL ===
    cash_status = _get_cash_bank_reconciliation(db, as_of)
    reconciliations.append(cash_status)

    # === 8. GRNI = GRNI GL ===
    grni_status = _get_grni_status(db, as_of)
    reconciliations.append(grni_status)

    # === 9. Cash Flow Ending = Balance Sheet Cash ===
    cf_status = _get_cashflow_vs_bs_status(db, date_from, date_to)
    reconciliations.append(cf_status)

    # === 10. Changes in Equity Closing = Balance Sheet Equity ===
    eq_status = _get_equity_vs_bs_status(db, date_from, date_to)
    reconciliations.append(eq_status)

    # === 11. Cash Flow Reconciliation (opening + change - ending = 0) ===
    cf = laporan_service.get_arus_kas(db, date_from, date_to)
    cf_recon_selisih = Decimal(str(cf.get("selisih_rekonsiliasi", 0)))
    cf_recon_match = cf_recon_selisih == 0
    reconciliations.append({
        "name": "Cash Flow Movement",
        "status": "MATCH" if cf_recon_match else "MISMATCH",
        "selisih": str(cf_recon_selisih),
        "detail": f"Opening + Net Change - Ending = {cf_recon_selisih}",
    })

    # === Summary ===
    total_checks = len(reconciliations)
    match_count = sum(1 for r in reconciliations if r["status"] == "MATCH")
    mismatch_count = sum(1 for r in reconciliations if r["status"] == "MISMATCH")
    not_configured_count = sum(1 for r in reconciliations if r["status"] == "NOT_CONFIGURED")

    overall_status = "HEALTHY" if mismatch_count == 0 else "ISSUES_FOUND"

    return {
        "as_of": as_of.isoformat(),
        "overall_status": overall_status,
        "reconciliations": reconciliations,
        "summary": {
            "total_checks": total_checks,
            "match_count": match_count,
            "mismatch_count": mismatch_count,
            "not_configured_count": not_configured_count,
        },
    }


# ==========================================
# Helpers for individual reconciliation checks
# ==========================================
def _get_ar_ap_reconciliation(db: Session, jenis: str, as_of: datetime) -> Dict:
    """Check AR Aging = AR GL or AP Aging = AP GL."""
    try:
        from app.services.aging_service import aging
        aging_report = aging(db, "piutang" if jenis == "AR" else "hutang", as_of)

        # Sum outstanding from aging
        total_outstanding = Decimal("0")
        for item in aging_report.get("items", []):
            total_outstanding += Decimal(str(item.get("sisa_tagihan", 0)))

        # GL balance for AR/AP control accounts
        setting_key = sa_cfg.KEY_PIUTANG_USAHA if jenis == "AR" else sa_cfg.KEY_HUTANG_USAHA
        control_account_id = sa_cfg.get_akun_id(db, setting_key)

        if not control_account_id:
            return {
                "name": f"{jenis} Aging vs GL",
                "status": "NOT_CONFIGURED",
                "selisih": "0.00",
                "detail": f"Setting {setting_key} belum di-configure",
            }

        gl_balance = _get_gl_balance_for_account(db, control_account_id, as_of)
        # AR is DEBIT normal → positive; AP is KREDIT normal → positive = kredit - debit
        if jenis == "AR":
            gl_balance_positive = gl_balance  # debit - kredit
        else:
            gl_balance_positive = -gl_balance  # kredit - debit

        selisih = total_outstanding - gl_balance_positive
        match = abs(selisih) < Decimal("0.01")

        return {
            "name": f"{jenis} Aging vs GL",
            "status": "MATCH" if match else "MISMATCH",
            "selisih": str(selisih),
            "detail": f"Aging outstanding: {total_outstanding}, GL balance: {gl_balance_positive}",
        }
    except Exception as e:
        return {
            "name": f"{jenis} Aging vs GL",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {str(e)}",
        }


def _get_inventory_reconciliation(db: Session, as_of: datetime) -> Dict:
    """Check Inventory Valuation = Inventory GL."""
    try:
        from app.services.rekonsiliasi_persediaan_service import get_ringkasan_rekonsiliasi
        recon = get_ringkasan_rekonsiliasi(db, as_of=as_of)
        ringkasan = recon.get("ringkasan", {})
        selisih = Decimal(str(ringkasan.get("selisih_nilai", 0)))
        match = selisih == 0
        return {
            "name": "Inventory vs GL",
            "status": "MATCH" if match else "MISMATCH",
            "selisih": str(selisih),
            "detail": f"Stock value: {ringkasan.get('total_nilai_stok', 0)}, GL: {ringkasan.get('total_saldo_gl', 0)}",
        }
    except Exception as e:
        return {
            "name": "Inventory vs GL",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {str(e)}",
        }


def _get_asset_register_reconciliation(db: Session, as_of: datetime) -> Dict:
    """Check Asset Register = FA GL."""
    try:
        from app.services.asset_register_reconciliation_service import get_ringkasan_rekonsiliasi_aset
        recon = get_ringkasan_rekonsiliasi_aset(db, as_of)
        status = recon.get("reconciliation_status", "MISMATCH")
        return {
            "name": "Fixed Asset vs GL",
            "status": status,
            "selisih": "See detail",
            "detail": f"Akun count: {recon.get('akun_count', 0)}, Total cost: {recon.get('summary', {}).get('total_nilai_perolehan_register', 0)}",
        }
    except Exception as e:
        return {
            "name": "Fixed Asset vs GL",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {str(e)}",
        }


def _get_cash_bank_reconciliation(db: Session, as_of: datetime) -> Dict:
    """Check Cash/Bank Ledger = Cash/Bank GL."""
    try:
        from app.models.master.kas_bank_akun import KasBankAkun
        cash_account_ids = [r[0] for r in db.query(KasBankAkun.akun_perkiraan_id).all() if r[0]]

        if not cash_account_ids:
            return {
                "name": "Cash/Bank vs GL",
                "status": "NOT_CONFIGURED",
                "selisih": "0.00",
                "detail": "Tidak ada akun kas/bank di master",
            }

        # KasBankAkun.saldo vs GL balance
        total_master_saldo = Decimal("0")
        for kb_id in cash_account_ids:
            kb = db.query(KasBankAkun).filter_by(akun_perkiraan_id=kb_id).first()
            if kb:
                total_master_saldo += Decimal(str(kb.saldo or 0))

        gl_balance = _get_gl_balance_multi_accounts(db, cash_account_ids, as_of)

        selisih = total_master_saldo - gl_balance
        match = abs(selisih) < Decimal("0.01")

        return {
            "name": "Cash/Bank vs GL",
            "status": "MATCH" if match else "MISMATCH",
            "selisih": str(selisih),
            "detail": f"Master saldo: {total_master_saldo}, GL: {gl_balance}",
        }
    except Exception as e:
        return {
            "name": "Cash/Bank vs GL",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {str(e)}",
        }


def _get_grni_status(db: Session, as_of: datetime) -> Dict:
    """Check Open GRNI = GRNI GL."""
    try:
        recon = get_grni_reconciliation(db, as_of)
        match = recon.get("match", False)
        status = "MATCH" if match else "MISMATCH"
        if recon.get("grni_account_id") is None:
            status = "NOT_CONFIGURED"
        return {
            "name": "GRNI vs GL",
            "status": status,
            "selisih": recon.get("selisih", "0.00"),
            "detail": f"Open GRNI: {recon.get('open_grni_value', 0)}, GL: {recon.get('grni_gl_balance', 0)}",
        }
    except Exception as e:
        return {
            "name": "GRNI vs GL",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {str(e)}",
        }


def _get_cashflow_vs_bs_status(db: Session, date_from: datetime, date_to: datetime) -> Dict:
    """Check Cash Flow Ending = Balance Sheet Cash."""
    try:
        recon = get_cash_flow_vs_balance_sheet_reconciliation(db, date_from, date_to)
        match = recon.get("match", False)
        return {
            "name": "Cash Flow Ending vs BS Cash",
            "status": "MATCH" if match else "MISMATCH",
            "selisih": recon.get("selisih", "0.00"),
            "detail": f"CF Ending: {recon.get('cash_flow_ending', 0)}, BS Cash: {recon.get('balance_sheet_cash', 0)}",
        }
    except Exception as e:
        return {
            "name": "Cash Flow Ending vs BS Cash",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {str(e)}",
        }


def _get_equity_vs_bs_status(db: Session, date_from: datetime, date_to: datetime) -> Dict:
    """Check Changes in Equity Closing = Balance Sheet Equity."""
    try:
        recon = get_equity_vs_balance_sheet_reconciliation(db, date_from, date_to)
        match = recon.get("match", False)
        return {
            "name": "Equity Closing vs BS Equity",
            "status": "MATCH" if match else "MISMATCH",
            "selisih": recon.get("selisih", "0.00"),
            "detail": f"Equity Closing: {recon.get('equity_closing', 0)}, BS Equity: {recon.get('balance_sheet_equity', 0)}",
        }
    except Exception as e:
        return {
            "name": "Equity Closing vs BS Equity",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {str(e)}",
        }


# ==========================================
# GL balance helpers
# ==========================================
def _get_gl_balance_for_account(db: Session, akun_id: UUID, as_of: datetime) -> Decimal:
    """Get GL balance (debit - kredit) for single account up to as_of."""
    row = (
        db.query(
            func.coalesce(func.sum(JurnalDetail.debit), 0),
            func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id == akun_id,
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal <= as_of,
        )
        .first()
    )
    return Decimal(str(row[0] or 0)) - Decimal(str(row[1] or 0))


def _get_gl_balance_multi_accounts(db: Session, akun_ids: List[UUID], as_of: datetime) -> Decimal:
    """Get GL balance (debit - kredit) for multiple accounts up to as_of."""
    if not akun_ids:
        return Decimal("0")
    row = (
        db.query(
            func.coalesce(func.sum(JurnalDetail.debit), 0),
            func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id.in_(akun_ids),
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal <= as_of,
        )
        .first()
    )
    return Decimal(str(row[0] or 0)) - Decimal(str(row[1] or 0))
