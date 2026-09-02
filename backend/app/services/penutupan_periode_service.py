import calendar
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from app.models.transaksi.penutupan_periode import PenutupanPeriode, StatusPeriode
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal, RefModule
from app.models.detail.jurnal_detail import JurnalDetail
from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun, SaldoNormal
from app.services.posting_service import JurnalEntryItem, auto_posting_jurnal
from app.services import setting_akun_service as sa_cfg
from app.services.laporan_service import _saldo_per_akun_list


# ============================================================
# HELPERS
# ============================================================

def _get_periode_bounds(tahun: int, bulan: int) -> Tuple[datetime, datetime]:
    """Return (awal_bulan, akhir_bulan) datetime untuk periode tertentu."""
    _, last_day = calendar.monthrange(tahun, bulan)
    date_from = datetime(tahun, bulan, 1, 0, 0, 0)
    date_to = datetime(tahun, bulan, last_day, 23, 59, 59)
    return date_from, date_to


def is_periode_closed(db: Session, tanggal: datetime) -> bool:
    """Cek apakah tanggal jatuh di periode yang sudah ditutup."""
    tahun = tanggal.year
    bulan = tanggal.month
    record = (
        db.query(PenutupanPeriode)
        .filter(
            PenutupanPeriode.tahun == tahun,
            PenutupanPeriode.bulan == bulan,
            PenutupanPeriode.status == StatusPeriode.DITUTUP.value,
        )
        .first()
    )
    return record is not None


def validate_periode_not_closed(db: Session, tanggal: datetime, context: str = ""):
    """Raise ValueError jika tanggal jatuh di periode yang sudah ditutup."""
    if is_periode_closed(db, tanggal):
        ctx = f" ({context})" if context else ""
        raise ValueError(
            f"Periode {tanggal.strftime('%B %Y')} sudah ditutup. "
            f"Tidak bisa melakukan posting.{ctx}"
        )


# ============================================================
# LIST
# ============================================================

