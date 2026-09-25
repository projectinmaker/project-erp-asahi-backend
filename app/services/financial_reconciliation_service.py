"""financial_reconciliation_service.py

Service untuk reconciliation financial statements (Master Roadmap §26).

3 reconciliation functions:
1. get_grni_reconciliation() — Open GRNI = GRNI GL
   Compare sum of open PenerimaanBarang (received but not yet invoiced) vs
   GL balance of PENERIMAAN_DALAM_PROSES account.

2. get_cash_flow_vs_balance_sheet_reconciliation() — Cash Flow Ending = BS Cash
   Compare cash flow report's ending cash vs balance sheet's cash total.

3. get_equity_vs_balance_sheet_reconciliation() — Changes in Equity Closing = BS Equity
   Compare changes in equity report's closing equity vs balance sheet's equity total.

Dipakai oleh:
- Endpoint GET /laporan/rekonsiliasi/grni
- Endpoint GET /laporan/rekonsiliasi/cashflow-vs-bs
- Endpoint GET /laporan/rekonsiliasi/equity-vs-bs
- Accounting Health aggregate (accounting_health_service)
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Dict
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail
from app.models.akun_perkiraan import AkunPerkiraan
from app.services import setting_akun_service as sa_cfg
from app.services import laporan_service
from app.services import reporting_ledger as gl


# ==========================================
# 1. GRNI Reconciliation (Roadmap §26: "Open GRNI = GRNI GL")
# ==========================================
def get_grni_reconciliation(
    db: Session,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Rekonsiliasi Open GRNI vs GRNI GL account balance.

    Logic:
    - Open GRNI = sum of PenerimaanBarangDetail.qty * harga_perolehan
      for PenerimaanBarang with status SELESAI and no linked PurchaseInvoice
      (received but not yet invoiced)
    - GRNI GL = sum(debit - kredit) of PENERIMAAN_DALAM_PROSES account
      (from POSTED journals up to as_of)

    Sesuai Roadmap §26:
        "Open GRNI = GRNI GL" (Difference = 0)

    Return (camelCase — dibaca langsung oleh frontend tanpa response_model):
        {
            "asOf": "...",
            "grniAccountId": "uuid",
            "grniAccountKode": "...",
            "grniAccountNama": "...",
            "openGrniValue": "12345.00",       # dari PenerimaanBarang
            "grniGlBalance": "12345.00",       # dari GL
            "selisih": "0.00",
            "match": true | false,
            "openReceiptsCount": 5,
            "catatan": "..."
        }
    """
    from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
    from app.models.detail.penerimaan_barang_detail import PenerimaanBarangDetail
    from app.models.transaksi.penjualan.sales_order import StatusPenjualan

    # Get GRNI account from settings
    grni_account_id = sa_cfg.get_akun_id(db, sa_cfg.KEY_PENERIMAAN_DALAM_PROSES)
    if not grni_account_id:
        return {
            "asOf": as_of.isoformat() if as_of else None,
            "grniAccountId": None,
            "grniAccountKode": "-",
            "grniAccountNama": "PENERIMAAN_DALAM_PROSES not configured",
            "openGrniValue": "0.00",
            "grniGlBalance": "0.00",
            "selisih": "0.00",
            "match": True,
            "openReceiptsCount": 0,
            "catatan": (
                "Akun PENERIMAAN_DALAM_PROSES belum di-configure di Setting Akun. "
                "Reconciliation GRNI tidak bisa dijalankan. Configure via PUT /master/setting-akun/PENERIMAAN_DALAM_PROSES."
            ),
        }

    grni_account = db.get(AkunPerkiraan, grni_account_id)

    # === 1. Hitung Open GRNI dari PenerimaanBarang ===
    # Open GRNI = PenerimaanBarang yang status SELESAI (received)
    # dan belum di-link ke PurchaseInvoice (purchase_invoice_id IS NULL)
    # ATAU linked ke invoice yang belum POSTED
    open_receipts_query = (
        db.query(PenerimaanBarang)
        .filter(PenerimaanBarang.status == StatusPenjualan.SELESAI)
    )
    if as_of is not None:
        open_receipts_query = open_receipts_query.filter(PenerimaanBarang.tanggal <= as_of)

    open_receipts = open_receipts_query.all()

    open_grni_value = Decimal("0")
    open_count = 0
    for receipt in open_receipts:
        # Cek apakah sudah ada PurchaseInvoice POSTED yang memakai receipt ini
        has_posted_invoice = False
        if receipt.purchase_invoice_id:
            inv = db.query(PurchaseInvoice).filter_by(
                id=receipt.purchase_invoice_id
            ).first() if False else None  # avoid circular import — use simpler check
            # Simplified: cek via purchase_invoice_id field
            from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
            inv = db.get(PurchaseInvoice, receipt.purchase_invoice_id)
            if inv and inv.status in (StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI):
                has_posted_invoice = True

        if not has_posted_invoice:
            # This receipt is "open" — belum di-invoice atau invoice belum posted
            for detail in receipt.details:
                harga = Decimal(str(detail.harga_perolehan or 0))
                open_grni_value += harga * detail.qty
            open_count += 1

    # === 2. Hitung GRNI GL Balance ===
    # GRNI account is liability (KREDIT normal) → saldo = kredit - debit
    query = (
        db.query(
            func.coalesce(func.sum(JurnalDetail.debit), 0),
            func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id == grni_account_id,
            JurnalUmum.status == StatusJurnal.POSTED,
        )
    )
    if as_of is not None:
        query = query.filter(JurnalUmum.tanggal <= as_of)

    row = query.first()
    total_debit = Decimal(str(row[0] or 0))
    total_kredit = Decimal(str(row[1] or 0))
    # GRNI is KREDIT normal → positive balance = kredit > debit
    grni_gl_balance = total_kredit - total_debit

    selisih = open_grni_value - grni_gl_balance
    match = abs(selisih) < Decimal("0.01")  # tolerance 1 cent

    return {
        "asOf": as_of.isoformat() if as_of else None,
        "grniAccountId": str(grni_account_id),
        "grniAccountKode": grni_account.kode if grni_account else "-",
        "grniAccountNama": grni_account.nama if grni_account else "-",
        "openGrniValue": str(open_grni_value),
        "grniGlBalance": str(grni_gl_balance),
        "selisih": str(selisih),
        "match": match,
        "openReceiptsCount": open_count,
        "catatan": (
            "Open GRNI = penerimaan barang SELESAI yang belum di-invoice "
            "(atau invoice belum POSTED). GRNI GL = saldo akun PENERIMAAN_DALAM_PROSES. "
            "Selisih dapat terjadi jika: (1) penerimaan tanpa GRNI account configured, "
            "(2) jurnal manual langsung ke akun GRNI, (3) invoice posted tapi receipt "
            "belum di-link. Tidak ada koreksi otomatis."
        ),
    }


