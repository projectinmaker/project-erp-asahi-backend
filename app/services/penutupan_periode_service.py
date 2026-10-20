from app.services.accounting_control import atomic_accounting_write
from app.services.posting_service import reverse_journal
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
    from app.services.reporting_ledger import month_bounds
    return month_bounds(tahun, bulan)


def is_periode_closed(db: Session, tanggal: datetime, company_id=None) -> bool:
    """Cek apakah tanggal jatuh di periode yang sudah ditutup (company-aware).

    AUD-17 (update #05): penutupan bisa GLOBAL (company_id NULL → mengunci
    semua perusahaan) atau khusus satu perusahaan. Aturan:
    - ``company_id`` terisi (jurnal/dokumen milik perusahaan C) → terkunci
      bila ada baris DITUTUP utk (tahun, bulan, C) ATAU (tahun, bulan, NULL).
    - ``company_id`` None (tanpa perusahaan) → hanya terkunci baris global
      (tahun, bulan, NULL).
    """
    from app.services.reporting_ledger import local_datetime
    tanggal = local_datetime(tanggal)
    tahun = tanggal.year
    bulan = tanggal.month
    query = db.query(PenutupanPeriode).filter(
        PenutupanPeriode.tahun == tahun,
        PenutupanPeriode.bulan == bulan,
        PenutupanPeriode.status == StatusPeriode.DITUTUP.value,
    )
    if company_id is not None:
        from sqlalchemy import or_
        query = query.filter(or_(
            PenutupanPeriode.company_id == company_id,
            PenutupanPeriode.company_id.is_(None),
        ))
    else:
        query = query.filter(PenutupanPeriode.company_id.is_(None))
    return query.first() is not None


# Sentinel: company tidak diberikan eksplisit → resolve dari konteks posting
# aktif (db.info['posting_company'], di-set workflow/decorator saat posting).
_AUTO = object()


def resolve_company(db: Session, company_id=_AUTO):
    if company_id is _AUTO:
        company_id = db.info.get('posting_company') if db is not None else None
    return company_id


def validate_periode_not_closed(db: Session, tanggal: datetime, context: str = "", company_id=_AUTO):
    """Raise ValueError jika tanggal jatuh di periode yang sudah ditutup.

    AUD-17: ``company_id`` eksplisit None = cek global saja; tanpa argumen
    (AUTO) = resolve dari konteks posting ``db.info['posting_company']``.
    """
    company = resolve_company(db, company_id)
    if is_periode_closed(db, tanggal, company):
        ctx = f" ({context})" if context else ""
        ctxt = "" if company is None else f" (perusahaan {company})"
        raise ValueError(
            f"Periode {tanggal.strftime('%B %Y')} sudah ditutup{ctxt}. "
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
    company_id: Optional[UUID] = None,
) -> Tuple[List[PenutupanPeriode], int]:
    """Ambil daftar penutupan periode dengan filter & pagination.

    AUD-17: ``company_id`` terisi → hanya baris milik perusahaan tsb (plus
    baris global bila None berarti tampilkan semua). Kosong → semua baris.
    """
    query = db.query(PenutupanPeriode).options(
        joinedload(PenutupanPeriode.closer),
        joinedload(PenutupanPeriode.reopener),
        joinedload(PenutupanPeriode.jurnal),
    )

    if tahun:
        query = query.filter(PenutupanPeriode.tahun == tahun)
    if status:
        query = query.filter(PenutupanPeriode.status == status)
    if company_id is not None:
        from sqlalchemy import or_
        query = query.filter(or_(PenutupanPeriode.company_id == company_id,
                                 PenutupanPeriode.company_id.is_(None)))

    total = query.count()
    data = query.order_by(PenutupanPeriode.tahun.desc(), PenutupanPeriode.bulan.desc()).offset(skip).limit(limit).all()
    return data, total


# ============================================================
# TUTUP PERIODE
# ============================================================

def _validate_company_unit(db: Session, company_id: Optional[UUID]) -> None:
    """AUD-17: pastikan unit company valid (kind COMPANY, AKTIF)."""
    if company_id is None:
        return
    from app.models.organization import OrganizationUnit
    unit = db.get(OrganizationUnit, company_id)
    if unit is None or unit.kind != 'COMPANY':
        raise ValueError('Perusahaan (companyId) tidak ditemukan atau bukan jenis COMPANY')
    if unit.status != 'AKTIF':
        raise ValueError('Perusahaan (companyId) tidak aktif')


def _company_scope_filter(query, model, company_id: Optional[UUID]):
    """Filter baris milik scope company: exact (C) untuk operasi scope C;
    untuk scope global (None) hanya baris global."""
    if company_id is not None:
        return query.filter(model.company_id == company_id)
    return query.filter(model.company_id.is_(None))