def get_periode_list(
    db: Session,
    tahun: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Tuple[List[PenutupanPeriode], int]:
    """Ambil daftar penutupan periode dengan filter & pagination."""
    query = db.query(PenutupanPeriode).options(
        joinedload(PenutupanPeriode.closer),
        joinedload(PenutupanPeriode.reopener),
        joinedload(PenutupanPeriode.jurnal),
    )

    if tahun:
        query = query.filter(PenutupanPeriode.tahun == tahun)
    if status:
        query = query.filter(PenutupanPeriode.status == status)

    total = query.count()
    data = query.order_by(PenutupanPeriode.tahun.desc(), PenutupanPeriode.bulan.desc()).offset(skip).limit(limit).all()
    return data, total


# ============================================================
# TUTUP PERIODE
# ============================================================

def tutup_periode(
    db: Session,
    tahun: int,
    bulan: int,
    user_id: UUID,
    keterangan: Optional[str] = None,
    with_closing_entry: bool = True,
) -> PenutupanPeriode:
    """Tutup periode (tahun/bulan).

    1. Validasi: bulan 1-12, bukan bulan sekarang (harus sudah lewat)
    2. Validasi: periode belum ditutup
    3. Hitung laba/rugi periode
    4. (Opsional) Buat jurnal penutupan: zero-out PENDAPATAN/HPP/BEBAN, net ke LABA_RUGI_BERJALAN
       ⚠️  Jurnal HARUS dibuat SEBELUM status di-set DITUTUP, karena
          auto_posting_jurnal() punya period guard yang menolak posting ke periode tertutup.
    5. Set status DITUTUP & simpan record penutupan
    6. Commit
    """
    # Validasi bulan
    if bulan < 1 or bulan > 12:
        raise ValueError(f"Bulan harus 1-12, diberikan: {bulan}")

    now = datetime.now(timezone.utc)
    current_year = now.year
    current_month = now.month

    # Tidak bisa tutup bulan sekarang atau bulan depan
    if tahun > current_year or (tahun == current_year and bulan >= current_month):
        raise ValueError(
            f"Tidak bisa menutup periode yang belum berakhir. "
            f"Periode {tahun}-{bulan:02d}, sekarang {current_year}-{current_month:02d}."
        )

    # Cek apakah sudah ada record untuk periode ini
    existing = (
        db.query(PenutupanPeriode)
        .filter(PenutupanPeriode.tahun == tahun, PenutupanPeriode.bulan == bulan)
        .first()
    )
    if existing and existing.status == StatusPeriode.DITUTUP.value:
        raise ValueError(f"Periode {tahun}-{bulan:02d} sudah ditutup.")

    # Hitung batas periode
    date_from, date_to = _get_periode_bounds(tahun, bulan)

    # Hitung laba/rugi: PENDAPATAN - HPP - BEBAN
    pendapatan = _saldo_per_akun_list(db, HeaderCOA.PENDAPATAN, date_from, date_to)
    hpp = _saldo_per_akun_list(db, HeaderCOA.HPP, date_from, date_to)
    beban = _saldo_per_akun_list(db, HeaderCOA.BEBAN, date_from, date_to)

    total_pendapatan = sum((i["total"] for i in pendapatan), Decimal("0"))
    total_hpp = sum((i["total"] for i in hpp), Decimal("0"))
    total_beban = sum((i["total"] for i in beban), Decimal("0"))

    laba_rugi = total_pendapatan - total_hpp - total_beban

    # ⚠️  STEP 4a: Buat jurnal penutupan DULU, sebelum status DITUTUP.
    #     auto_posting_jurnal() punya period guard yang menolak posting
    #     ke periode yang sudah DITUTUP. Jika kita set DITUTUP dulu,
    #     jurnal penutupan akan selalu gagal.
    jurnal_penutupan_id = None
    if with_closing_entry:
        try:
            jurnal_penutupan_id = _create_closing_entry(
                db=db,
                tahun=tahun,
                bulan=bulan,
                date_from=date_from,
                date_to=date_to,
                laba_rugi=laba_rugi,
                pendapatan=pendapatan,
                hpp=hpp,
                beban=beban,
                user_id=user_id,
            )
        except Exception as e:
            logger.warning(f"Jurnal penutupan gagal dibuat (non-fatal): {e}")

    # STEP 4b: Baru set status DITUTUP
    if existing:
        pp = existing
        pp.status = StatusPeriode.DITUTUP.value
        pp.laba_rugi = laba_rugi
        pp.keterangan = keterangan
        pp.closed_by = user_id
        pp.closed_at = now
        pp.reopened_by = None
        pp.reopened_at = None
        if jurnal_penutupan_id:
            pp.jurnal_penutupan_id = jurnal_penutupan_id
    else:
        pp = PenutupanPeriode(
            tahun=tahun,
            bulan=bulan,
            status=StatusPeriode.DITUTUP.value,
            laba_rugi=laba_rugi,
            keterangan=keterangan,
            closed_by=user_id,
            closed_at=now,
            created_by=user_id,
            jurnal_penutupan_id=jurnal_penutupan_id,
        )
        db.add(pp)
        db.flush()

    db.commit()
    db.refresh(pp)
    logger.info(
        f"Periode {tahun}-{bulan:02d} ditutup | laba_rugi={laba_rugi} | "
        f"closing_entry={'YA' if jurnal_penutupan_id else 'TIDAK'}"
    )
    return pp


def _create_closing_entry(
    db: Session,
    tahun: int,
    bulan: int,
    date_from: datetime,
    date_to: datetime,
    laba_rugi: Decimal,
    pendapatan: List[dict],
    hpp: List[dict],
    beban: List[dict],
    user_id: UUID,
) -> Optional[UUID]:
    """Buat jurnal penutupan: zero-out PENDAPATAN/HPP/BEBAN, net ke LABA_RUGI_BERJALAN.

    Logic:
    - PENDAPATAN (kredit-normal): D-akun pendapatan (mengurangi) → total = kredit
    - HPP (debit-normal): K-akun HPP (mengurangi) → total = debit
    - BEBAN (debit-normal): K-akun BEBAN (mengurangi) → total = debit
    - LABA_RUGI_BERJALAN (modal, kredit-normal):
        - Jika laba (laba_rugi > 0): K-LabaRugiBerjalan (menambah laba)
        - Jika rugi (laba_rugi < 0): D-LabaRugiBerjalan (menambah rugi)

    Return: UUID jurnal_penutupan_id, atau None jika tidak ada entry yang perlu dibuat.
    """
    entries: List[JurnalEntryItem] = []

    # 1. Zero-out PENDAPATAN (kredit-normal → debit untuk mengurangi)
    for item in pendapatan:
        if item["total"] > 0:
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=_get_akun_id_by_kode(db, item["kode_akun"]),
                debit=item["total"],
                keterangan=f"Tutup {item['nama_akun']}",
            ))
        elif item["total"] < 0:
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=_get_akun_id_by_kode(db, item["kode_akun"]),
                kredit=abs(item["total"]),
                keterangan=f"Tutup {item['nama_akun']}",
            ))

    # 2. Zero-out HPP (debit-normal → kredit untuk mengurangi)
    for item in hpp:
        if item["total"] > 0:
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=_get_akun_id_by_kode(db, item["kode_akun"]),
                kredit=item["total"],
                keterangan=f"Tutup {item['nama_akun']}",
            ))
        elif item["total"] < 0:
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=_get_akun_id_by_kode(db, item["kode_akun"]),
                debit=abs(item["total"]),
                keterangan=f"Tutup {item['nama_akun']}",
            ))

    # 3. Zero-out BEBAN (debit-normal → kredit untuk mengurangi)
    for item in beban:
        if item["total"] > 0:
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=_get_akun_id_by_kode(db, item["kode_akun"]),
                kredit=item["total"],
                keterangan=f"Tutup {item['nama_akun']}",
            ))
        elif item["total"] < 0:
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=_get_akun_id_by_kode(db, item["kode_akun"]),
                debit=abs(item["total"]),
                keterangan=f"Tutup {item['nama_akun']}",
            ))

    if not entries and laba_rugi == 0:
        logger.info(f"Tidak ada jurnal penutupan untuk {tahun}-{bulan:02d} (semua saldo P&L = 0)")
        return None

    # 4. Net ke LABA_RUGI_BERJALAN
    akun_lrb_id = sa_cfg.get_akun_id_or_raise(db, sa_cfg.KEY_LABA_RUGI_BERJALAN, f"Penutupan {tahun}-{bulan:02d}")
    if laba_rugi > 0:
        # Laba: kredit LabaRugiBerjalan
        entries.append(JurnalEntryItem(
            akun_perkiraan_id=akun_lrb_id,
            kredit=laba_rugi,
            keterangan=f"Laba periode {tahun}-{bulan:02d}",
        ))
    elif laba_rugi < 0:
        # Rugi: debit LabaRugiBerjalan
        entries.append(JurnalEntryItem(
            akun_perkiraan_id=akun_lrb_id,
            debit=abs(laba_rugi),
            keterangan=f"Rugi periode {tahun}-{bulan:02d}",
        ))

    # 5. Post jurnal penutupan (tanggal = akhir bulan)
    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.PENUTUPAN_PERIODE,
        ref_no=f"CL-{tahun}-{bulan:02d}",
        entries=entries,
        keterangan=f"Jurnal Penutupan Periode {tahun}-{bulan:02d} | Laba/Rugi: {laba_rugi}",
        tanggal=date_to,
        created_by=user_id,
        status=StatusJurnal.POSTED,
    )
    logger.info(f"Jurnal penutupan posted: {jurnal.no_jurnal} | {len(entries)} details")
    return jurnal.id


