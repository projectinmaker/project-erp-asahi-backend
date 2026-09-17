"""dashboard_service.py

Service untuk dashboard widgets Phase 10 (Master Roadmap §27).

3 widget baru:
1. get_inventory_value_widget() — Inventory Value dari SUM StockBalance.nilai
2. get_low_stock_widget() — Low stock count dari backend full dataset
3. get_accounting_health_summary() — Quick accounting health status (from Phase 9)

Dipakai oleh:
- Endpoint GET /dashboard/summary (updated untuk include 3 widget baru)
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Dict
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.transaksi.stock_balance import StockBalance
from app.models.master.barang import Barang
from app.models.master.gudang import Gudang


# ==========================================
# 1. Inventory Value Widget (Roadmap §27: "Inventory Value dari backend SUM StockBalance.nilai")
# ==========================================
def get_inventory_value_widget(
    db: Session,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Hitung total nilai persediaan dari StockBalance.

    Sesuai Roadmap §27:
        "Inventory Value dari backend SUM StockBalance.nilai"
        "Jangan menghitung Inventory Value dari paginated frontend rows"

    Logic:
    - SUM(StockBalance.nilai) untuk semua barang yang punya StockBalance
    - Juga return count of unique barang with stock > 0
    - Juga return total qty (SUM StockBalance.qty)

    Return:
        {
            "total_nilai": "12345678.00",
            "total_qty": 500,
            "barang_count": 25,
            "as_of": "2026-09-15T..."
        }
    """
    # SUM nilai dari StockBalance (backend full dataset, bukan paginated)
    total_nilai = (
        db.query(func.coalesce(func.sum(StockBalance.nilai), 0))
        .scalar()
    )
    total_nilai = Decimal(str(total_nilai or 0))

    # SUM qty
    total_qty = (
        db.query(func.coalesce(func.sum(StockBalance.qty), 0))
        .scalar()
    )
    total_qty = int(total_qty or 0)

    # Count unique barang with stock > 0
    barang_count = (
        db.query(func.count(func.distinct(StockBalance.barang_id)))
        .filter(StockBalance.qty > 0)
        .scalar()
    )
    barang_count = int(barang_count or 0)

    return {
        "total_nilai": str(total_nilai),
        "total_qty": total_qty,
        "barang_count": barang_count,
        "as_of": as_of.isoformat() if as_of else None,
    }


# ==========================================
# 2. Low Stock Widget (Roadmap §27: "Low Stock count backend full dataset")
# ==========================================
def get_low_stock_widget(
    db: Session,
    limit: int = 20,
) -> Dict:
    """Hitung jumlah barang yang stoknya di bawah minimum (low stock).

    Sesuai Roadmap §27:
        "Low Stock count backend full dataset"
        "Jangan menghitung Inventory Value dari paginated frontend rows"

    Logic:
    - Cari barang dengan stok <= stok_minimum (dan stok_minimum > 0)
    - Return count + list of items (limited by `limit` param)

    Return:
        {
            "count": 5,
            "items": [
                {
                    "barang_id": "uuid",
                    "kode": "B001",
                    "nama": "Produk A",
                    "stok": 2,
                    "stok_minimum": 10,
                    "selisih": -8
                },
                ...
            ]
        }
    """
    # Count of low stock items (backend full dataset)
    count = (
        db.query(func.count(Barang.id))
        .filter(
            Barang.stok <= Barang.stok_minimum,
            Barang.stok_minimum > 0,
            Barang.status == "AKTIF",
        )
        .scalar()
    )
    count = int(count or 0)

    # List of low stock items (limited)
    items_query = (
        db.query(Barang)
        .filter(
            Barang.stok <= Barang.stok_minimum,
            Barang.stok_minimum > 0,
            Barang.status == "AKTIF",
        )
        .order_by((Barang.stok_minimum - Barang.stok).desc())
        .limit(limit)
        .all()
    )

    items = []
    for barang in items_query:
        items.append({
            "barang_id": str(barang.id),
            "kode": barang.kode,
            "nama": barang.nama,
            "stok": barang.stok,
            "stok_minimum": barang.stok_minimum,
            "selisih": barang.stok - barang.stok_minimum,
        })

    return {
        "count": count,
        "items": items,
    }


