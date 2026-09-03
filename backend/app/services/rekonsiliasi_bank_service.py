from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from app.models.transaksi.kas_bank.rekonsiliasi_bank import (
    RekonsiliasiBank, RekonsiliasiBankDetail,
    StatusRekonsiliasi, TipeRekonsiliasiDetail, SisiPenyesuaian,
)
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal, RefModule
from app.models.detail.jurnal_detail import JurnalDetail
from app.models.master.kas_bank_akun import KasBankAkun
from app.models.akun_perkiraan import AkunPerkiraan, SaldoNormal
from app.services.posting_service import JurnalEntryItem, auto_posting_jurnal


# ============================================================
# HELPER: Compute saldo buku dari jurnal
# ============================================================

def compute_saldo_buku(db: Session, kas_bank_akun_id: UUID, tanggal_akhir: datetime) -> Decimal:
    """Hitung saldo buku untuk akun kas/bank sampai tanggal_akhir (inclusive).

    Menggunakan jurnal_detail + jurnal_umum (POSTED only).
    Akun kas/bank selalu saldo_normal DEBIT.
    """
    kb = db.query(KasBankAkun).filter(KasBankAkun.id == kas_bank_akun_id).first()
    if not kb:
        raise ValueError(f"Kas/Bank dengan ID {kas_bank_akun_id} tidak ditemukan")

    akun_id = kb.akun_perkiraan_id
    if not akun_id:
        raise ValueError(f"Kas/Bank {kb.nama} tidak memiliki akun perkiraan")

    akun = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == akun_id).first()
    if not akun:
        raise ValueError(f"Akun perkiraan tidak ditemukan untuk Kas/Bank {kb.nama}")

    # Total debit & kredit from all POSTED journals up to tanggal_akhir (inclusive)
    row = (
        db.query(
            func.coalesce(func.sum(JurnalDetail.debit), 0),
            func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id == akun_id,
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal <= tanggal_akhir,
        )
        .first()
    )

    total_debit = Decimal(str(row[0]))
    total_kredit = Decimal(str(row[1]))

    if akun.saldo_normal == SaldoNormal.DEBIT:
        return total_debit - total_kredit
    else:
        return total_kredit - total_debit


# ============================================================
# LIST
# ============================================================

