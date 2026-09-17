"""Shared posted-ledger queries. Business dates use Asia/Jakarta (UTC+07)."""
from datetime import datetime, time, timedelta, timezone
from calendar import monthrange
from decimal import Decimal
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased
from app.models.akun_perkiraan import AkunPerkiraan as Account, HeaderCOA, TingkatAkun
from app.models.detail.jurnal_detail import JurnalDetail as Line
from app.models.transaksi.jurnal import JurnalUmum as Journal, StatusJurnal, RefModule
from app.services.organization_service import apply_scope

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


def posted(db, query, start=None, end=None, before=None, performance=False):
    query = apply_scope(db, query, Journal).filter(Journal.status == StatusJurnal.POSTED)
    if start is not None:
        query = query.filter(Journal.tanggal >= local_datetime(start))
    if end is not None:
        query = query.filter(Journal.tanggal <= local_datetime(end))
    if before is not None:
        query = query.filter(Journal.tanggal < local_datetime(before))
    if performance:
        # COALESCE is needed because legacy manual journals may have NULL ref_module.
        query = query.filter(~func.coalesce(closing_condition(), False))
    return query


def totals(db, start=None, end=None, before=None, performance=False, header=None):
    q = db.query(Account, func.sum(Line.debit), func.sum(Line.kredit)).join(
        Line, Line.akun_perkiraan_id == Account.id).join(Journal, Journal.id == Line.jurnal_umum_id)
    if header is not None:
        q = q.filter(Account.header == header)
    q = posted(db, q, start, end, before, performance)
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