# ==========================================
# 3. Accounting Health Summary Widget (Roadmap §27: "Accounting Health visible")
# ==========================================
def get_accounting_health_summary_widget(db: Session) -> Dict:
    """Quick accounting health summary untuk dashboard widget.

    Sesuai Roadmap §27:
        "Accounting Health visible"

    Target Accounting Health (Roadmap §27):
        Trial Balance        MATCH
        Balance Sheet        MATCH
        AR vs GL             MATCH
        AP vs GL             MATCH
        Inventory vs GL      MATCH
        Fixed Asset vs GL    MATCH
        Cash/Bank vs GL      MATCH
        GRNI vs GL           MATCH

    Return:
        {
            "overall_status": "HEALTHY" | "ISSUES_FOUND",
            "match_count": 10,
            "mismatch_count": 1,
            "not_configured_count": 0,
            "total_checks": 11
        }

    Catatan: ini adalah summary only (tanpa detail per reconciliation).
    Untuk detail, frontend bisa call GET /laporan/accounting-health.
    """
    try:
        from app.services.accounting_health_service import get_accounting_health
        health = get_accounting_health(db, as_of=None)
        return {
            "overall_status": health["overall_status"],
            "match_count": health["summary"]["match_count"],
            "mismatch_count": health["summary"]["mismatch_count"],
            "not_configured_count": health["summary"]["not_configured_count"],
            "total_checks": health["summary"]["total_checks"],
        }
    except Exception as e:
        logger.error(f"Error getting accounting health summary: {e}")
        return {
            "overall_status": "ERROR",
            "match_count": 0,
            "mismatch_count": 0,
            "not_configured_count": 0,
            "total_checks": 0,
            "error": str(e),
        }


# ==========================================
# Phase I — Balance Sheet KPI Widgets (Catatan Financial Statements §3 item 8-9)
# ==========================================

