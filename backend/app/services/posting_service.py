"""
posting_service.py

Service untuk auto-posting jurnal umum dari berbagai modul transaksi.
Digunakan oleh modul Kas/Bank, Penjualan, Pembelian, Persediaan, dan Aset Tetap.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun

from app.models.transaksi.jurnal import JurnalUmum, RefModule, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail
from app.models.transaksi.penutupan_periode import PenutupanPeriode, StatusPeriode


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


def _generate_no_jurnal(db: Session, prefix: str, tanggal: datetime) -> str:
    """Generate nomor jurnal auto-increment per bulan.
    Format: JV-YYYYMM-NNN
    """
    year_month = tanggal.strftime("%Y%m")
    pattern = f"{prefix}-{year_month}%"

    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                   {"key": f"journal-number:{prefix}:{year_month}"})
    numbers = (
        db.query(JurnalUmum.no_jurnal)
        .filter(JurnalUmum.no_jurnal.like(pattern))
        .all()
    )
    next_num = max((int(row[0].rsplit("-", 1)[-1]) for row in numbers
                    if row[0].rsplit("-", 1)[-1].isdigit()), default=0) + 1

    return f"{prefix}-{year_month}-{next_num:03d}"


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
    allow_inactive_accounts: bool = False,
) -> JurnalUmum:
    """
    Membuat Jurnal Umum otomatis beserta detailnya (double-entry).

    Parameter:
        db: SQLAlchemy Session
        ref_module: Enum RefModule (SALES_INVOICE, PEMBAYARAN, dll)
        ref_no: Nomor dokumen sumber (misal INV-2026-08-001)
        entries: List of JurnalEntryItem -- baris-baris jurnal (debit & kredit)
        keterangan: Keterangan umum jurnal
        ref_id: UUID dokumen sumber (opsional)
        tanggal: Tanggal jurnal (default: sekarang)
        created_by: UUID user yang membuat
        tipe_transaksi: Tipe transaksi (opsional, untuk pelacakan)
        status: Status jurnal (default: POSTED)
        no_jurnal: Nomor jurnal (jika None, akan digenerate otomatis)

    Return:
        JurnalUmum object (flushed, belum committed — caller harus commit)
    """
    try:
        tanggal = tanggal or datetime.now(timezone.utc)
        # Validasi: cek periode tidak ditutup (inline query untuk menghindari circular import)
        if tanggal:
            _periode_closed = (
                db.query(PenutupanPeriode)
                .filter(
                    PenutupanPeriode.tahun == tanggal.year,
                    PenutupanPeriode.bulan == tanggal.month,
                    PenutupanPeriode.status == StatusPeriode.DITUTUP.value,
                )
                .first()
            )
            if _periode_closed:
                raise ValueError(
                    f"Periode {tanggal.strftime('%B %Y')} sudah ditutup. "
                    f"Tidak bisa posting jurnal ({ref_no})."
                )

        # Validasi: pastikan entries tidak kosong
        if len(entries) < 2:
            raise ValueError("entries tidak boleh kosong, minimal 2 baris (debit & kredit)")

        for entry in entries:
            for field in ("debit", "kredit"):
                value = Decimal(str(getattr(entry, field)))
                if not value.is_finite() or value < 0:
                    raise ValueError("Nilai debit/kredit harus angka valid dan tidak negatif")
                setattr(entry, field, value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if (entry.debit > 0) == (entry.kredit > 0):
                raise ValueError("Setiap baris jurnal harus berisi debit atau kredit, bukan keduanya/nol")
            account = db.get(AkunPerkiraan, entry.akun_perkiraan_id)
            if account is None or account.tingkat != TingkatAkun.DETAIL:
                raise ValueError("Jurnal hanya boleh memakai akun DETAIL yang tersedia")
            if not allow_inactive_accounts and account.status != "AKTIF":
                raise ValueError(f"Akun {account.kode} tidak aktif")

        # Validasi: pastikan total debit == total kredit (balanced)
        total_debit = sum(e.debit for e in entries)
        total_kredit = sum(e.kredit for e in entries)
        if total_debit != total_kredit:
            raise ValueError(
                f"Jurnal tidak balance: total debit={total_debit}, total_kredit={total_kredit}"
            )

        # Default tanggal
        if tanggal is None:
            tanggal = datetime.now(timezone.utc)

        if ref_id is not None:
            posting_type = tipe_transaksi or ref_module.value
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                           {"key": f"posting:{ref_module.value}:{ref_id}:{posting_type}"})
            existing = db.query(JurnalUmum.id).filter(
                JurnalUmum.ref_module == ref_module, JurnalUmum.ref_id == ref_id,
                JurnalUmum.tipe_transaksi == posting_type,
            ).first()
            if existing:
                raise ValueError("Dokumen sumber sudah memiliki jurnal untuk jenis posting ini")

        # Generate nomor jurnal jika tidak diberikan
        if no_jurnal is None:
            no_jurnal = _generate_no_jurnal(db, "JV", tanggal)

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
        db.flush()  # jurnal.id (default=uuid.uuid4) baru terisi setelah flush,
                    # harus di-flush dulu sebelum dipakai sebagai FK di JurnalDetail

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

        # Flush lagi agar detail ikut ke DB (bukan commit) agar jurnal.id tersedia untuk caller.
        # Caller bertanggung jawab atas commit/rollback transaksi.
        # Ini mencegah rollback di sini menghancurkan transaksi parent
        # saat caller menandai jurnal gagal sebagai "non-fatal".
        db.flush()
        db.refresh(jurnal)

        logger.info(
            f"Jurnal prepared: {jurnal.no_jurnal} | ref={ref_no} | "
            f"D={total_debit} K={total_kredit} | {len(entries)} details"
        )
        return jurnal

    except Exception as e:
        # Tidak ada db.rollback() di sini — caller bertanggung jawab.
        # Hanya log error dan re-raise agar caller bisa memutuskan.
        logger.error(f"Error auto-posting jurnal: {e}")
        raise


def reverse_journal(db: Session, journal_id: UUID, user_id: UUID, reason: str = "Pembatalan") -> JurnalUmum:
    """Create one linked reversal on the original date; caller commits atomically.

    Closed periods must be reopened through the authorized period workflow.
    The original journal and its POSTED status remain intact for audit.
    """
    original = (db.query(JurnalUmum).filter(JurnalUmum.id == journal_id)
                .populate_existing().with_for_update().one())
    if original.reversal_of_id:
        raise ValueError("Jurnal pembalik tidak dapat dibalik melalui pembatalan dokumen")
    previous = db.query(JurnalUmum).filter(JurnalUmum.reversal_of_id == journal_id).first()
    if previous:
        return previous
    if original.status != StatusJurnal.POSTED:
        raise ValueError("Jurnal sumber belum POSTED")
    reversal = auto_posting_jurnal(
        db=db, ref_module=RefModule.MANUAL, ref_no=original.no_jurnal,
        ref_id=original.id, tanggal=original.tanggal, created_by=user_id,
        tipe_transaksi="REVERSAL", keterangan=f"{reason}: {original.no_jurnal}",
        entries=[JurnalEntryItem(d.akun_perkiraan_id, debit=d.kredit, kredit=d.debit,
                                keterangan=f"Pembalik {original.no_jurnal}") for d in original.details],
        allow_inactive_accounts=True,
    )
    reversal.reversal_of_id = original.id
    db.flush()
    return reversal
