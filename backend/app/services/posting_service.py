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
    organization: Optional[dict] = None,
    is_manual: bool = False,
) -> JurnalUmum:
    """
    Membuat Jurnal Umum otomatis beserta detailnya (double-entry).

    Parameter:
        db: SQLAlchemy Session
        ref_module: enum RefModule (SALES_INVOICE, PEMBAYARAN, dll)
        ref_no: Nomor dokumen sumber (misal INV-2026-08-001)
        entries: List of JurnalEntryItem -- baris-baris jurnal (debit & kredit)
        keterangan: Keterangan umum jurnal
        ref_id: UUID dokumen sumber (opsional)
        tanggal: Tanggal jurnal (default: sekarang)
        created_by: UUID user yang membuat
        tipe_transaksi: Tipe transaksi (opsional, untuk pelacakan)
        status: Status jurnal (default: POSTED)
        no_jurnal: Nomor jurnal (jika None, akan digenerate otomatis)
        allow_inactive_accounts: True untuk bypass active check (dipakai
            reversal jurnal lama supaya bisa membaca akun yang sudah
            inactive post-migration). Default False.
        organization: Snapshot org unit (company/branch/dll).
        is_manual: True kalau jurnal ini user-initiated manual journal
            (RefModule.MANUAL). False kalau auto-posting dari modul sistem.
            Dipakai untuk enforce control account rule: control account
            (AR/AP/INVENTORY) tidak boleh diposting manual, hanya sistem.

    Return:
        JurnalUmum object (flushed, belum committed — caller harus commit)

    Validasi baru (ASAHI COA Revisi v2):
    - Akun control account (is_control_account=True) tidak boleh dipakai
      jurnal manual (is_manual=True). Hanya sistem yang boleh posting.
    - Akun legacy-locked (allow_system_posting=False AND
      allow_manual_posting=False, active=True) tidak boleh dipakai
      transaksi baru (kecuali allow_inactive_accounts=True untuk
      reversal histori).
    - Akun system account (system_account_type di-set, posting rules False)
      tidak boleh dipakai transaksi sama sekali — nilai di-generate sistem.
    """
    try:
        from app.services.accounting_control import accounting_lock
        accounting_lock(db)
        from app.services.reporting_ledger import local_datetime
        tanggal = local_datetime(tanggal or datetime.now(timezone.utc))
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

        total_debit, total_kredit = validate_entries(
            db, entries, allow_inactive_accounts=allow_inactive_accounts,
            is_manual=is_manual,
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
        from app.services.organization_service import for_source, validate
        organization = organization if organization is not None else validate(db, for_source(db, ref_id))
        jurnal = JurnalUmum(
            **organization,
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
    from app.models.transaksi.asset_event import AssetEvent
    if db.query(AssetEvent).filter(AssetEvent.source_journal_id == journal_id, AssetEvent.status != 'BATAL').first():
        raise ValueError('Jurnal digunakan oleh registrasi aset. Batalkan transaksi aset terkait terlebih dahulu.')
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
        organization={field: getattr(original, field) for field in ("company_id", "branch_id", "department_id", "cost_center_id", "project_id")},
    )
    reversal.reversal_of_id = original.id
    db.flush()
    return reversal


def validate_entries(db, entries, allow_inactive_accounts=False, is_manual=False):
    """Validasi baris-baris jurnal sebelum diposting.

    Parameter:
        db: SQLAlchemy Session
        entries: List of JurnalEntryItem
        allow_inactive_accounts: True untuk bypass active check. Dipakai
            reversal jurnal lama (supaya akun yang inactive post-migration
            tetap bisa di-reverse). Default False.
        is_manual: True kalau jurnal ini dibuat user manual (bukan auto-posting
            modul sistem). Default False. Dipakai untuk enforce rule:
            control account tidak boleh dipakai jurnal manual.

    Validasi (ASAHI COA Revisi v2):
    1. entries minimal 2 baris (debit & kredit)
    2. Setiap baris: debit/kredit angka valid & tidak negatif
    3. Setiap baris: debit atau kredit (bukan keduanya, bukan 0)
    4. Akun harus DETAIL (bukan HEADER/GROUP)
    5. Akun harus active (kecuali allow_inactive_accounts=True)
    6. Akun legacy-locked (allow_*_posting=False, active=True) tidak boleh
       dipakai transaksi baru (kecuali allow_inactive_accounts=True untuk
       reversal)
    7. Akun control account (is_control_account=True) tidak boleh dipakai
       jurnal manual (is_manual=True) — hanya sistem yang boleh posting
    8. Akun system_account (system_account_type di-set, posting rules False)
       tidak boleh dipakai transaksi sama sekali (kecuali reversal via
       allow_inactive_accounts=True)
    9. Total debit == total kredit (balanced)
    """
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

        # === Rule 5: active check ===
        if not allow_inactive_accounts and account.active is False:
            raise ValueError(
                f"Akun {account.kode} ({account.nama}) sudah inactive (active=False). "
                "Tidak bisa dipakai transaksi baru."
            )
        # Legacy `status` field juga dicek untuk backward compat (kalau
        # migration v2 belum dijalankan, account.active mungkin None).
        if not allow_inactive_accounts and account.status not in (None, "AKTIF"):
            raise ValueError(f"Akun {account.kode} tidak aktif")

        # === Rule 6: legacy-locked check ===
        # Bypass kalau allow_inactive_accounts=True (reversal histori)
        if not allow_inactive_accounts and account.is_legacy_locked:
            raise ValueError(
                f"Akun {account.kode} ({account.nama}) adalah akun LEGACY yang "
                "dikunci untuk transaksi baru. Tidak bisa dipakai posting. "
                "Pilih akun lain (misal HPP Produk Jadi 531001 untuk HPP, atau "
                "Persediaan 114xxx untuk pembelian)."
            )

        # === Rule 7: control account manual-post ban ===
        if is_manual and account.is_control_account and not account.allow_manual_posting:
            raise ValueError(
                f"Akun {account.kode} ({account.nama}) adalah CONTROL ACCOUNT "
                f"({account.subledger_type or account.system_account_type}). "
                "Tidak bisa dipakai jurnal manual — hanya sistem yang boleh "
                "posting ke akun ini. Gunakan modul transaksi yang sesuai "
                "(Penjualan/Pembelian/Kas-Bank/Persediaan)."
            )

        # === Rule 8: system account ban ===
        if not allow_inactive_accounts and account.is_system_account:
            raise ValueError(
                f"Akun {account.kode} ({account.nama}) adalah SYSTEM ACCOUNT "
                f"({account.system_account_type}). Nilai akun ini di-generate "
                "oleh sistem (perhitungan laba rugi / closing periode), tidak "
                "boleh diposting transaksi langsung."
            )

    # Validasi: pastikan total debit == total kredit (balanced)
    total_debit = sum(e.debit for e in entries)
    total_kredit = sum(e.kredit for e in entries)
    if total_debit != total_kredit:
        raise ValueError(
            f"Jurnal tidak balance: total debit={total_debit}, total_kredit={total_kredit}"
        )

    return total_debit, total_kredit
