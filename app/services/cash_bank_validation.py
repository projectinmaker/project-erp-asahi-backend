"""cash_bank_validation.py

Helper untuk enforce Master Roadmap §23 (Cash/Bank):
- validate_active_kas_bank: cek KasBankAkun AKTIF
- validate_transfer_banks_different: cek dari != ke
- validate_reconciliation_cutoff_lock: cek tidak ada rekonsiliasi SELESAI setelah tanggal_akhir

Dipakai oleh:
- app/services/kas_bank_service.py (create pembayaran/penerimaan/transfer)
- app/services/rekonsiliasi_bank_service.py (create/update rekonsiliasi)
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from typing import Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.master.kas_bank_akun import KasBankAkun
from app.models.transaksi.kas_bank.rekonsiliasi_bank import (
    RekonsiliasiBank, StatusRekonsiliasi,
)


# ==========================================
# Cash/Bank Master Validation
# ==========================================
def validate_active_kas_bank(
    db: Session,
    kas_bank_id: UUID,
    context: str = "Transaksi",
) -> KasBankAkun:
    """Cek apakah KasBankAkun masih AKTIF sebelum dipakai transaksi.

    Sesuai Master Roadmap §23:
        "Bank/Cash ACTIVE"

    Dipanggil di awal create Pembayaran/Penerimaan/Transfer.
    """
    kb = db.get(KasBankAkun, kas_bank_id)
    if kb is None:
        raise HTTPException(
            status_code=404,
            detail=f"Kas/Bank dengan ID {kas_bank_id} tidak ditemukan. {context} dibatalkan.",
        )
    if kb.status != "AKTIF":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Kas/Bank '{kb.nama}' (kode: {kb.kode}) sudah {kb.status} — "
                f"tidak boleh dipakai transaksi baru. {context} dibatalkan."
            ),
        )
    return kb


def validate_transfer_banks_different(
    dari_kas_bank_id: UUID,
    ke_kas_bank_id: UUID,
    context: str = "Transfer Bank",
) -> None:
    """Cek apakah bank asal != bank tujuan.

    Sesuai Master Roadmap §23:
        "Source/destination bank different"

    Raises:
        HTTPException 400 kalau sama
    """
    if dari_kas_bank_id == ke_kas_bank_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Gudang asal dan tujuan tidak boleh sama (id: {dari_kas_bank_id}). "
                f"{context} dibatalkan. Pilih kas/bank tujuan yang berbeda."
            ),
        )


def validate_transfer_amount(
    nilai_transfer: Decimal,
    biaya_transfer: Decimal = Decimal("0"),
    context: str = "Transfer Bank",
) -> None:
    """Cek apakah nilai transfer valid.

    Sesuai Master Roadmap §23:
        "Transfer amount > 0"
        "Fee >= 0"

    Raises:
        HTTPException 400 kalau invalid
    """
    if nilai_transfer is None or nilai_transfer <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Nilai transfer harus > 0 (diterima: {nilai_transfer}). "
                f"{context} dibatalkan."
            ),
        )
    if biaya_transfer is None or biaya_transfer < 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Biaya transfer harus >= 0 (diterima: {biaya_transfer}). "
                f"{context} dibatalkan."
            ),
        )


# ==========================================
# Reconciliation Cutoff Lock
# ==========================================
def validate_reconciliation_cutoff_lock(
    db: Session,
    kas_bank_akun_id: UUID,
    tanggal_akhir: datetime,
    exclude_rekonsiliasi_id: Optional[UUID] = None,
    context: str = "Rekonsiliasi Bank",
) -> None:
    """Cek apakah tidak ada rekonsiliasi SELESAI setelah tanggal_akhir.

    Sesuai Master Roadmap §23:
        "Reconciled cutoff lock"

    Logic:
    - Cari rekonsiliasi SELESAI untuk kas/bank yang sama dengan tanggal_akhir > tanggal_akhir baru
    - Kalau ada, reject dengan pesan jelas

    Tujuan: prevent user buat/edit rekonsiliasi untuk periode yang sudah
    "locked" oleh rekonsiliasi SELESAI setelahnya.

    Parameter:
        db: SQLAlchemy Session
        kas_bank_akun_id: UUID KasBankAkun
        tanggal_akhir: tanggal akhir rekonsiliasi yang akan dibuat/diedit
        exclude_rekonsiliasi_id: skip rekonsiliasi tertentu (untuk update case)
        context: deskripsi untuk error message

    Raises:
        HTTPException 400 kalau ada rekonsiliasi SELESAI setelah tanggal_akhir
    """
    query = (
        db.query(RekonsiliasiBank)
        .filter(
            RekonsiliasiBank.kas_bank_akun_id == kas_bank_akun_id,
            RekonsiliasiBank.status == StatusRekonsiliasi.SELESAI.value,
            RekonsiliasiBank.tanggal_akhir > tanggal_akhir,
        )
    )
    if exclude_rekonsiliasi_id is not None:
        query = query.filter(RekonsiliasiBank.id != exclude_rekonsiliasi_id)

    existing = query.order_by(RekonsiliasiBank.tanggal_akhir.asc()).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tidak bisa buat/edit rekonsiliasi untuk tanggal {tanggal_akhir.strftime('%d/%m/%Y')} "
                f"karena sudah ada rekonsiliasi SELESAI untuk tanggal "
                f"{existing.tanggal_akhir.strftime('%d/%m/%Y')} (id: {existing.id}). "
                f"Cutoff lock aktif — periode setelah rekonsiliasi SELESAI tidak boleh di-edit. "
                f"Void rekonsiliasi SELESAI terlebih dahulu kalau perlu edit periode sebelumnya. "
                f"{context} dibatalkan."
            ),
        )
