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
