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
from sqlalchemy import func, or_

from app.models.akun_perkiraan import AkunPerkiraan
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

    Return (camelCase — dibaca langsung oleh frontend tanpa response_model):
        {
            "asOf": "2026-09-15T...",
            "overallStatus": "HEALTHY" | "ISSUES_FOUND",
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
                "totalChecks": 11,
                "matchCount": 10,
                "mismatchCount": 1,
                "notConfiguredCount": 0
            }
        }
    """
    if as_of is None:
        # B-04: default ke "sekarang" Jakarta. Jangan pakai local_datetime(utcnow()):
        # utcnow() bukan jam Jakarta, sehingga tanggalnya bisa melenceng satu hari.
        as_of = datetime.now(gl.JAKARTA)
    # B-04: normalisasi sekali ke akhir hari Jakarta agar SEMUA bound turunan
    # (neraca saldo / neraca / aging / saldo GL / GRNI) bersifat inclusive —
    # jurnal dokumen bertanggal hari as_of (instant 00:00 UTC) ikut terhitung.
    as_of = gl.day_end(gl.local_datetime(as_of))

    # Untuk periode-based reconciliation (cash flow, equity), pakai awal tahun sampai as_of
    year = as_of.year
    date_from = datetime(year, 1, 1, tzinfo=as_of.tzinfo)
    date_to = as_of

    reconciliations: List[Dict] = []

    # === 1. Trial Balance Debit = Credit ===
    try:
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
    except Exception as e:
        db.rollback()
        reconciliations.append({
            "name": "Trial Balance",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {e}",
        })

    # === 2. Balance Sheet (Assets = Liabilities + Equity) ===
    try:
        bs = laporan_service.get_neraca(db, as_of)
        bs_selisih = Decimal(str(bs.get("selisih", 0)))
        bs_match = bs_selisih == 0
        reconciliations.append({
            "name": "Balance Sheet",
            "status": "MATCH" if bs_match else "MISMATCH",
            "selisih": str(bs_selisih),
            "detail": f"Aset - Kewajiban - Ekuitas = {bs_selisih}",
        })
    except Exception as e:
        db.rollback()
        reconciliations.append({
            "name": "Balance Sheet",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {e}",
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
    try:
        cf = laporan_service.get_arus_kas(db, date_from, date_to)
        cf_recon_selisih = Decimal(str(cf.get("selisih_rekonsiliasi", 0)))
        cf_recon_match = cf_recon_selisih == 0
        reconciliations.append({
            "name": "Cash Flow Movement",
            "status": "MATCH" if cf_recon_match else "MISMATCH",
            "selisih": str(cf_recon_selisih),
            "detail": f"Opening + Net Change - Ending = {cf_recon_selisih}",
        })
    except Exception as e:
        db.rollback()
        reconciliations.append({
            "name": "Cash Flow Movement",
            "status": "MISMATCH",
            "selisih": "N/A",
            "detail": f"Error: {e}",
        })

    # === Summary ===
    total_checks = len(reconciliations)
    match_count = sum(1 for r in reconciliations if r["status"] == "MATCH")
    mismatch_count = sum(1 for r in reconciliations if r["status"] == "MISMATCH")
    not_configured_count = sum(1 for r in reconciliations if r["status"] == "NOT_CONFIGURED")

    overall_status = "HEALTHY" if mismatch_count == 0 else "ISSUES_FOUND"

    return {
        "asOf": as_of.isoformat(),
        "overallStatus": overall_status,
        "reconciliations": reconciliations,
        "summary": {
            "totalChecks": total_checks,
            "matchCount": match_count,
            "mismatchCount": mismatch_count,
            "notConfiguredCount": not_configured_count,
        },
    }


# ==========================================
# Helpers for individual reconciliation checks
# ==========================================
def _get_ar_ap_reconciliation(db: Session, jenis: str, as_of: datetime) -> Dict:
    """Check AR Aging = AR GL or AP Aging = AP GL.

    AUD2-D3a — dua koreksi agar check tidak lagi menghasilkan MATCH palsu:
    1. Sisi aging memakai key ``total`` per pihak (Σ sisa_tagihan) — key
       ``sisa_tagihan`` hanya ada di rincian per invoice, sehingga pembacaan
       lama selalu 0 (0 = 0 → "MATCH").
    2. Sisi GL mengagregasi akun kontrol + SELURUH subledger per pihak di
       bawahnya (tempat posting aktual menempel), bukan akun kontrol saja.
    """
    try:
        from app.services.aging_service import aging
        aging_report = aging(db, "piutang" if jenis == "AR" else "hutang", as_of)

        # Sum outstanding from aging — per pihak (key 'total' = Σ sisa_tagihan).
        total_outstanding = Decimal("0")
        for item in aging_report.get("items", []):
            total_outstanding += Decimal(str(item.get("total", 0)))

        # GL balance for AR/AP: kontrol + seluruh subledger di bawahnya.
        setting_key = sa_cfg.KEY_PIUTANG_USAHA if jenis == "AR" else sa_cfg.KEY_HUTANG_USAHA
        control_account_id = sa_cfg.get_akun_id(db, setting_key)

        if not control_account_id:
            return {
                "name": f"{jenis} Aging vs GL",
                "status": "NOT_CONFIGURED",
                "selisih": "0.00",
                "detail": f"Setting {setting_key} belum di-configure",
            }

        gl_balance = _get_gl_balance_ar_ap(db, control_account_id, as_of)
        # AR is DEBIT normal → positive; AP is KREDIT normal → positive = kredit - debit
        if jenis == "AR":
            gl_balance_positive = gl_balance  # debit - kredit
        else:
            gl_balance_positive = -gl_balance  # kredit - debit

        selisih = total_outstanding - gl_balance_positive
        match = abs(selisih) < Decimal("0.01")

        detail = (
            f"Aging outstanding: {total_outstanding}, GL (kontrol + subledger): {gl_balance_positive}"
        )
        if not match:
            # Pelunasan yang tercatat SETELAH as_of mengurangi outstanding
            # secara retroaktif (aging memakai tanggal efektif pembayaran; GL
            # memakai tanggal jurnal) — beda waktu, bukan selisih data.
            # Default as-of (hari berjalan ≥ semua jurnal) bebas dari artefak ini.
            detail += (
                ". Periksa jurnal pelunasan bertanggal SETELAH as-of "
                "(reklasifikasi retroaktif) — bila tidak ada, selisih ini perlu "
                "ditelusuri (subledger vs GL)."
            )

        return {
            "name": f"{jenis} Aging vs GL",
            "status": "MATCH" if match else "MISMATCH",
            "selisih": str(selisih),
            "detail": detail,
        }
    except Exception as e:
        # Rollback agar transaksi yang gagal tidak meracuni session
        # untuk reconciliation check berikutnya (InFailedSqlTransaction).
        db.rollback()
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
        db.rollback()
        return {
            "name": "Inventory vs GL",
            "status": "MISMATCH",
            "selisih": "0.00",
            "detail": f"Error: {str(e)}",
        }


def _get_asset_register_reconciliation(db: Session, as_of: datetime) -> Dict:
    """Check Asset Register = FA GL."""
    try:
        from app.services.asset_register_reconciliation_service import (
            get_rekonsiliasi_aset, get_ringkasan_rekonsiliasi_aset)
        recon = get_ringkasan_rekonsiliasi_aset(db, as_of)
        status = recon.get("reconciliationStatus", "MISMATCH")
        # Selisih numerik (jumlah selisihCost + selisihAccum per akun) agar frontend
        # bisa memformat Rp — bukan string non-numerik yang menjadi "Rp NaN".
        try:
            full = get_rekonsiliasi_aset(db, as_of)
            total_selisih = sum(
                (Decimal(str(r.get("selisihCost") or 0)) + Decimal(str(r.get("selisihAccum") or 0))
                 for r in (full.get("perAkun") or [])),
                Decimal("0"),
            )
        except Exception:
            db.rollback()
            total_selisih = Decimal("0")
        return {
            "name": "Fixed Asset vs GL",
            "status": status,
            "selisih": str(total_selisih),
            "detail": f"Akun count: {recon.get('akunCount', 0)}, Total cost: {recon.get('summary', {}).get('totalNilaiPerolehanRegister', 0)}",
        }
    except Exception as e:
        db.rollback()
        return {
            "name": "Fixed Asset vs GL",
            "status": "MISMATCH",
            "selisih": "0.00",
            "detail": f"Error: {str(e)}",
        }


def _get_cash_bank_reconciliation(db: Session, as_of: datetime) -> Dict:
    """Check Cash/Bank Ledger = Cash/Bank GL.

    AUD2-D3b — KasBankAkun.saldo disinkronkan pada saat posting untuk SEMUA
    jurnal POSTED tanpa memandang tanggal jurnal (termasuk jurnal yang
    bertanggal di masa depan), sedangkan GL dibatasi ``as_of``. Membandingkan
    keduanya pada basis tanggal berbeda menghasilkan MISMATCH semu
    (observasi live: 1.710.000). Check kini membandingkan master ledger vs
    saldo GL atas himpunan jurnal yang SAMA (seluruh POSTED) — inilah yang
    benar-benar divalidasi: sinkronisasi ledger kas vs buku besar. Angka
    per ``as_of`` tetap tersedia di buku besar / neraca (basis GL murni).
    """
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

        # KasBankAkun.saldo vs GL balance — basis konsisten: seluruh jurnal POSTED.
        total_master_saldo = Decimal("0")
        for kb_id in cash_account_ids:
            kb = db.query(KasBankAkun).filter_by(akun_perkiraan_id=kb_id).first()
            if kb:
                total_master_saldo += Decimal(str(kb.saldo or 0))

        gl_balance = _get_gl_balance_multi_accounts(db, cash_account_ids, None)

        selisih = total_master_saldo - gl_balance
        match = abs(selisih) < Decimal("0.01")

        gl_as_of = _get_gl_balance_multi_accounts(db, cash_account_ids, as_of)
        return {
            "name": "Cash/Bank vs GL",
            "status": "MATCH" if match else "MISMATCH",
            "selisih": str(selisih),
            "detail": (
                f"Master saldo: {total_master_saldo}, GL (semua jurnal POSTED): {gl_balance}"
                f"; GL per as-of {as_of.date()}: {gl_as_of}"
            ),
        }
    except Exception as e:
        db.rollback()
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
        if recon.get("grniAccountId") is None:
            status = "NOT_CONFIGURED"
        return {
            "name": "GRNI vs GL",
            "status": status,
            "selisih": recon.get("selisih", "0.00"),
            "detail": f"Open GRNI: {recon.get('openGrniValue', 0)}, GL: {recon.get('grniGlBalance', 0)}",
        }
    except Exception as e:
        db.rollback()
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
            "detail": f"CF Ending: {recon.get('cashFlowEnding', 0)}, BS Cash: {recon.get('balanceSheetCash', 0)}",
        }
    except Exception as e:
        db.rollback()
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
            "detail": f"Equity Closing: {recon.get('equityClosing', 0)}, BS Equity: {recon.get('balanceSheetEquity', 0)}",
        }
    except Exception as e:
        db.rollback()
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
    """Get GL balance (debit - kredit) for single account up to as_of.

    ``as_of=None`` berarti tanpa batas tanggal (seluruh jurnal POSTED).
    """
    row = (
        db.query(
            func.coalesce(func.sum(JurnalDetail.debit), 0),
            func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id == akun_id,
            JurnalUmum.status == StatusJurnal.POSTED,
        )
    )
    if as_of is not None:
        row = row.filter(JurnalUmum.tanggal <= as_of)
    row = row.first()
    return Decimal(str(row[0] or 0)) - Decimal(str(row[1] or 0))


def _get_gl_balance_ar_ap(db: Session, control_account_id: UUID, as_of: datetime) -> Decimal:
    """Saldo GL (debit - kredit) akun kontrol AR/AP + seluruh subledger bawahnya.

    Posting piutang/hutang aktual menempel di akun subledger per pihak
    (anak kontrol — induk_id / induk_kode menunjuk kontrol); akun kontrol
    sendiri hanya menerima baris legacy langsung. Mengagregasi keduanya
    mencegah "MATCH palsu 0=0" (AUD2-D3a).
    """
    account_ids = {control_account_id}
    control = db.get(AkunPerkiraan, control_account_id)
    if control is not None:
        children = db.query(AkunPerkiraan.id).filter(
            or_(AkunPerkiraan.induk_id == control_account_id,
                AkunPerkiraan.induk_kode == control.kode)
        ).all()
        account_ids |= {r[0] for r in children}
    return _get_gl_balance_multi_accounts(db, list(account_ids), as_of)


def _get_gl_balance_multi_accounts(db: Session, akun_ids: List[UUID], as_of: Optional[datetime]) -> Decimal:
    """Get GL balance (debit - kredit) for multiple accounts up to as_of.

    ``as_of=None`` berarti tanpa batas tanggal (seluruh jurnal POSTED).
    """
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
        )
    )
    if as_of is not None:
        row = row.filter(JurnalUmum.tanggal <= as_of)
    row = row.first()
    return Decimal(str(row[0] or 0)) - Decimal(str(row[1] or 0))
