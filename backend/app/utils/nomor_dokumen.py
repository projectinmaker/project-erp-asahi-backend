"""
nomor_dokumen.py

Utility untuk generate nomor dokumen otomatis.
Format: PREFIX-YYYY-MM-NNN (contoh: INV-2026-08-001)
Menggunakan dependency injection (db: Session) bukan langsung SessionLocal.
"""

from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session


# Mapping doc_type ke prefix
default_prefix_map = {
    "INVOICE": "INV",
    "PAYMENT": "PAY",
    "RETUR_PEMBELAN": "RET-PB",
    "RETUR_BARANG": "RET-BRG",
    "SALES_ORDER": "SO",
    "PURCHASE_ORDER": "PO",
    "PENERIMAAN_BARANG": "PB",
    "PENGIRIMAN_BARANG": "KB",
    "PENYESUAIAN_STOK": "PS",
    "TRANSFER_KAS": "TK",
    "MANUAL_JURNAL": "JV",
}


def get_nomor_dokumen(
    db: Session,
    model_class,
    prefix: str,
    no_column: str = "nomor",
    tanggal: Optional[date] = None,
) -> str:
    """
    Generate nomor dokumen otomatis dengan format: PREFIX-YYYY-MM-NNN.

    Parameter:
        db: SQLAlchemy Session (dependency injection)
        model_class: Model SQLAlchemy untuk mencari nomor terakhir
        prefix: Prefix dokumen (INV, PAY, SO, PO, dll)
        no_column: Nama kolom nomor di model (default: "nomor")
        tanggal: Tanggal dokumen (default: hari ini)

    Return:
        String nomor dokumen, contoh: "INV-2026-08-001"
    """
    if tanggal is None:
        tanggal = date.today()

    month_str = tanggal.strftime("%Y-%m")
    pattern = f"{prefix}-{month_str}%"

    # Cari nomor terakhir dengan prefix dan bulan yang sama
    last_no = (
        db.query(getattr(model_class, no_column))
        .filter(getattr(model_class, no_column).like(pattern))
        .order_by(getattr(model_class, no_column).desc())
        .first()
    )

    if last_no and last_no[0]:
        # Ekstrak angka terakhir: INV-2026-08-003 -> 003 -> 3
        parts = last_no[0].rsplit("-", 1)
        try:
            last_seq = int(parts[1])
        except (IndexError, ValueError):
            last_seq = 0
    else:
        last_seq = 0

    next_seq = last_seq + 1
    return f"{prefix}-{month_str}-{next_seq:03d}"