@atomic_accounting_write
def tutup_periode(
    db: Session,
    tahun: int,
    bulan: int,
    user_id: UUID,
    keterangan: Optional[str] = None,
    with_closing_entry: bool = True,
    company_id: Optional[UUID] = None,
) -> PenutupanPeriode:
    """Tutup periode (tahun/bulan) — global (company NULL) atau per perusahaan.

    AUD-17 (update #05): ``company_id`` terisi → menutup buku khusus
    perusahaan tsb (jurnal penutupan & laba/rugi dihitung hanya dari jurnal
    perusahaan itu); NULL → perilaku lama (global, semua dimensi).

    1. Validasi: bulan 1-12, bukan bulan sekarang (harus sudah lewat)
    2. Validasi: periode belum ditutup (scope company atau global)
    3. Hitung laba/rugi periode (scoped)
    4. (Opsional) Buat jurnal penutupan: zero-out PENDAPATAN/HPP/BEBAN, net ke LABA_RUGI_BERJALAN
       ⚠️  Jurnal HARUS dibuat SEBELUM status di-set DITUTUP, karena
          auto_posting_jurnal() punya period guard yang menolak posting ke periode tertutup.
    5. Set status DITUTUP & simpan record penutupan
    6. Commit
    """
    # Validasi bulan
    if bulan < 1 or bulan > 12:
        raise ValueError(f"Bulan harus 1-12, diberikan: {bulan}")

    _validate_company_unit(db, company_id)

    from app.services.reporting_ledger import JAKARTA
    now = datetime.now(JAKARTA)
    current_year = now.year
    current_month = now.month

    # Tidak bisa tutup bulan sekarang atau bulan depan
    if tahun > current_year or (tahun == current_year and bulan >= current_month):
        raise ValueError(
            f"Tidak bisa menutup periode yang belum berakhir. "
            f"Periode {tahun}-{bulan:02d}, sekarang {current_year}-{current_month:02d}."
        )

    # Cek apakah sudah ada record untuk periode ini (scope company)
    existing = _company_scope_filter(
        db.query(PenutupanPeriode).filter(
            PenutupanPeriode.tahun == tahun, PenutupanPeriode.bulan == bulan),
        PenutupanPeriode, company_id).first()
    if existing and existing.status == StatusPeriode.DITUTUP.value:
        raise ValueError(f"Periode {tahun}-{bulan:02d} sudah ditutup.")
    if company_id is not None:
        global_closed = db.query(PenutupanPeriode).filter(
            PenutupanPeriode.tahun == tahun, PenutupanPeriode.bulan == bulan,
            PenutupanPeriode.company_id.is_(None),
            PenutupanPeriode.status == StatusPeriode.DITUTUP.value).first()
        if global_closed:
            raise ValueError(
                f"Periode {tahun}-{bulan:02d} sudah ditutup GLOBAL (mengunci semua "
                f"perusahaan) — penutupan perusahaan ini tidak diperlukan."
            )

    # Hitung batas periode
    date_from, date_to = _get_periode_bounds(tahun, bulan)

    # AUD-17: scope komputasi (laba/rugi + jurnal penutupan) per perusahaan
    # via report_scope; konteks posting_company untuk lookup setting akun.
    previous_scope = db.info.get('report_scope')
    db.info['posting_company'] = company_id
    if company_id is not None:
        db.info['report_scope'] = {'company_id': company_id}
    try:
        # Hitung laba/rugi: PENDAPATAN - HPP - BEBAN (scoped)
        pendapatan = _saldo_per_akun_list(db, HeaderCOA.PENDAPATAN, date_from, date_to)
        hpp = _saldo_per_akun_list(db, HeaderCOA.HPP, date_from, date_to)
        beban = _saldo_per_akun_list(db, HeaderCOA.BEBAN, date_from, date_to)

        total_pendapatan = sum((i["total"] for i in pendapatan), Decimal("0"))
        total_hpp = sum((i["total"] for i in hpp), Decimal("0"))
        total_beban = sum((i["total"] for i in beban), Decimal("0"))

        laba_rugi = total_pendapatan - total_hpp - total_beban

        # ⚠️  STEP 4a: Buat jurnal penutupan DULU, sebelum status DITUTUP.
        jurnal_penutupan_id = None
        if with_closing_entry:
            try:
                jurnal_penutupan_id = _close_by_organization(
                    db, tahun, bulan, date_from, date_to, user_id, company_id=company_id)
            except Exception as e:
                raise ValueError(f"Jurnal penutupan gagal dibuat: {e}") from e
    finally:
        if previous_scope is not None:
            db.info['report_scope'] = previous_scope
        else:
            db.info.pop('report_scope', None)
        db.info.pop('posting_company', None)

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
            company_id=company_id,
        )
        db.add(pp)
        db.flush()

    db.commit()
    db.refresh(pp)
    scope_label = 'global' if company_id is None else f'perusahaan {company_id}'
    logger.info(
        f"Periode {tahun}-{bulan:02d} ({scope_label}) ditutup | laba_rugi={laba_rugi} | "
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
    organization: Optional[dict] = None,
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

    # 4. Net ke LABA_RUGI_TAHUN_BERJALAN (322000)
    # AUD-14 (update #04) — bug fix: sebelumnya memakai KEY_LABA_RUGI_BERJALAN
    # (key legacy yang tidak pernah di-seed di DB → tutup periode dgn P&L ≠ 0
    # selalu gagal ValueError runtime). Key kanonik COA v2 adalah
    # KEY_LABA_RUGI_TAHUN_BERJALAN (LABA_RUGI_TAHUN_BERJALAN → 322000).
    akun_lrb_id = sa_cfg.get_akun_id_or_raise(db, sa_cfg.KEY_LABA_RUGI_TAHUN_BERJALAN, f"Penutupan {tahun}-{bulan:02d}")
    retained = db.get(AkunPerkiraan, akun_lrb_id)
    if retained is None or retained.header != HeaderCOA.MODAL:
        raise ValueError('Setting LABA_RUGI_BERJALAN harus menunjuk akun MODAL')
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
        allow_inactive_accounts=True,
        organization=organization,
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

def _validate_reopen_authorization(user) -> None:
    """Cek apakah user berhak membuka kembali periode yang sudah ditutup.

    Sesuai Master Roadmap §25:
        "Reopen reason + authorization"

    Hanya manajer/admin yang boleh reopen periode — finance user biasa
    tidak boleh, supaya tidak ada unilateral reopening yang bisa mengganggu
    audit trail periode yang sudah ditutup.

    Raises:
        HTTPException 403 kalau user bukan manajer/admin
    """
    from fastapi import HTTPException
    from app.services.workflow_service import role, APPROVERS
    if role(user) not in APPROVERS:
        raise HTTPException(
            status_code=403,
            detail=(
                "Hanya manajer/admin yang boleh membuka kembali periode yang sudah ditutup. "
                "Hubungi manajer/admin jika perlu reopen periode."
            ),
        )


def _validate_sequential_reopen(db: Session, tahun: int, bulan: int, company_id=None) -> None:
    """Cek apakah tidak ada periode setelah (tahun, bulan) yang masih DITUTUP.

    AUD-17: scope perusahaan — baris yang dianggap hanya milik scope
    (company C atau global NULL); penutupan perusahaan LAIN tidak memblokir
    pembukaan scope ini.

    Sesuai Master Roadmap §25:
        "Sequential reopen"

    Logic:
    - Cari PenutupanPeriode dengan status DITUTUP yang lebih baru dari (tahun, bulan)
    - Kalau ada, reject dengan pesan jelas — harus reopen periode terbaru dulu,
      baru bisa reopen periode yang lebih lama.

    Tujuan: prevent user reopen periode lama padahal periode setelahnya masih
    DITUTUP, yang bisa menyebabkan inkonsistensi data (jurnal baru di periode
    lama tidak akan ter-closing di periode setelahnya yang sudah lock).

    Parameter:
        db: SQLAlchemy Session
        tahun: tahun periode yang akan di-reopen
        bulan: bulan periode yang akan di-reopen
        company_id: scope perusahaan (None = global)

    Raises:
        ValueError kalau ada periode setelahnya yang masih DITUTUP
    """
    # Hitung periode_after = (tahun, bulan) + 1 month
    after_tahun = tahun + (1 if bulan == 12 else 0)
    after_bulan = 1 if bulan == 12 else bulan + 1

    # Cari periode DITUTUP setelah (tahun, bulan) — scope company/global
    query = (
        db.query(PenutupanPeriode)
        .filter(
            PenutupanPeriode.status == StatusPeriode.DITUTUP.value,
            # Periode setelah (tahun, bulan): tahun > target OR (tahun == target AND bulan > target)
            (
                (PenutupanPeriode.tahun > tahun) |
                ((PenutupanPeriode.tahun == tahun) & (PenutupanPeriode.bulan > bulan))
            ),
        )
    )
    from sqlalchemy import or_
    query = query.filter(or_(PenutupanPeriode.company_id.is_(None),
                             PenutupanPeriode.company_id == company_id))
    later_closed = query.order_by(
        PenutupanPeriode.tahun.desc(), PenutupanPeriode.bulan.desc()).first()

    if later_closed:
        raise ValueError(
            f"Tidak bisa membuka periode {tahun}-{bulan:02d} karena masih ada periode "
            f"setelahnya yang DITUTUP: {later_closed.tahun}-{later_closed.bulan:02d}. "
            f"Buka kembali periode terbaru ({later_closed.tahun}-{later_closed.bulan:02d}) "
            f"terlebih dahulu, lalu buka periode ini secara berurutan (sequential reopen)."
        )


@atomic_accounting_write
def buka_periode(
    db: Session,
    tahun: int,
    bulan: int,
    user_id: UUID,
    alasan: Optional[str] = None,
    user=None,
    company_id: Optional[UUID] = None,
) -> PenutupanPeriode:
    """Buka kembali periode yang sudah ditutup (global atau scope perusahaan).

    AUD-17: ``company_id`` terisi → membuka baris penutupan perusahaan tsb;
    jurnal penutupan CL-{tahun}-{bulan} milik perusahaan itulah yang dibalik
    (idempotent terhadap pembalik existing). NULL → perilaku lama (global:
    semua jurnal penutupan periode dibalik).

    Phase 8 — Sesuai Master Roadmap §25:
        - "Reopen reason + authorization" — wajib alasan + user harus manajer/admin
        - "Sequential reopen" — tidak boleh reopen periode lama kalau periode setelahnya masih DITUTUP

    Catatan: Jurnal penutupan TIDAK dihapus (untuk audit trail).
    Jurnal pembalik dibuat setelah periode dibuka, dalam transaksi yang sama.
    """
    if bulan < 1 or bulan > 12:
        raise ValueError(f"Bulan harus 1-12, diberikan: {bulan}")

    _validate_company_unit(db, company_id)

    # === Phase 8: Enforce authorization (manager/admin only) ===
    if user is not None:
        _validate_reopen_authorization(user)

    # === Phase 8: Enforce reason required ===
    if not alasan or not alasan.strip():
        raise ValueError(
            "Alasan pembukaan kembali periode wajib diisi. "
            "Contoh: 'Koreksi jurnal periode sebelumnya', 'Adjustment audit', dll."
        )

    pp = _company_scope_filter(
        db.query(PenutupanPeriode).filter(
            PenutupanPeriode.tahun == tahun,
            PenutupanPeriode.bulan == bulan,
            PenutupanPeriode.status == StatusPeriode.DITUTUP.value,
        ),
        PenutupanPeriode, company_id).first()
    if not pp:
        raise ValueError(f"Periode {tahun}-{bulan:02d} tidak ditemukan atau tidak dalam status DITUTUP.")

    # === Phase 8: Enforce sequential reopen (AUD-17: per scope company) ===
    _validate_sequential_reopen(db, tahun, bulan, company_id)

    now = datetime.now(timezone.utc)
    pp.status = StatusPeriode.DIBUKA.value
    db.flush()
    closing_journals = db.query(JurnalUmum).filter(
        JurnalUmum.ref_module == RefModule.PENUTUPAN_PERIODE,
        JurnalUmum.ref_no == f"CL-{tahun}-{bulan:02d}", JurnalUmum.reversal_of_id.is_(None))
    # AUD-17: scope perusahaan → hanya jurnal penutupan milik perusahaan itu.
    if company_id is not None:
        closing_journals = closing_journals.filter(JurnalUmum.company_id == company_id)
    closing_journals = closing_journals.all()
    identifiers = {j.id for j in closing_journals}
    if pp.jurnal_penutupan_id:
        identifiers.add(pp.jurnal_penutupan_id)
    for identifier in sorted(identifiers, key=str):
        reverse_journal(db, identifier, user_id, "Buka kembali periode")
    pp.reopened_by = user_id
    pp.reopened_at = now
    pp.keterangan = f"[DIBUKA] {alasan.strip()}"

    db.commit()
    db.refresh(pp)
    scope_label = 'global' if company_id is None else f'perusahaan {company_id}'
    logger.info(f"Periode {tahun}-{bulan:02d} ({scope_label}) dibuka kembali oleh user {user_id} | alasan: {alasan}")
    return pp


# ============================================================
# PRE-CLOSE READINESS (Roadmap §25: "Pre-close readiness")
# ============================================================

def get_pre_close_readiness(
    db: Session,
    tahun: int,
    bulan: int,
) -> dict:
    """Cek readiness sebelum menutup periode.

    Sesuai Master Roadmap §25:
        "Pre-close readiness"

    Returns dict dengan (camelCase — kontrak wire frontend penutupan-periode.tsx):
    - ready: bool — True kalau semua check pass
    - periode: (tahun, bulan)
    - checks: dict berisi hasil setiap check (camelCase)
    - blockingIssues: list of issues yang block closing
    """
    from app.services.laporan_service import validate_reports
    from app.services.reporting_ledger import month_bounds

    date_from, date_to = month_bounds(tahun, bulan)

    # 1. Validate reports (Trial Balance, Balance Sheet, Equity, Cash Flow)
    report_validation = validate_reports(db, date_from, date_to)

    # 2. Cek apakah ada jurnal DRAFT di periode ini (belum diposting)
    draft_journals = (
        db.query(JurnalUmum)
        .filter(
            JurnalUmum.tanggal >= date_from,
            JurnalUmum.tanggal <= date_to,
            JurnalUmum.status == StatusJurnal.DRAFT,
        )
        .count()
    )

    # 3. Cek apakah ada dokumen transaksi yang belum diposting (Sales Invoice, Purchase Invoice, dll)
    # Untuk P0, cukup cek jurnal DRAFT. Cek dokumen transaksi bisa ditambah di Phase P1.

    # 4. Cek apakah periode sebelumnya sudah ditutup (sequential closing)
    prev_tahun = tahun - (1 if bulan == 1 else 0)
    prev_bulan = 12 if bulan == 1 else bulan - 1
    prev_periode = (
        db.query(PenutupanPeriode)
        .filter(
            PenutupanPeriode.tahun == prev_tahun,
            PenutupanPeriode.bulan == prev_bulan,
        )
        .first()
    )
    prev_closed = (
        prev_periode is not None and
        prev_periode.status == StatusPeriode.DITUTUP.value
    )

    # 5. Cek apakah periode ini belum ditutup
    current_periode = (
        db.query(PenutupanPeriode)
        .filter(PenutupanPeriode.tahun == tahun, PenutupanPeriode.bulan == bulan)
        .first()
    )
    already_closed = (
        current_periode is not None and
        current_periode.status == StatusPeriode.DITUTUP.value
    )

    # Build checks dict (camelCase keys — wire contract)
    checks = {
        'trialBalanceBalanced': report_validation['checks']['neraca_saldo_mutasi'] == 0,
        'trialBalanceEndingBalanced': report_validation['checks']['neraca_saldo_akhir'] == 0,
        'balanceSheetBalanced': report_validation['checks']['persamaan_neraca'] == 0,
        'equityReconciled': report_validation['checks']['perubahan_modal'] == 0,
        'cashFlowReconciled': report_validation['checks']['arus_kas'] == 0,
        'noDraftJournals': draft_journals == 0,
        'noInvalidJournals': len(report_validation['jurnal_tidak_valid']) == 0,
        'previousPeriodClosed': prev_closed,
        'periodNotAlreadyClosed': not already_closed,
    }

    # Blocking issues
    blocking_issues = []
    if not checks['trialBalanceBalanced']:
        blocking_issues.append(f"Trial Balance tidak balance: selisih mutasi = {report_validation['checks']['neraca_saldo_mutasi']}")
    if not checks['trialBalanceEndingBalanced']:
        blocking_issues.append(f"Trial Balance tidak balance: selisih saldo akhir = {report_validation['checks']['neraca_saldo_akhir']}")
    if not checks['balanceSheetBalanced']:
        blocking_issues.append(f"Neraca tidak balance: persamaan = {report_validation['checks']['persamaan_neraca']}")
    if not checks['equityReconciled']:
        blocking_issues.append(f"Perubahan modal tidak reconcile: selisih = {report_validation['checks']['perubahan_modal']}")
    if not checks['cashFlowReconciled']:
        blocking_issues.append(f"Arus kas tidak reconcile: selisih = {report_validation['checks']['arus_kas']}")
    if not checks['noDraftJournals']:
        blocking_issues.append(f"Ada {draft_journals} jurnal DRAFT yang belum diposting di periode ini")
    if not checks['noInvalidJournals']:
        blocking_issues.append(f"Ada {len(report_validation['jurnal_tidak_valid'])} jurnal tidak valid (unbalanced / akun non-DETAIL / dll)")
    if not checks['previousPeriodClosed']:
        blocking_issues.append(f"Periode sebelumnya ({prev_tahun}-{prev_bulan:02d}) belum ditutup — tutup dulu secara berurutan")
    if not checks['periodNotAlreadyClosed']:
        blocking_issues.append(f"Periode {tahun}-{bulan:02d} sudah ditutup — tidak perlu tutup lagi")

    ready = len(blocking_issues) == 0

    return {
        'ready': ready,
        'periode': {'tahun': tahun, 'bulan': bulan},
        'checks': checks,
        'blockingIssues': blocking_issues,
        'detail': {
            'reportValidation': report_validation,
            'draftJournalsCount': draft_journals,
            'invalidJournalsCount': len(report_validation['jurnal_tidak_valid']),
            'previousPeriod': {'tahun': prev_tahun, 'bulan': prev_bulan, 'closed': prev_closed},
        },
    }


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


def _close_by_organization(db, tahun, bulan, date_from, date_to, user_id, company_id=None):
    """Close each exact dimension tuple, retaining balanced branch/project journals.

    AUD-17: ``company_id`` terisi → hanya tuple jurnal perusahaan tsb yang
    di-zero-out (penutupan buku perusahaan); None → semua tuple (global).
    """
    from app.models.organization import FIELDS
    from app.services import reporting_ledger as gl
    columns = [getattr(JurnalUmum, field) for field in FIELDS]
    groups = db.query(*columns).filter(JurnalUmum.status == StatusJurnal.POSTED,
        JurnalUmum.tanggal >= date_from, JurnalUmum.tanggal <= date_to).distinct().all() or [tuple(None for _ in FIELDS)]
    if company_id is not None:
        # Penutupan per perusahaan: hanya tuple milik perusahaan itu
        # (tuple tanpa perusahaan = buku global, ditutup penutupan global).
        groups = [g for g in groups if g[0] == company_id] or [tuple(company_id, *(None for _ in FIELDS[1:]))]
    first = None
    previous_scope = db.info.pop('report_scope', None)
    try:
        for group in sorted(groups, key=lambda row: tuple(str(v or '') for v in row)):
            # Exact tuple, including NULL child dimensions, unlike report filters.
            q = db.query(AkunPerkiraan, func.sum(JurnalDetail.debit), func.sum(JurnalDetail.kredit)).join(
                JurnalDetail, JurnalDetail.akun_perkiraan_id == AkunPerkiraan.id).join(
                JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id).filter(AkunPerkiraan.header.in_(gl.PROFIT))
            for column, value in zip(columns, group):
                q = q.filter(column == value)
            rows = {a.id: {'account': a, 'debit': d, 'kredit': k} for a,d,k in
                    gl.posted(db, q, date_from, date_to).group_by(AkunPerkiraan.id).all()}
            identifier = _create_closing_entry(db, tahun, bulan, date_from, date_to, gl.profit(rows),
                gl.header_items(rows, HeaderCOA.PENDAPATAN), gl.header_items(rows, HeaderCOA.HPP),
                gl.header_items(rows, HeaderCOA.BEBAN), user_id, dict(zip(FIELDS, group)))
            first = first or identifier
    finally:
        if previous_scope is not None:
            db.info['report_scope'] = previous_scope
    return first


# ============================================================
# AUD-14 (update #04) — CLOSING TAHUNAN
# Transfer saldo LABA_RUGI_TAHUN_BERJALAN (322000) → LABA_DITAHAN (321000)
# ============================================================
from datetime import date as _date_mod  # noqa: E402
from app.models.transaksi.penutupan_tahun import PenutupanTahun  # noqa: E402
from app.services.reporting_ledger import JAKARTA  # noqa: E402

YEAR_REF_NO = "CL-YEAR-{tahun}"


def _year_end(tahun: int) -> datetime:
    from app.services.reporting_ledger import day_end
    return day_end(_date_mod(tahun, 12, 31))


def _laba_berjalan_saldo_per_organisasi(db: Session, akun_lrb_id: UUID, sampai: datetime,
                                        company_id: Optional[UUID] = None):
    """Saldo (kredit − debit) akun 322000 per tuple dimensi organisasi s/d `sampai`.

    Saldonya mencerminkan hasil jurnal penutupan bulanan (zero-out P&L per
    bulan dialirkan ke 322000) + selisih lain yang menyentuh akun tsb.

    AUD-17: ``company_id`` terisi → hanya tuple perusahaan tsb.
    """
    from app.models.organization import FIELDS
    columns = [getattr(JurnalUmum, field) for field in FIELDS]
    rows = (
        db.query(
            *columns,
            func.coalesce(func.sum(JurnalDetail.kredit), 0)
            - func.coalesce(func.sum(JurnalDetail.debit), 0),
        )
        .join(JurnalDetail, JurnalDetail.jurnal_umum_id == JurnalUmum.id)
        .filter(
            JurnalDetail.akun_perkiraan_id == akun_lrb_id,
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal <= sampai,
            # Kecualikan jurnal transfer tahunan sendiri (idempotensi re-close)
            JurnalUmum.ref_no != YEAR_REF_NO.format(tahun=sampai.year),
        )
    )
    if company_id is not None:
        rows = rows.filter(JurnalUmum.company_id == company_id)
    rows = rows.group_by(*columns).all()
    return rows


def get_tahun_list(db: Session, company_id: Optional[UUID] = None) -> List[dict]:
    """Daftar tahun buku + status penutupan (untuk UI Penutupan Periode).

    AUD-17: scope perusahaan — record tahun & bulan DITUTUP yang dihitung
    hanya milik scope (perusahaan C atau global NULL).
    """
    records = _company_scope_filter(
        db.query(PenutupanTahun), PenutupanTahun, company_id).order_by(PenutupanTahun.tahun.desc()).all()
    by_year = {r.tahun: r for r in records}
    min_year = db.query(func.min(func.extract('year', JurnalUmum.tanggal))).scalar()
    today = datetime.now(JAKARTA).date()
    years = set(by_year.keys())
    if min_year:
        years.add(int(min_year))
    years.add(today.year - 1)
    from sqlalchemy import or_
    scope_months = or_(PenutupanPeriode.company_id.is_(None),
                       PenutupanPeriode.company_id == company_id)
    result = []
    for tahun in sorted((y for y in years if y), reverse=True):
        rec = by_year.get(tahun)
        # Status Desember (proxy: seluruh tahun harus DITUTUP sebelum tutup tahun)
        months_closed = (
            db.query(func.count(func.distinct(PenutupanPeriode.bulan)))
            .filter(
                PenutupanPeriode.tahun == tahun,
                PenutupanPeriode.status == StatusPeriode.DITUTUP.value,
                PenutupanPeriode.bulan <= 12,
                scope_months,
            )
            .scalar()
        ) or 0
        # camelCase — kontrak wire frontend (laporan penutupan periode)
        result.append({
            "tahun": tahun,
            "status": rec.status if rec else "TERBUKA",
            "labaRugi": str(rec.laba_rugi) if (rec and rec.laba_rugi is not None) else None,
            "bulanDitutup": int(months_closed),
            "semuaBulanDitutup": int(months_closed) == 12,
            "keterangan": rec.keterangan if rec else None,
            "companyId": str(rec.company_id) if rec and rec.company_id else None,
            "closedAt": rec.closed_at.isoformat() if (rec and rec.closed_at) else None,
            "reopenedAt": rec.reopened_at.isoformat() if (rec and rec.reopened_at) else None,
            "jurnalPenutupanId": str(rec.jurnal_penutupan_id) if (rec and rec.jurnal_penutupan_id) else None,
        })
    return result


@atomic_accounting_write
def tutup_tahun(
    db: Session,
    tahun: int,
    user_id: UUID,
    keterangan: Optional[str] = None,
    with_closing_entry: bool = True,
    company_id: Optional[UUID] = None,
) -> PenutupanTahun:
    """Tutup tahun buku: transfer laba berjalan → laba ditahan (scope company).

    AUD-17: ``company_id`` terisi → transfer hanya saldo 322000 milik tuple
    perusahaan tsb & record tahun per perusahaan; NULL → global (semua tuple).

    Guard:
    1. Tahun harus sudah berlalu (bukan tahun berjalan/masa depan).
    2. Ke-12 bulan tahun tsb sudah DITUTUP (scope: global ATAU perusahaan ini).
    3. Tahun belum ditutup (status DITUTUP ditolak).
    4. Jurnal transfer dibuat SEBELUM flag DITUTUP — dan karena Desember
       sudah terkunci, jurnal penutupan tahunan memakai bypass
       ``allow_closed_period`` (hanya untuk RefModule.PENUTUPAN_PERIODE).
    """
    now = datetime.now(JAKARTA)
    if tahun >= now.year:
        raise ValueError(
            f"Tidak bisa menutup tahun buku yang belum berakhir "
            f"(tahun {tahun}, sekarang {now.year})."
        )
    _validate_company_unit(db, company_id)
    existing = _company_scope_filter(
        db.query(PenutupanTahun).filter(PenutupanTahun.tahun == tahun),
        PenutupanTahun, company_id).first()
    if existing and existing.status == "DITUTUP":
        raise ValueError(f"Tahun buku {tahun} sudah ditutup.")

    # AUD-17: cek 12 bulan DITUTUP per scope (global atau perusahaan ini).
    from sqlalchemy import or_
    month_scope = or_(PenutupanPeriode.company_id.is_(None),
                      PenutupanPeriode.company_id == company_id)
    closed_months = {row[0] for row in (
        db.query(PenutupanPeriode.bulan).filter(
            PenutupanPeriode.tahun == tahun,
            PenutupanPeriode.status == StatusPeriode.DITUTUP.value,
            month_scope).distinct().all())}
    if len(closed_months) < 12:
        open_months = [b for b in range(1, 13) if b not in closed_months]
        raise ValueError(
            f"Semua 12 bulan tahun {tahun} harus DITUTUP sebelum tutup tahun "
            f"(baru {len(closed_months)}/12; belum: bulan {open_months})."
        )

    year_end = _year_end(tahun)
    jurnal_penutupan_id = None
    laba_rugi_total = Decimal("0")

    if with_closing_entry:
        akun_lrb_id = sa_cfg.get_akun_id_or_raise(db, sa_cfg.KEY_LABA_RUGI_TAHUN_BERJALAN, f"Tutup tahun {tahun}")
        akun_ld_id = sa_cfg.get_akun_id_or_raise(db, sa_cfg.KEY_LABA_DITAHAN, f"Tutup tahun {tahun}")
        for akun_id, label in ((akun_lrb_id, "Laba (Rugi) Tahun Berjalan"), (akun_ld_id, "Laba (Rugi) Ditahan")):
            akun = db.get(AkunPerkiraan, akun_id)
            if akun is None or akun.header != HeaderCOA.MODAL:
                raise ValueError(f"Setting akun untuk {label} harus menunjuk akun MODAL")

        from app.models.organization import FIELDS
        first = None
        # AUD-17: scope perusahaan → hanya tuple perusahaan tsb yang ditransfer.
        for group in _laba_berjalan_saldo_per_organisasi(db, akun_lrb_id, year_end, company_id=company_id):
            org = dict(zip(FIELDS, group[:-1]))
            saldo_kredit = group[-1]
            if saldo_kredit == 0:
                continue
            laba_rugi_total += saldo_kredit
            saldo_abs = abs(saldo_kredit)
            if saldo_kredit > 0:
                # Laba: D 322000 (nol-kan laba berjalan) / K 321000 (tahan laba)
                entries = [
                    JurnalEntryItem(akun_perkiraan_id=akun_lrb_id, debit=saldo_abs,
                                    keterangan=f"Transfer laba tahun {tahun}"),
                    JurnalEntryItem(akun_perkiraan_id=akun_ld_id, kredit=saldo_abs,
                                    keterangan=f"Laba ditahan tahun {tahun}"),
                ]
            else:
                # Rugi: D 321000 / K 322000
                entries = [
                    JurnalEntryItem(akun_perkiraan_id=akun_ld_id, debit=saldo_abs,
                                    keterangan=f"Tutup rugi tahun {tahun}"),
                    JurnalEntryItem(akun_perkiraan_id=akun_lrb_id, kredit=saldo_abs,
                                    keterangan=f"Rugi tahun {tahun} ditahan"),
                ]
            jurnal = auto_posting_jurnal(
                db=db,
                ref_module=RefModule.PENUTUPAN_PERIODE,
                ref_no=YEAR_REF_NO.format(tahun=tahun),
                entries=entries,
                keterangan=f"Jurnal Penutupan Tahun {tahun} | Laba/Rugi: {saldo_kredit}",
                tanggal=year_end,
                created_by=user_id,
                status=StatusJurnal.POSTED,
                allow_inactive_accounts=True,
                organization=org,
                allow_closed_period=True,  # AUD-14 — Desember sudah terkunci
            )
            first = first or jurnal.id
        jurnal_penutupan_id = first
    else:
        # Tanpa closing entry: laba/rugi tahun = saldo 322000 (informasional)
        try:
            from app.services import setting_akun_service as _cfg
            lid = _cfg.get_setting_akun_id_optional(db, _cfg.KEY_LABA_RUGI_TAHUN_BERJALAN)
            if lid:
                saldo = (
                    db.query(func.coalesce(func.sum(JurnalDetail.kredit), 0)
                             - func.coalesce(func.sum(JurnalDetail.debit), 0))
                    .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
                    .filter(JurnalDetail.akun_perkiraan_id == lid,
                            JurnalUmum.status == StatusJurnal.POSTED,
                            JurnalUmum.tanggal <= year_end)
                    .scalar()
                )
                laba_rugi_total = saldo or Decimal("0")
        except Exception:  # noqa: BLE001
            laba_rugi_total = Decimal("0")

    if existing:
        rec = existing
        rec.status = "DITUTUP"
        rec.laba_rugi = laba_rugi_total
        rec.keterangan = keterangan
        rec.closed_by = user_id
        rec.closed_at = now
        rec.reopened_by = None
        rec.reopened_at = None
        rec.reopen_alasan = None
        rec.jurnal_penutupan_id = jurnal_penutupan_id
    else:
        rec = PenutupanTahun(
            tahun=tahun,
            status="DITUTUP",
            laba_rugi=laba_rugi_total,
            keterangan=keterangan,
            closed_by=user_id,
            closed_at=now,
            created_by=user_id,
            jurnal_penutupan_id=jurnal_penutupan_id,
            company_id=company_id,
        )
        db.add(rec)
    db.flush()
    db.commit()
    db.refresh(rec)
    scope_label = 'global' if company_id is None else f'perusahaan {company_id}'
    logger.info(
        f"Tahun buku {tahun} ({scope_label}) ditutup | laba_rugi={laba_rugi_total} | "
        f"closing_entry={'YA' if jurnal_penutupan_id else 'TIDAK'}"
    )
    return rec


@atomic_accounting_write
def buka_tahun(
    db: Session,
    tahun: int,
    user_id: UUID,
    alasan: Optional[str] = None,
    user=None,
    company_id: Optional[UUID] = None,
) -> PenutupanTahun:
    """Buka kembali tahun buku yang sudah ditutup (reversal jurnal transfer).

    AUD-17: ``company_id`` terisi → membuka record tahun perusahaan tsb &
    hanya jurnal transfer CL-YEAR milik perusahaan itu yang dibalik.

    - Alasan wajib + hanya manajer/admin (pola buka_periode).
    - Jurnal transfer CL-YEAR-{tahun} dibalik (audit trail tetap utuh);
      jurnal pembalik bertanggal hari ini (periode terbuka).
    - Bulan-bulan tetap DITUTUP — buka bulan tertentu lewat buka_periode
      (urutan yang benar: buka tahun dulu, lalu buka bulan).
    """
    if user is not None:
        _validate_reopen_authorization(user)
    if not alasan or not alasan.strip():
        raise ValueError("Alasan pembukaan kembali tahun buku wajib diisi.")

    _validate_company_unit(db, company_id)
    rec = _company_scope_filter(
        db.query(PenutupanTahun).filter(
            PenutupanTahun.tahun == tahun, PenutupanTahun.status == "DITUTUP"),
        PenutupanTahun, company_id).first()
    if not rec:
        raise ValueError(f"Tahun buku {tahun} tidak ditemukan atau tidak dalam status DITUTUP.")

    closing_journals = db.query(JurnalUmum).filter(
        JurnalUmum.ref_module == RefModule.PENUTUPAN_PERIODE,
        JurnalUmum.ref_no == YEAR_REF_NO.format(tahun=tahun),
        JurnalUmum.reversal_of_id.is_(None),
    )
    if company_id is not None:
        closing_journals = closing_journals.filter(JurnalUmum.company_id == company_id)
    closing_journals = closing_journals.all()
    identifiers = {j.id for j in closing_journals}
    if rec.jurnal_penutupan_id:
        identifiers.add(rec.jurnal_penutupan_id)
    for identifier in sorted(identifiers, key=str):
        reverse_journal(db, identifier, user_id, f"Buka kembali tahun buku {tahun}")

    now_utc = datetime.now(timezone.utc)
    rec.status = "DIBUKA"
    rec.reopened_by = user_id
    rec.reopened_at = now_utc
    rec.reopen_alasan = alasan.strip()
    db.flush()
    db.commit()
    db.refresh(rec)
    logger.info(f"Tahun buku {tahun} dibuka kembali oleh {user_id} | alasan: {alasan}")
    return rec