# ==========================================
# 2. Cash Flow Ending vs Balance Sheet Cash
# ==========================================
def get_cash_flow_vs_balance_sheet_reconciliation(
    db: Session,
    date_from: datetime,
    date_to: datetime,
) -> Dict:
    """Rekonsiliasi Cash Flow Ending Cash vs Balance Sheet Cash.

    Sesuai Roadmap §26:
        "Cash Flow Ending Cash = Balance Sheet Cash" (Difference = 0)

    Logic:
    - Cash Flow Ending = saldo_akhir dari get_arus_kas()
    - Balance Sheet Cash = total akun Kas/Bank di neraca per tanggal_akhir
    - Selisih harus 0

    Return (camelCase — dibaca langsung oleh frontend tanpa response_model):
        {
            "periode": {"dateFrom": "...", "dateTo": "..."},
            "cashFlowEnding": "12345.00",
            "balanceSheetCash": "12345.00",
            "selisih": "0.00",
            "match": true | false,
            "catatan": "..."
        }
    """
    # Cash Flow report
    cash_flow = laporan_service.get_arus_kas(db, date_from, date_to)
    cash_flow_ending = Decimal(str(cash_flow.get("saldo_akhir", 0)))

    # Balance Sheet cash = total of all KasBankAkun GL balances as of date_to
    from app.models.master.kas_bank_akun import KasBankAkun
    cash_account_ids = [
        r[0] for r in db.query(KasBankAkun.akun_perkiraan_id).all() if r[0]
    ]

    balance_sheet_cash = Decimal("0")
    if cash_account_ids:
        query = (
            db.query(
                func.coalesce(func.sum(JurnalDetail.debit), 0),
                func.coalesce(func.sum(JurnalDetail.kredit), 0),
            )
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                JurnalDetail.akun_perkiraan_id.in_(cash_account_ids),
                JurnalUmum.status == StatusJurnal.POSTED,
                JurnalUmum.tanggal <= date_to,
            )
        )
        row = query.first()
        total_debit = Decimal(str(row[0] or 0))
        total_kredit = Decimal(str(row[1] or 0))
        # Cash is DEBIT normal → balance = debit - kredit
        balance_sheet_cash = total_debit - total_kredit

    selisih = cash_flow_ending - balance_sheet_cash
    match = abs(selisih) < Decimal("0.01")

    return {
        "periode": {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        },
        "cashFlowEnding": str(cash_flow_ending),
        "balanceSheetCash": str(balance_sheet_cash),
        "selisih": str(selisih),
        "match": match,
        "catatan": (
            "Cash Flow Ending = saldo akhir kas/bank dari laporan arus kas. "
            "Balance Sheet Cash = total saldo akun kas/bank di neraca per tanggal akhir. "
            "Selisih dapat terjadi jika: (1) ada akun kas/bank baru yang belum ada "
            "di KasBankAkun master, (2) jurnal manual langsung ke akun kas/bank "
            "tanpa via modul Kas/Bank. Tidak ada koreksi otomatis."
        ),
    }


