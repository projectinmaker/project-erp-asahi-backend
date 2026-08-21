"""
posting_service.py

Service untuk auto-posting jurnal umum dari berbagai modul transaksi.
Digunakan oleh modul Kas/Bank, Penjualan, Pembelian, Persediaan, dan Aset Tetap.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session

from app.models.transaksi.jurnal import JurnalUmum, RefModule, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail


class JurnalEntryItem:
    """Data class untuk satu baris jurnal (debit atau kredit)."""
    def __init__(
        self,
        akun_perkiraan_id: UUID,
        debit: Decimal = Decimal("0"),
        kredit: Decimal = Decimal("0"),
        keterangan: Optional[str] = None,
    ):
        self.akun_perkiraan_id = akun_perkiraan_id
        self.debit = debit
        self.kredit = kredit
        self.keterangan = keterangan


def auto_posting_jurnal(
    db: Session,
    ref_module: RefModule,
    ref_no: str,
    entries: List[JurnalEntryItem],
    keterangan: Optional[str] = None,
    ref_id: Optional[UUID] = None,
    tanggal: Optional[datetime] = None,
    created_by: Optional[UUID] = None,
    tipe_transaksi: Optional[str] = None,
    status: StatusJurnal = StatusJurnal.POSTED,
    no_jurnal: Optional[str] = None,
) -> JurnalUmum:
    """
    Membuat Jurnal Umum otomatis beserta detailnya (double-entry).

    Parameter:
        db: SQLAlchemy Session
        ref_module: Enum RefModule (SALES_INVOICE, PEMBAYARAN, dll)
        ref_no: Nomor dokumen sumber (misal INV-2026-08-001)
        entries: List of JurnalEntryItem — baris-baris jurnal (debit & kredit)
        keterangan: Keterangan umum jurnal
        ref_id: UUID dokumen sumber (opsional)
        tanggal: Tanggal jurnal (default: sekarang)
        created_by: UUID user yang membuat
        tipe_transaksi: Tipe transaksi (opsional, untuk pelacakan)
        status: Status jurnal (default: POSTED)
        no_jurnal: Nomor jurnal (jika None, akan digenerate otomatis)

    Return:
        JurnalUmum object yang sudah di-commit ke database
    """
    try:
        # Validasi: pastikan entries tidak kosong
        if not entries:
            raise ValueError("entries tidak boleh kosong, minimal 2 baris (debit & kredit)")

        # Validasi: pastikan total debit == total kredit (balanced)
        total_debit = sum(e.debit for e in entries)
        total_kredit = sum(e.kredit for e in entries)
        if total_debit != total_kredit:
            raise ValueError(
                f"Jurnal tidak balance: total debit={total_debit}, total_kredit={total_kredit}"
            )

        # Generate nomor jurnal jika tidak diberikan
        if no_jurnal is None:
            now = tanggal or datetime.now(timezone.utc)
            prefix = "JV"
            # Format: JV-YYYYMM-NNN (counter per bulan disederhanakan)
            no_jurnal = f"{prefix}-{now.strftime('%Y%m')}-001"

        # Default tanggal
        if tanggal is None:
            tanggal = datetime.now(timezone.utc)

        # Buat header Jurnal Umum
        jurnal = JurnalUmum(
            no_jurnal=no_jurnal,
            tanggal=tanggal,
            tipe_transaksi=tipe_transaksi or ref_module.value,
            ref_module=ref_module,
            ref_no=ref_no,
            ref_id=ref_id,
            total_debit=total_debit,
            total_kredit=total_kredit,
            keterangan=keterangan,
            status=status,
            created_by=created_by,
        )

        db.add(jurnal)

        # Buat detail jurnal
        for entry in entries:
            detail = JurnalDetail(
                jurnal_umum_id=jurnal.id,
                akun_perkiraan_id=entry.akun_perkiraan_id,
                debit=entry.debit,
                kredit=entry.kredit,
                keterangan=entry.keterangan,
            )
            db.add(detail)

        db.commit()
        db.refresh(jurnal)

        logger.info(
            f"Jurnal posted: {jurnal.no_jurnal} | ref={ref_no} | "
            f"D={total_debit} K={total_kredit} | {len(entries)} details"
        )
        return jurnal

    except Exception as e:
        db.rollback()
        logger.error(f"Error auto-posting jurnal: {e}")
        raise