def get_rekonsiliasi_list(
    db: Session,
    kas_bank_akun_id: Optional[UUID] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Tuple[List[RekonsiliasiBank], int]:
    """Ambil daftar rekonsiliasi bank dengan filter & pagination."""
    query = db.query(RekonsiliasiBank).options(
        joinedload(RekonsiliasiBank.kas_bank),
        joinedload(RekonsiliasiBank.creator),
        joinedload(RekonsiliasiBank.jurnal),
    )

    if kas_bank_akun_id:
        query = query.filter(RekonsiliasiBank.kas_bank_akun_id == kas_bank_akun_id)
    if status:
        query = query.filter(RekonsiliasiBank.status == status)

    total = query.count()
    data = query.order_by(
        RekonsiliasiBank.tanggal_akhir.desc()
    ).offset(skip).limit(limit).all()
    return data, total


# ============================================================
# GET BY ID
# ============================================================

def get_rekonsiliasi_by_id(db: Session, rekonsiliasi_id: UUID) -> Optional[RekonsiliasiBank]:
    """Ambil 1 rekonsiliasi berdasarkan ID dengan semua detail."""
    return (
        db.query(RekonsiliasiBank)
        .options(
            joinedload(RekonsiliasiBank.kas_bank),
            joinedload(RekonsiliasiBank.creator),
            joinedload(RekonsiliasiBank.jurnal),
            joinedload(RekonsiliasiBank.details).joinedload(RekonsiliasiBankDetail.akun_perkiraan),
        )
        .filter(RekonsiliasiBank.id == rekonsiliasi_id)
        .first()
    )


# ============================================================
# CREATE
# ============================================================

def create_rekonsiliasi(
    db: Session,
    kas_bank_akun_id: UUID,
    tanggal_akhir: datetime,
    saldo_bank: Decimal,
    user_id: UUID,
    keterangan: Optional[str] = None,
) -> RekonsiliasiBank:
    """Buat rekonsiliasi bank baru (status DRAFT).

    1. Validasi kas_bank exists dan aktif
    2. Cek belum ada rekonsiliasi SELESAI untuk kas_bank + tanggal yang sama
    3. Compute saldo_buku dari jurnal
    4. Hitung selisih = saldo_bank - saldo_buku
    5. Simpan header DRAFT
    """
    kb = db.query(KasBankAkun).filter(
        KasBankAkun.id == kas_bank_akun_id,
        KasBankAkun.status == "AKTIF",
    ).first()
    if not kb:
        raise ValueError(f"Kas/Bank tidak ditemukan atau tidak aktif")

    # Cek duplikat: tidak boleh ada rekonsiliasi SELESAI untuk periode yang sama
    existing = (
        db.query(RekonsiliasiBank)
        .filter(
            RekonsiliasiBank.kas_bank_akun_id == kas_bank_akun_id,
            RekonsiliasiBank.tanggal_akhir == tanggal_akhir,
            RekonsiliasiBank.status == StatusRekonsiliasi.SELESAI.value,
        )
        .first()
    )
    if existing:
        raise ValueError(
            f"Sudah ada rekonsiliasi SELESAI untuk {kb.nama} per {tanggal_akhir.strftime('%d/%m/%Y')}"
        )

    # Compute saldo buku
    saldo_buku = compute_saldo_buku(db, kas_bank_akun_id, tanggal_akhir)
    selisih = Decimal(str(saldo_bank)) - saldo_buku

    rekonsiliasi = RekonsiliasiBank(
        kas_bank_akun_id=kas_bank_akun_id,
        tanggal_akhir=tanggal_akhir,
        saldo_bank=saldo_bank,
        saldo_buku=saldo_buku,
        selisih=selisih,
        status=StatusRekonsiliasi.DRAFT.value,
        keterangan=keterangan,
        created_by=user_id,
    )
    db.add(rekonsiliasi)
    db.commit()
    db.refresh(rekonsiliasi)
    logger.info(
        f"Rekonsiliasi DRAFT: {kb.nama} | {tanggal_akhir.strftime('%d/%m/%Y')} | "
        f"buku={saldo_buku} bank={saldo_bank} selisih={selisih}"
    )
    return rekonsiliasi


# ============================================================
# UPDATE HEADER
# ============================================================

def update_rekonsiliasi(
    db: Session,
    db_obj: RekonsiliasiBank,
    saldo_bank: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
) -> RekonsiliasiBank:
    """Update header rekonsiliasi (hanya saldo_bank & keterangan).

    Recomputes saldo_buku and selisih.
    Hanya bisa di DRAFT.
    """
    if db_obj.status != StatusRekonsiliasi.DRAFT.value:
        raise ValueError("Hanya rekonsiliasi DRAFT yang bisa diupdate")

    if saldo_bank is not None:
        db_obj.saldo_bank = saldo_bank
    if keterangan is not None:
        db_obj.keterangan = keterangan

    # Recompute saldo_buku & selisih
    db_obj.saldo_buku = compute_saldo_buku(db, db_obj.kas_bank_akun_id, db_obj.tanggal_akhir)
    db_obj.selisih = Decimal(str(db_obj.saldo_bank)) - db_obj.saldo_buku

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


# ============================================================
# DETAIL CRUD
# ============================================================

def add_detail(
    db: Session,
    rekonsiliasi_id: UUID,
    tipe: str,
    keterangan: str,
    jumlah: Decimal,
    sisi: str,
    akun_perkiraan_id: Optional[UUID] = None,
) -> RekonsiliasiBankDetail:
    """Tambah detail line ke rekonsiliasi (hanya DRAFT)."""
    rek = db.query(RekonsiliasiBank).filter(RekonsiliasiBank.id == rekonsiliasi_id).first()
    if not rek:
        raise ValueError("Rekonsiliasi tidak ditemukan")
    if rek.status != StatusRekonsiliasi.DRAFT.value:
        raise ValueError("Hanya rekonsiliasi DRAFT yang bisa ditambah detail")

    # Validasi tipe
    if tipe not in (TipeRekonsiliasiDetail.MEMO.value, TipeRekonsiliasiDetail.PENYESUAIAN.value):
        raise ValueError(f"Tipe harus MEMO atau PENYESUAIAN, diberikan: {tipe}")

    # Validasi sisi
    if sisi not in (SisiPenyesuaian.DEBIT.value, SisiPenyesuaian.KREDIT.value):
        raise ValueError(f"Sisi harus DEBIT atau KREDIT, diberikan: {sisi}")

    # PENYESUAIAN wajib punya akun_perkiraan_id
    if tipe == TipeRekonsiliasiDetail.PENYESUAIAN.value and not akun_perkiraan_id:
        raise ValueError("PENYESUAIAN wajib memiliki akun_perkiraan_id")

    # Jumlah harus positif
    if jumlah <= 0:
        raise ValueError("Jumlah harus lebih dari 0")

    detail = RekonsiliasiBankDetail(
        rekonsiliasi_bank_id=rekonsiliasi_id,
        tipe=tipe,
        keterangan=keterangan,
        jumlah=jumlah,
        sisi=sisi,
        akun_perkiraan_id=akun_perkiraan_id,
    )
    db.add(detail)
    db.commit()
    db.refresh(detail)
    return detail


def update_detail(
    db: Session,
    detail_id: UUID,
    keterangan: Optional[str] = None,
    jumlah: Optional[Decimal] = None,
    sisi: Optional[str] = None,
    akun_perkiraan_id: Optional[UUID] = None,
) -> RekonsiliasiBankDetail:
    """Update detail line (hanya DRAFT)."""
    detail = db.query(RekonsiliasiBankDetail).filter(RekonsiliasiBankDetail.id == detail_id).first()
    if not detail:
        raise ValueError("Detail rekonsiliasi tidak ditemukan")

    rek = db.query(RekonsiliasiBank).filter(RekonsiliasiBank.id == detail.rekonsiliasi_bank_id).first()
    if rek and rek.status != StatusRekonsiliasi.DRAFT.value:
        raise ValueError("Hanya rekonsiliasi DRAFT yang bisa diupdate detailnya")

    if keterangan is not None:
        detail.keterangan = keterangan
    if jumlah is not None:
        if jumlah <= 0:
            raise ValueError("Jumlah harus lebih dari 0")
        detail.jumlah = jumlah
    if sisi is not None:
        if sisi not in (SisiPenyesuaian.DEBIT.value, SisiPenyesuaian.KREDIT.value):
            raise ValueError(f"Sisi harus DEBIT atau KREDIT, diberikan: {sisi}")
        detail.sisi = sisi
    if akun_perkiraan_id is not None:
        detail.akun_perkiraan_id = akun_perkiraan_id
    elif akun_perkiraan_id is not None and detail.tipe == TipeRekonsiliasiDetail.PENYESUAIAN.value:
        # Explicitly set to None for PENYESUAIAN is not allowed
        pass

    db.add(detail)
    db.commit()
    db.refresh(detail)
    return detail


def remove_detail(db: Session, detail_id: UUID) -> None:
    """Hapus detail line (hanya DRAFT)."""
    detail = db.query(RekonsiliasiBankDetail).filter(RekonsiliasiBankDetail.id == detail_id).first()
    if not detail:
        raise ValueError("Detail rekonsiliasi tidak ditemukan")

    rek = db.query(RekonsiliasiBank).filter(RekonsiliasiBank.id == detail.rekonsiliasi_bank_id).first()
    if rek and rek.status != StatusRekonsiliasi.DRAFT.value:
        raise ValueError("Hanya rekonsiliasi DRAFT yang bisa dihapus detailnya")

    db.delete(detail)
    db.commit()


# ============================================================
# COMPLETE REKONSILIASI
# ============================================================

def complete_rekonsiliasi(db: Session, rekonsiliasi_id: UUID, user_id: UUID) -> RekonsiliasiBank:
    """Selesaikan rekonsiliasi (DRAFT → SELESAI).

    1. Validasi status DRAFT
    2. Validasi balance: adjusted_buku == adjusted_bank (within tolerance)
    3. Buat jurnal penyesuaian untuk detail PENYESUAIAN (non-fatal)
    4. Set status SELESAI
    5. Commit

    Balance formula:
        saldo_buku + penyesuaian_net = saldo_bank + memo_net

    Where:
        penyesuaian_net = sum(DEBIT) - sum(KREDIT) for PENYESUAIAN items
        memo_net = sum(DEBIT) - sum(KREDIT) for MEMO items
    """
    rek = (
        db.query(RekonsiliasiBank)
        .options(
            joinedload(RekonsiliasiBank.kas_bank),
            joinedload(RekonsiliasiBank.details),
        )
        .filter(RekonsiliasiBank.id == rekonsiliasi_id)
        .first()
    )
    if not rek:
        raise ValueError("Rekonsiliasi tidak ditemukan")
    if rek.status != StatusRekonsiliasi.DRAFT.value:
        raise ValueError(f"Hanya rekonsiliasi DRAFT yang bisa diselesaikan, status saat ini: {rek.status}")

    # Recompute saldo_buku (might have changed since creation)
    rek.saldo_buku = compute_saldo_buku(db, rek.kas_bank_akun_id, rek.tanggal_akhir)
    rek.selisih = Decimal(str(rek.saldo_bank)) - rek.saldo_buku

    # Compute nets from details
    penyesuaian_net = Decimal("0")
    memo_net = Decimal("0")
    penyesuaian_items: List[RekonsiliasiBankDetail] = []

    for d in rek.details:
        amount = Decimal(str(d.jumlah))
        if d.sisi == SisiPenyesuaian.DEBIT.value:
            signed = amount
        else:
            signed = -amount

        if d.tipe == TipeRekonsiliasiDetail.PENYESUAIAN.value:
            penyesuaian_net += signed
            penyesuaian_items.append(d)
        else:  # MEMO
            memo_net += signed

    # Balance check: saldo_buku + penyesuaian_net should equal saldo_bank + memo_net
    adjusted_buku = rek.saldo_buku + penyesuaian_net
    adjusted_bank = Decimal(str(rek.saldo_bank)) + memo_net
    diff = abs(adjusted_buku - adjusted_bank)

    if diff > Decimal("0.01"):  # Tolerance 1 cent
        raise ValueError(
            f"Rekonsiliasi tidak balance. "
            f"Adjusted Buku: {adjusted_buku}, Adjusted Bank: {adjusted_bank}, "
            f"Selisih: {diff}. Tambahkan detail untuk menyeimbangkan."
        )

    # Jurnal penyesuaian (non-fatal)
    jurnal_penyesuaian_id = None
    if penyesuaian_items:
        try:
            jurnal_penyesuaian_id = _create_adjustment_journal(
                db=db,
                rek=rek,
                items=penyesuaian_items,
                user_id=user_id,
            )
            rek.jurnal_penyesuaian_id = jurnal_penyesuaian_id
        except Exception as e:
            logger.warning(f"Jurnal penyesuaian rekonsiliasi gagal (non-fatal): {e}")

    rek.status = StatusRekonsiliasi.SELESAI.value
    db.commit()
    db.refresh(rek)
    logger.info(
        f"Rekonsiliasi SELESAI: {rek.kas_bank.nama} | {rek.tanggal_akhir.strftime('%d/%m/%Y')} | "
        f"adjusted={adjusted_buku} | journal={'YA' if jurnal_penyesuaian_id else 'TIDAK'}"
    )
    return rek


def _create_adjustment_journal(
    db: Session,
    rek: RekonsiliasiBank,
    items: List[RekonsiliasiBankDetail],
    user_id: UUID,
) -> Optional[UUID]:
    """Buat jurnal penyesuaian untuk PENYESUAIAN items.

    DEBIT item: D-KasBank, K-akun_perkiraan (e.g., bank interest)
    KREDIT item: D-akun_perkiraan, K-KasBank (e.g., bank charges)

    Return: UUID jurnal, atau None jika tidak ada item.
    """
    if not items:
        return None

    kas_bank_akun_id = rek.kas_bank.akun_perkiraan_id
    entries: List[JurnalEntryItem] = []

    for item in items:
        if not item.akun_perkiraan_id:
            logger.warning(f"PENYESUAIAN item {item.id} tidak memiliki akun_perkiraan_id, skip")
            continue

        if item.sisi == SisiPenyesuaian.DEBIT.value:
            # Bank interest: D-KasBank, K-akun
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=kas_bank_akun_id,
                debit=item.jumlah,
                keterangan=f"Rekonsiliasi: {item.keterangan}",
            ))
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=item.akun_perkiraan_id,
                kredit=item.jumlah,
                keterangan=f"Rekonsiliasi: {item.keterangan}",
            ))
        else:
            # Bank charges: D-akun, K-KasBank
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=item.akun_perkiraan_id,
                debit=item.jumlah,
                keterangan=f"Rekonsiliasi: {item.keterangan}",
            ))
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=kas_bank_akun_id,
                kredit=item.jumlah,
                keterangan=f"Rekonsiliasi: {item.keterangan}",
            ))

    if not entries:
        return None

    ref_no = f"RB-{rek.tanggal_akhir.strftime('%Y%m')}-{rek.kas_bank.kode}"
    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.REKONSILIASI_BANK,
        ref_no=ref_no,
        entries=entries,
        keterangan=f"Penyesuaian Rekonsiliasi Bank - {rek.kas_bank.nama} per {rek.tanggal_akhir.strftime('%d/%m/%Y')}",
        tanggal=rek.tanggal_akhir,
        created_by=user_id,
        status=StatusJurnal.POSTED,
    )
    logger.info(f"Jurnal penyesuaian rekonsiliasi posted: {jurnal.no_jurnal} | {len(entries)} details")
    return jurnal.id


# ============================================================
# VOID REKONSILIASI
# ============================================================

def void_rekonsiliasi(db: Session, rekonsiliasi_id: UUID) -> RekonsiliasiBank:
    """Batalkan rekonsiliasi (SELESAI/DRAFT → BATAL).

    Catatan: Jurnal penyesuaian TIDAK dihapus (audit trail).
    Pengguna harus membuat jurnal balik manual jika diperlukan.
    """
    rek = (
        db.query(RekonsiliasiBank)
        .options(joinedload(RekonsiliasiBank.kas_bank))
        .filter(RekonsiliasiBank.id == rekonsiliasi_id)
        .first()
    )
    if not rek:
        raise ValueError("Rekonsiliasi tidak ditemukan")
    if rek.status == StatusRekonsiliasi.BATAL.value:
        raise ValueError("Rekonsiliasi sudah dibatalkan")

    rek.status = StatusRekonsiliasi.BATAL.value
    db.add(rek)
    db.commit()
    db.refresh(rek)
    logger.info(f"Rekonsiliasi BATAL: {rek.kas_bank.nama} | {rek.tanggal_akhir.strftime('%d/%m/%Y')}")
    return rek