def get_balance_sheet_kpi_widget(
    db: Session,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Balance Sheet KPI untuk dashboard (Catatan §3 item 8).

    Return AR outstanding, AP outstanding, Cash total, Total Assets.

    Source:
    - AR outstanding: sum of SalesInvoice outstanding (from aging_service)
    - AP outstanding: sum of PurchaseInvoice outstanding (from aging_service)
    - Cash total: sum of KasBankAkun GL balance (from POSTED journals)
    - Total Assets: from Balance Sheet report (get_neraca)
    """
    try:
        from app.services.laporan_service import get_neraca
        from app.services.reporting_ledger import local_datetime
        from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal
        from app.models.detail.jurnal_detail import JurnalDetail
        from app.models.master.kas_bank_akun import KasBankAkun

        if as_of is None:
            as_of = local_datetime(datetime.utcnow())

        # === 1. AR outstanding (from aging) ===
        try:
            from app.services.aging_service import aging
            ar_aging = aging(db, 'piutang', as_of)
            ar_outstanding = sum(
                Decimal(str(r.get('sisa_tagihan', 0)))
                for p in ar_aging.get('items', [])
                for r in p.get('rincian', [])
            )
        except Exception:
            ar_outstanding = Decimal('0')

        # === 2. AP outstanding (from aging) ===
        try:
            ap_aging = aging(db, 'hutang', as_of)
            ap_outstanding = sum(
                Decimal(str(r.get('sisa_tagihan', 0)))
                for p in ap_aging.get('items', [])
                for r in p.get('rincian', [])
            )
        except Exception:
            ap_outstanding = Decimal('0')

        # === 3. Cash total (from GL) ===
        try:
            cash_account_ids = [
                r[0] for r in db.query(KasBankAkun.akun_perkiraan_id).all() if r[0]
            ]
            if cash_account_ids:
                row = (
                    db.query(
                        func.coalesce(func.sum(JurnalDetail.debit), 0),
                        func.coalesce(func.sum(JurnalDetail.kredit), 0),
                    )
                    .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
                    .filter(
                        JurnalDetail.akun_perkiraan_id.in_(cash_account_ids),
                        JurnalUmum.status == StatusJurnal.POSTED,
                        JurnalUmum.tanggal <= as_of,
                    )
                    .first()
                )
                cash_total = Decimal(str(row[0] or 0)) - Decimal(str(row[1] or 0))
            else:
                cash_total = Decimal('0')
        except Exception:
            cash_total = Decimal('0')

        # === 4. Total Assets (from Balance Sheet) ===
        try:
            neraca = get_neraca(db, as_of)
            total_assets = Decimal(str(neraca.get('total_aset', 0)))
        except Exception:
            total_assets = Decimal('0')

        return {
            "ar_outstanding": str(ar_outstanding),
            "ap_outstanding": str(ap_outstanding),
            "cash_total": str(cash_total),
            "total_assets": str(total_assets),
            "as_of": as_of.isoformat() if as_of else None,
        }
    except Exception as e:
        logger.error(f"Error getting balance sheet KPI: {e}")
        return {
            "ar_outstanding": "0",
            "ap_outstanding": "0",
            "cash_total": "0",
            "total_assets": "0",
            "as_of": as_of.isoformat() if as_of else None,
            "error": str(e),
        }


def get_margin_widget(
    db: Session,
    bulan: int,
    tahun: int,
) -> Dict:
    """Gross Margin % dan Net Margin % untuk dashboard (Catatan §3 item 9).

    Source: dari get_laba_rugi (GL-based, bukan invoice).
    """
    try:
        from app.services.laporan_service import get_laba_rugi
        from app.services.reporting_ledger import month_bounds

        date_from, date_to = month_bounds(tahun, bulan)
        lr = get_laba_rugi(db, date_from, date_to)

        total_pendapatan = Decimal(str(lr.get('total_pendapatan', 0)))
        total_hpp = Decimal(str(lr.get('total_hpp', 0)))
        total_beban = Decimal(str(lr.get('total_beban', 0)))
        laba_kotor = Decimal(str(lr.get('laba_kotor', 0)))
        laba_bersih = Decimal(str(lr.get('laba_bersih', 0)))

        # Gross Margin = (Revenue - COGS) / Revenue * 100
        gross_margin_pct = (
            (laba_kotor / total_pendapatan * 100).quantize(Decimal('0.01'))
            if total_pendapatan > 0 else Decimal('0')
        )

        # Net Margin = Net Profit / Revenue * 100
        net_margin_pct = (
            (laba_bersih / total_pendapatan * 100).quantize(Decimal('0.01'))
            if total_pendapatan > 0 else Decimal('0')
        )

        return {
            "total_pendapatan": str(total_pendapatan),
            "total_hpp": str(total_hpp),
            "total_beban": str(total_beban),
            "laba_kotor": str(laba_kotor),
            "laba_bersih": str(laba_bersih),
            "gross_margin_pct": str(gross_margin_pct),
            "net_margin_pct": str(net_margin_pct),
            "periode": {"bulan": bulan, "tahun": tahun},
        }
    except Exception as e:
        logger.error(f"Error getting margin widget: {e}")
        return {
            "total_pendapatan": "0",
            "total_hpp": "0",
            "total_beban": "0",
            "laba_kotor": "0",
            "laba_bersih": "0",
            "gross_margin_pct": "0",
            "net_margin_pct": "0",
            "periode": {"bulan": bulan, "tahun": tahun},
            "error": str(e),
        }
