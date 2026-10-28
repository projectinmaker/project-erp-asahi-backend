"""Shared posted-ledger queries. Business dates use Asia/Jakarta (UTC+07)."""
from datetime import datetime, time, timedelta, timezone, date
from calendar import monthrange
from decimal import Decimal
from sqlalchemy import func, or_, select, and_
from sqlalchemy.orm import aliased
from app.models.akun_perkiraan import AkunPerkiraan as Account, HeaderCOA, TingkatAkun
from app.models.detail.jurnal_detail import JurnalDetail as Line
from app.models.transaksi.jurnal import JurnalUmum as Journal, StatusJurnal, RefModule

ZERO = Decimal('0')
JAKARTA = timezone(timedelta(hours=7))
PROFIT = (HeaderCOA.PENDAPATAN, HeaderCOA.HPP, HeaderCOA.BEBAN)


def local_datetime(value):
    if not isinstance(value, datetime):
        return datetime.combine(value, time.min, JAKARTA)
    return value.astimezone(JAKARTA) if value.tzinfo else value.replace(tzinfo=JAKARTA)


def day_start(value):
    return local_datetime(value).replace(hour=0, minute=0, second=0, microsecond=0)


def day_end(value):
    return local_datetime(value).replace(hour=23, minute=59, second=59, microsecond=999999)


def month_bounds(year, month):
    start = datetime(year, month, 1, tzinfo=JAKARTA)
    end = datetime(year, month, monthrange(year, month)[1], 23, 59, 59, 999999, tzinfo=JAKARTA)
    return start, end


def period(start, end):
    return {'dari': local_datetime(start).date().isoformat(), 'sampai': local_datetime(end).date().isoformat()}


def closing_condition():
    original = aliased(Journal)
    return or_(Journal.ref_module == RefModule.PENUTUPAN_PERIODE,
               Journal.reversal_of_id.in_(
                   select(original.id).where(original.ref_module == RefModule.PENUTUPAN_PERIODE)))


def posted(db, query, start=None, end=None, before=None, performance=False, fiscal_year=None):
    query = query.filter(Journal.status == StatusJurnal.POSTED)
    # === AUD-15 (update #04) — mode tahun buku ===
    # Mutasi periode hanya menghitung jurnal non-SALDO_AWAL mulai 1 Jan tahun
    # buku; saldo awal tahun dibaca terpisah lewat fiscal_opening_totals()
    # (jurnal SA-{tahun} milik tahun itu sendiri, bukan dihitung dua kali).
    if fiscal_year is not None:
        # Jurnal saldo awal + PEMBALIKNYA (bertanggal tahun buku saat replace)
        # tidak dihitung sebagai mutasi — saldo awal tahun dibaca terpisah via
        # fiscal_opening_totals (jurnal SA-{tahun} aktif terbaru).
        query = query.filter(Journal.ref_module != RefModule.SALDO_AWAL)
        sa_ids = select(Journal.id).where(Journal.ref_module == RefModule.SALDO_AWAL)
        query = query.filter(~Journal.reversal_of_id.in_(sa_ids))
        if start is None:
            start = date(fiscal_year, 1, 1)
    if start is not None:
        query = query.filter(Journal.tanggal >= local_datetime(start))
    if end is not None:
        # B-04: document journals are stored as 00:00 UTC (= 07:00 WIB) of the business
        # date, while local_datetime(end) is midnight Jakarta (= 17:00 UTC the day before),
        # so an inclusive bound at midnight would cut off every journal dated on `end`.
        # Bound inclusively at end-of-day Jakarta so day-H transactions are included.
        query = query.filter(Journal.tanggal <= day_end(end))
    if before is not None:
        query = query.filter(Journal.tanggal < local_datetime(before))
    if performance:
        # COALESCE is needed because legacy manual journals may have NULL ref_module.
        query = query.filter(~func.coalesce(closing_condition(), False))
    return query


def fiscal_opening_totals(db, tahun):
    """AUD-15 — saldo awal tahun buku: baris jurnal SA-{tahun} aktif.

    Aktif = POSTED dan belum dibalik jurnal pembaliknya. Legacy SA-INIT
    (tanpa ref_no per tahun) tidak termasuk — mode tahun buku memang
    memisahkan buku per tahun (SA-INIT tetap terbaca di mode kontinu).
    """
    reversal = aliased(Journal)
    reversed_ids = select(reversal.reversal_of_id).where(
        reversal.reversal_of_id.is_not(None), reversal.status == StatusJurnal.POSTED)
    journal = (db.query(Journal)
               .filter(Journal.ref_module == RefModule.SALDO_AWAL,
                       Journal.ref_no == f"SA-{tahun}",
                       Journal.status == StatusJurnal.POSTED,
                       ~Journal.id.in_(reversed_ids))
               .order_by(Journal.created_at.desc(), Journal.id)
               .first())
    if journal is None:
        return {}
    rows = (db.query(Account, func.sum(Line.debit), func.sum(Line.kredit))
            .join(Line, Line.akun_perkiraan_id == Account.id)
            .filter(Line.jurnal_umum_id == journal.id)
            .group_by(Account.id).order_by(Account.kode).all())
    return {a.id: {'account': a, 'debit': d or ZERO, 'kredit': k or ZERO} for a, d, k in rows}


def available_fiscal_years(db):
    """AUD-15 — daftar tahun yang punya set saldo awal (SA-{tahun} aktif)."""
    reversal = aliased(Journal)
    reversed_ids = select(reversal.reversal_of_id).where(
        reversal.reversal_of_id.is_not(None), reversal.status == StatusJurnal.POSTED)
    rows = (db.query(Journal.ref_no)
            .filter(Journal.ref_module == RefModule.SALDO_AWAL,
                    Journal.ref_no.like('SA-%'),
                    Journal.status == StatusJurnal.POSTED,
                    ~Journal.id.in_(reversed_ids))
            .distinct().all())
    years = []
    for (ref,) in rows:
        try:
            years.append(int(str(ref).split('-')[1]))
        except (ValueError, IndexError):
            continue
    return sorted(set(years), reverse=True)


def totals(db, start=None, end=None, before=None, performance=False, header=None, fiscal_year=None):
    q = db.query(Account, func.sum(Line.debit), func.sum(Line.kredit)).join(
        Line, Line.akun_perkiraan_id == Account.id).join(Journal, Journal.id == Line.jurnal_umum_id)
    if header is not None:
        q = q.filter(Account.header == header)
    q = posted(db, q, start, end, before, performance, fiscal_year=fiscal_year)
    # Do not omit inactive accounts or corrupted non-detail history: validation exposes it.
    return {a.id: {'account': a, 'debit': d or ZERO, 'kredit': k or ZERO}
            for a, d, k in q.group_by(Account.id).order_by(Account.kode).all()}


def net(row):
    return row['debit'] - row['kredit'] if row else ZERO


def header_items(rows, header):
    sign = 1 if header in (HeaderCOA.AKTIVA, HeaderCOA.HPP, HeaderCOA.BEBAN) else -1
    return [{'kode_akun': r['account'].kode, 'nama_akun': r['account'].nama, 'total': sign * net(r)}
            for r in rows.values() if r['account'].header == header and net(r)]


def profit(rows):
    return -sum((net(r) for r in rows.values() if r['account'].header in PROFIT), ZERO)