# ==========================================
# 3. Changes in Equity vs Balance Sheet Equity
# ==========================================
def get_equity_vs_balance_sheet_reconciliation(
    db: Session,
    date_from: datetime,
    date_to: datetime,
) -> Dict:
    """Rekonsiliasi Changes in Equity Closing vs Balance Sheet Equity.

    Sesuai Roadmap §26:
        "Changes in Equity Closing = Balance Sheet Equity" (Difference = 0)

    Logic:
    - Changes in Equity Closing = total_modal_akhir dari get_perubahan_modal()
    - Balance Sheet Equity = total ekuitas di neraca per tanggal_akhir
    - Selisih harus 0

    Return (camelCase — dibaca langsung oleh frontend tanpa response_model):
        {
            "periode": {...},
            "equityClosing": "12345.00",
            "balanceSheetEquity": "12345.00",
            "selisih": "0.00",
            "match": true | false,
            "catatan": "..."
        }
    """
    # Changes in Equity report
    equity_report = laporan_service.get_perubahan_modal(db, date_from, date_to)
    equity_closing = Decimal(str(equity_report.get("total_modal_akhir", 0)))

    # Balance Sheet equity total
    balance_sheet = laporan_service.get_neraca(db, date_to)
    balance_sheet_equity = Decimal(str(balance_sheet.get("total_ekuitas", 0)))

    selisih = equity_closing - balance_sheet_equity
    match = abs(selisih) < Decimal("0.01")

    return {
        "periode": {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        },
        "equityClosing": str(equity_closing),
        "balanceSheetEquity": str(balance_sheet_equity),
        "selisih": str(selisih),
        "match": match,
        "catatan": (
            "Changes in Equity Closing = total modal akhir dari laporan perubahan modal. "
            "Balance Sheet Equity = total ekuitas di neraca per tanggal akhir. "
            "Selisih dapat terjadi jika: (1) ada akun modal yang belum di-include "
            "di perubahan modal, (2) laba/rugi belum ditutup (unclosed profit) "
            "dihitung beda di kedua report. Tidak ada koreksi otomatis."
        ),
    }