def _get_akun_id_by_kode(db: Session, kode: str) -> UUID:
    """Cari UUID AkunPerkiraan berdasarkan kode."""
    akun = db.query(AkunPerkiraan).filter(AkunPerkiraan.kode == kode).first()
    if not akun:
        raise ValueError(f"Akun dengan kode {kode} tidak ditemukan")
    return akun.id


# ============================================================
# BUKA PERIODE
# ============================================================

def buka_periode(
    db: Session,
    tahun: int,
    bulan: int,
    user_id: UUID,
    alasan: Optional[str] = None,
) -> PenutupanPeriode:
    """Buka kembali periode yang sudah ditutup.

    Catatan: Jurnal penutupan TIDAK dihapus (untuk audit trail).
    Pengguna bisa membuat jurnal balik manual jika diperlukan.
    """
    if bulan < 1 or bulan > 12:
        raise ValueError(f"Bulan harus 1-12, diberikan: {bulan}")

    pp = (
        db.query(PenutupanPeriode)
        .filter(
            PenutupanPeriode.tahun == tahun,
            PenutupanPeriode.bulan == bulan,
            PenutupanPeriode.status == StatusPeriode.DITUTUP.value,
        )
        .first()
    )
    if not pp:
        raise ValueError(f"Periode {tahun}-{bulan:02d} tidak ditemukan atau tidak dalam status DITUTUP.")

    now = datetime.now(timezone.utc)
    pp.status = StatusPeriode.DIBUKA.value
    pp.reopened_by = user_id
    pp.reopened_at = now
    if alasan:
        pp.keterangan = f"[DIBUKA] {alasan}"

    db.commit()
    db.refresh(pp)
    logger.info(f"Periode {tahun}-{bulan:02d} dibuka kembali oleh user {user_id}")
    return pp


# ============================================================
# CHECK STATUS
# ============================================================

def get_periode_status(db: Session, tahun: int, bulan: int) -> Optional[dict]:
    """Cek status penutupan suatu periode."""
    pp = (
        db.query(PenutupanPeriode)
        .filter(PenutupanPeriode.tahun == tahun, PenutupanPeriode.bulan == bulan)
        .first()
    )
    if not pp:
        return {"tahun": tahun, "bulan": bulan, "status": "TERBUKA", "laba_rugi": None}
    return {
        "tahun": pp.tahun,
        "bulan": pp.bulan,
        "status": pp.status,
        "laba_rugi": pp.laba_rugi,
    }
