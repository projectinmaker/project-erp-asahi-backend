"""
kas_bank_service.py

Service layer untuk modul Kas & Bank.
Menghandle CRUD + auto-posting jurnal untuk:
- PembayaranKas (+ PembayaranRincian)
- PenerimaanKas (+ PenerimaanRincian)
- TransferBank
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.transaksi.kas_bank.pembayaran import PembayaranKas, StatusTransaksi
from app.models.transaksi.kas_bank.penerimaan import PenerimaanKas
from app.models.transaksi.kas_bank.transfer_bank import TransferBank
from app.models.detail.pembayaran_rincian import PembayaranRincian
from app.models.detail.penerimaan_rincian import PenerimaanRincian
from app.models.master.kas_bank_akun import KasBankAkun
from app.models.master.pengguna import Pengguna
from app.models.transaksi.jurnal import RefModule
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.utils.nomor_dokumen import get_nomor_dokumen


# ==========================================
# PEMBAYARAN KAS
# ==========================================

def get_pembayaran_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    kas_bank_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PembayaranKas], int]:
    """Ambil daftar pembayaran kas dengan filter & pagination."""
    query = db.query(PembayaranKas).options(
        joinedload(PembayaranKas.kas_bank),
        joinedload(PembayaranKas.creator),
        joinedload(PembayaranKas.rincian).joinedload(PembayaranRincian.akun_perkiraan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PembayaranKas.no_bukti.ilike(pattern)
            | PembayaranKas.penerima.ilike(pattern)
            | PembayaranKas.no_nukti.ilike(pattern)
        )

    if status:
        query = query.filter(PembayaranKas.status == status)
    if kas_bank_id:
        query = query.filter(PembayaranKas.kas_bank_id == kas_bank_id)
    if tanggal_from:
        query = query.filter(PembayaranKas.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PembayaranKas.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PembayaranKas.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_pembayaran_by_id(db: Session, pembayaran_id: UUID) -> Optional[PembayaranKas]:
    """Ambil 1 pembayaran berdasarkan ID dengan rincian."""
    return (
        db.query(PembayaranKas)
        .options(
            joinedload(PembayaranKas.kas_bank),
            joinedload(PembayaranKas.creator),
            joinedload(PembayaranKas.rincian).joinedload(PembayaranRincian.akun_perkiraan),
        )
        .filter(PembayaranKas.id == pembayaran_id)
        .first()
    )


def create_pembayaran(
    db: Session,
    no_nukti: str,
    tanggal: datetime,
    kas_bank_id: UUID,
    rincian_data: list,
    no_cek: Optional[str] = None,
    penerima: Optional[str] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: UUID = None,
) -> PembayaranKas:
    """
    Buat PembayaranKas baru beserta rincian.
    - Generate no_bukti otomatis
    - Hitung total_nilai dari sum rincian
    - Auto-post jurnal jika auto_post_jurnal=True dan status=SELESAI
    """
    try:
        # Generate nomor bukti
        no_bukti = get_nomor_dokumen(
            db, PembayaranKas, prefix="PAY",
            no_column="no_bukti", tanggal=tanggal.date()
        )

        # Hitung total dari rincian
        total_nilai = sum(Decimal(str(r.get("nilai", 0))) for r in rincian_data)

        # Validasi: dari_kas_bank harus ada
        kas_bank = db.query(KasBankAkun).filter(KasBankAkun.id == kas_bank_id).first()
        if not kas_bank:
            raise ValueError(f"Kas/Bank dengan ID {kas_bank_id} tidak ditemukan")

        # Buat header
        pembayaran = PembayaranKas(
            no_bukti=no_bukti,
            tanggal=tanggal,
            kas_bank_id=kas_bank_id,
            no_nukti=no_nukti,
            no_cek=no_cek,
            penerima=penerima,
            catatan=catatan,
            total_nilai=total_nilai,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusTransaksi.SELESAI,
            created_by=created_by,
        )
        db.add(pembayaran)
        db.flush()  # Flush untuk dapat ID

        # Buat rincian
        for r in rincian_data:
            detail = PembayaranRincian(
                pembayaran_id=pembayaran.id,
                akun_perkiraan_id=r["akun_perkiraan_id"],
                nilai=Decimal(str(r["nilai"])),
            )
            db.add(detail)

        # Auto-post jurnal
        if auto_post_jurnal and total_nilai > 0:
            entries = [
                # Kredit: Kas/Bank
                JurnalEntryItem(
                    akun_perkiraan_id=kas_bank.akun_perkiraan_id,
                    kredit=total_nilai,
                    keterangan=f"Pembayaran {no_bukti}",
                ),
            ]
            # Debit: Akun-akun dari rincian
            for r in rincian_data:
                entries.append(
                    JurnalEntryItem(
                        akun_perkiraan_id=r["akun_perkiraan_id"],
                        debit=Decimal(str(r["nilai"])),
                    )
                )

            jurnal = auto_posting_jurnal(
                db=db,
                ref_module=RefModule.PEMBAYARAN,
                ref_no=no_bukti,
                entries=entries,
                keterangan=f"Pembayaran Kas {no_bukti}",
                ref_id=pembayaran.id,
                tanggal=tanggal,
                created_by=created_by,
            )
            pembayaran.jurnal_umum_id = jurnal.id

        db.commit()
        db.refresh(pembayaran)
        logger.info(f"PembayaranKas created: {no_bukti} | total={total_nilai}")
        return pembayaran

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PembayaranKas: {e}")
        raise


def update_pembayaran(
    db: Session,
    db_obj: PembayaranKas,
    tanggal: Optional[datetime] = None,
    kas_bank_id: Optional[UUID] = None,
    no_nukti: Optional[str] = None,
    no_cek: Optional[str] = None,
    penerima: Optional[str] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> PembayaranKas:
    """Update data pembayaran (hanya field yang diberikan)."""
    if tanggal is not None:
        db_obj.tanggal = tanggal
    if kas_bank_id is not None:
        db_obj.kas_bank_id = kas_bank_id
    if no_nukti is not None:
        db_obj.no_nukti = no_nukti
    if no_cek is not None:
        db_obj.no_cek = no_cek
    if penerima is not None:
        db_obj.penerima = penerima
    if catatan is not None:
        db_obj.catatan = catatan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def cancel_pembayaran(db: Session, db_obj: PembayaranKas) -> PembayaranKas:
    """Batalkan pembayaran (status -> BATAL)."""
    if db_obj.status == StatusTransaksi.BATAL:
        raise ValueError("Pembayaran sudah dibatalkan")
    db_obj.status = StatusTransaksi.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PembayaranKas cancelled: {db_obj.no_bukti}")
    return db_obj


# ==========================================
# PENERIMAAN KAS
# ==========================================

def get_penerimaan_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    kas_bank_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PenerimaanKas], int]:
    """Ambil daftar penerimaan kas dengan filter & pagination."""
    query = db.query(PenerimaanKas).options(
        joinedload(PenerimaanKas.kas_bank),
        joinedload(PenerimaanKas.creator),
        joinedload(PenerimaanKas.rincian).joinedload(PenerimaanRincian.akun_perkiraan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PenerimaanKas.no_bukti.ilike(pattern)
            | PenerimaanKas.pemberi.ilike(pattern)
            | PenerimaanKas.no_nukti.ilike(pattern)
        )

    if status:
        query = query.filter(PenerimaanKas.status == status)
    if kas_bank_id:
        query = query.filter(PenerimaanKas.kas_bank_id == kas_bank_id)
    if tanggal_from:
        query = query.filter(PenerimaanKas.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PenerimaanKas.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PenerimaanKas.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_penerimaan_by_id(db: Session, penerimaan_id: UUID) -> Optional[PenerimaanKas]:
    """Ambil 1 penerimaan berdasarkan ID dengan rincian."""
    return (
        db.query(PenerimaanKas)
        .options(
            joinedload(PenerimaanKas.kas_bank),
            joinedload(PenerimaanKas.creator),
            joinedload(PenerimaanKas.rincian).joinedload(PenerimaanRincian.akun_perkiraan),
        )
        .filter(PenerimaanKas.id == penerimaan_id)
        .first()
    )


def create_penerimaan(
    db: Session,
    no_nukti: str,
    tanggal: datetime,
    kas_bank_id: UUID,
    rincian_data: list,
    no_cek: Optional[str] = None,
    pemberi: Optional[str] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: UUID = None,
) -> PenerimaanKas:
    """Buat PenerimaanKas baru beserta rincian + auto-post jurnal."""
    try:
        no_bukti = get_nomor_dokumen(
            db, PenerimaanKas, prefix="REC",
            no_column="no_bukti", tanggal=tanggal.date()
        )

        total_nilai = sum(Decimal(str(r.get("nilai", 0))) for r in rincian_data)

        kas_bank = db.query(KasBankAkun).filter(KasBankAkun.id == kas_bank_id).first()
        if not kas_bank:
            raise ValueError(f"Kas/Bank dengan ID {kas_bank_id} tidak ditemukan")

        penerimaan = PenerimaanKas(
            no_bukti=no_bukti,
            tanggal=tanggal,
            kas_bank_id=kas_bank_id,
            no_nukti=no_nukti,
            no_cek=no_cek,
            pemberi=pemberi,
            catatan=catatan,
            total_nilai=total_nilai,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusTransaksi.SELESAI,
            created_by=created_by,
        )
        db.add(penerimaan)
        db.flush()

        for r in rincian_data:
            detail = PenerimaanRincian(
                penerimaan_id=penerimaan.id,
                akun_perkiraan_id=r["akun_perkiraan_id"],
                nilai=Decimal(str(r["nilai"])),
            )
            db.add(detail)

        if auto_post_jurnal and total_nilai > 0:
            entries = [
                # Debit: Kas/Bank
                JurnalEntryItem(
                    akun_perkiraan_id=kas_bank.akun_perkiraan_id,
                    debit=total_nilai,
                    keterangan=f"Penerimaan {no_bukti}",
                ),
            ]
            # Kredit: Akun-akun dari rincian
            for r in rincian_data:
                entries.append(
                    JurnalEntryItem(
                        akun_perkiraan_id=r["akun_perkiraan_id"],
                        kredit=Decimal(str(r["nilai"])),
                    )
                )

            jurnal = auto_posting_jurnal(
                db=db,
                ref_module=RefModule.PENERIMAAN,
                ref_no=no_bukti,
                entries=entries,
                keterangan=f"Penerimaan Kas {no_bukti}",
                ref_id=penerimaan.id,
                tanggal=tanggal,
                created_by=created_by,
            )
            penerimaan.jurnal_umum_id = jurnal.id

        db.commit()
        db.refresh(penerimaan)
        logger.info(f"PenerimaanKas created: {no_bukti} | total={total_nilai}")
        return penerimaan

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PenerimaanKas: {e}")
        raise


def update_penerimaan(
    db: Session,
    db_obj: PenerimaanKas,
    tanggal: Optional[datetime] = None,
    kas_bank_id: Optional[UUID] = None,
    no_nukti: Optional[str] = None,
    no_cek: Optional[str] = None,
    pemberi: Optional[str] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> PenerimaanKas:
    """Update data penerimaan."""
    if tanggal is not None:
        db_obj.tanggal = tanggal
    if kas_bank_id is not None:
        db_obj.kas_bank_id = kas_bank_id
    if no_nukti is not None:
        db_obj.no_nukti = no_nukti
    if no_cek is not None:
        db_obj.no_cek = no_cek
    if pemberi is not None:
        db_obj.pemberi = pemberi
    if catatan is not None:
        db_obj.catatan = catatan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def cancel_penerimaan(db: Session, db_obj: PenerimaanKas) -> PenerimaanKas:
    """Batalkan penerimaan."""
    if db_obj.status == StatusTransaksi.BATAL:
        raise ValueError("Penerimaan sudah dibatalkan")
    db_obj.status = StatusTransaksi.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenerimaanKas cancelled: {db_obj.no_bukti}")
    return db_obj


# ==========================================
# TRANSFER BANK
# ==========================================

def get_transfer_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[TransferBank], int]:
    """Ambil daftar transfer bank dengan filter & pagination."""
    query = db.query(TransferBank).options(
        joinedload(TransferBank.dari_kas_bank),
        joinedload(TransferBank.ke_kas_bank),
        joinedload(TransferBank.creator),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(TransferBank.no_transfer.ilike(pattern))

    if status:
        query = query.filter(TransferBank.status == status)
    if tanggal_from:
        query = query.filter(TransferBank.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(TransferBank.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(TransferBank.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_transfer_by_id(db: Session, transfer_id: UUID) -> Optional[TransferBank]:
    """Ambil 1 transfer berdasarkan ID."""
    return (
        db.query(TransferBank)
        .options(
            joinedload(TransferBank.dari_kas_bank),
            joinedload(TransferBank.ke_kas_bank),
            joinedload(TransferBank.creator),
        )
        .filter(TransferBank.id == transfer_id)
        .first()
    )


def create_transfer(
    db: Session,
    tanggal: datetime,
    dari_kas_bank_id: UUID,
    ke_kas_bank_id: UUID,
    nilai_transfer: Decimal,
    biaya_transfer: Decimal = Decimal("0"),
    informasi: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: UUID = None,
) -> TransferBank:
    """Buat TransferBank baru + auto-post jurnal."""
    try:
        # Validasi: dari dan ke harus berbeda
        if dari_kas_bank_id == ke_kas_bank_id:
            raise ValueError("Kas/Bank asal dan tujuan tidak boleh sama")

        no_transfer = get_nomor_dokumen(
            db, TransferBank, prefix="TRF",
            no_column="no_transfer", tanggal=tanggal.date()
        )

        # Validasi kas bank
        dari_kb = db.query(KasBankAkun).filter(KasBankAkun.id == dari_kas_bank_id).first()
        ke_kb = db.query(KasBankAkun).filter(KasBankAkun.id == ke_kas_bank_id).first()
        if not dari_kb:
            raise ValueError(f"Kas/Bank asal ID {dari_kas_bank_id} tidak ditemukan")
        if not ke_kb:
            raise ValueError(f"Kas/Bank tujuan ID {ke_kas_bank_id} tidak ditemukan")

        transfer = TransferBank(
            no_transfer=no_transfer,
            tanggal=tanggal,
            dari_kas_bank_id=dari_kas_bank_id,
            ke_kas_bank_id=ke_kas_bank_id,
            nilai_transfer=nilai_transfer,
            biaya_transfer=biaya_transfer,
            informasi=informasi,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusTransaksi.SELESAI,
            created_by=created_by,
        )
        db.add(transfer)
        db.flush()

        # Auto-post jurnal
        if auto_post_jurnal and nilai_transfer > 0:
            entries = [
                # Debit: Kas/Bank Tujuan
                JurnalEntryItem(
                    akun_perkiraan_id=ke_kb.akun_perkiraan_id,
                    debit=nilai_transfer,
                    keterangan=f"Transfer ke {ke_kb.nama}",
                ),
                # Kredit: Kas/Bank Asal
                JurnalEntryItem(
                    akun_perkiraan_id=dari_kb.akun_perkiraan_id,
                    kredit=nilai_transfer,
                    keterangan=f"Transfer dari {dari_kb.nama}",
                ),
            ]

            # Jika ada biaya transfer
            if biaya_transfer and biaya_transfer > 0:
                # TODO: Akun beban admin bisa diambil dari setting/config
                # Sementara gunakan akun dari kas bank asal (biaya keluar dari asal)
                entries.append(
                    JurnalEntryItem(
                        akun_perkiraan_id=dari_kb.akun_perkiraan_id,
                        debit=biaya_transfer,
                        keterangan=f"Biaya transfer {no_transfer}",
                    )
                )
                entries.append(
                    JurnalEntryItem(
                        akun_perkiraan_id=dari_kb.akun_perkiraan_id,
                        kredit=biaya_transfer,
                        keterangan=f"Biaya transfer {no_transfer}",
                    )
                )

            jurnal = auto_posting_jurnal(
                db=db,
                ref_module=RefModule.TRANSFER_BANK,
                ref_no=no_transfer,
                entries=entries,
                keterangan=f"Transfer Bank {no_transfer}",
                ref_id=transfer.id,
                tanggal=tanggal,
                created_by=created_by,
            )
            transfer.jurnal_umum_id = jurnal.id

        db.commit()
        db.refresh(transfer)
        logger.info(f"TransferBank created: {no_transfer} | nilai={nilai_transfer}")
        return transfer

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating TransferBank: {e}")
        raise


def update_transfer(
    db: Session,
    db_obj: TransferBank,
    tanggal: Optional[datetime] = None,
    dari_kas_bank_id: Optional[UUID] = None,
    ke_kas_bank_id: Optional[UUID] = None,
    nilai_transfer: Optional[Decimal] = None,
    biaya_transfer: Optional[Decimal] = None,
    informasi: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> TransferBank:
    """Update data transfer."""
    if tanggal is not None:
        db_obj.tanggal = tanggal
    if dari_kas_bank_id is not None:
        if dari_kas_bank_id == db_obj.ke_kas_bank_id:
            raise ValueError("Kas/Bank asal dan tujuan tidak boleh sama")
        db_obj.dari_kas_bank_id = dari_kas_bank_id
    if ke_kas_bank_id is not None:
        if ke_kas_bank_id == db_obj.dari_kas_bank_id:
            raise ValueError("Kas/Bank asal dan tujuan tidak boleh sama")
        db_obj.ke_kas_bank_id = ke_kas_bank_id
    if nilai_transfer is not None:
        db_obj.nilai_transfer = nilai_transfer
    if biaya_transfer is not None:
        db_obj.biaya_transfer = biaya_transfer
    if informasi is not None:
        db_obj.informasi = informasi
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def cancel_transfer(db: Session, db_obj: TransferBank) -> TransferBank:
    """Batalkan transfer."""
    if db_obj.status == StatusTransaksi.BATAL:
        raise ValueError("Transfer sudah dibatalkan")
    db_obj.status = StatusTransaksi.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"TransferBank cancelled: {db_obj.no_transfer}")
    return db_obj
