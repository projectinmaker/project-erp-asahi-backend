"""Financial reports derive from the same posted general ledger and scope."""
from datetime import timedelta
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.models import KasBankAkun, SalesInvoice
from app.services import reporting_ledger as gl, cashflow_service as cashflow
from app.services.aging_service import aging
from app.services import settlement_service as settlement


def _saldo_per_akun_list(db, header, date_from, date_to=None, only_detail=True):
    # Performance excludes closing journals and their reversals, not business reversals.
    rows = gl.totals(db, date_from, date_to, performance=header in gl.PROFIT, header=header)
    if only_detail:
        rows = {key: r for key, r in rows.items() if r['account'].tingkat == gl.TingkatAkun.DETAIL}
    return gl.header_items(rows, header)


def _total_by_header(db, header, date_from, date_to=None):
    return sum((r['total'] for r in _saldo_per_akun_list(db, header, date_from, date_to)), gl.ZERO)


def get_laba_rugi(db, date_from, date_to):
    rows = gl.totals(db, date_from, date_to, performance=True)
    groups = {name: gl.header_items(rows, header) for name, header in
              (('pendapatan', gl.HeaderCOA.PENDAPATAN), ('hpp', gl.HeaderCOA.HPP), ('beban', gl.HeaderCOA.BEBAN))}
    totals = {'total_'+name: sum((r['total'] for r in items), gl.ZERO) for name, items in groups.items()}
    gross = totals['total_pendapatan'] - totals['total_hpp']
    return {'periode': gl.period(date_from, date_to), **groups, **totals,
            'laba_kotor': gross, 'laba_bersih': gross-totals['total_beban']}


def get_neraca_saldo(db, date_from, date_to):
    opening = gl.totals(db, before=date_from)
    movements = gl.totals(db, date_from, date_to)
    items = []
    for key in sorted(opening.keys() | movements.keys(), key=lambda k: (movements.get(k) or opening[k])['account'].kode):
        row = movements.get(key) or opening[key]
        account = row['account']
        debit = movements[key]['debit'] if key in movements else gl.ZERO
        credit = movements[key]['kredit'] if key in movements else gl.ZERO
        before = gl.net(opening.get(key))
        after = before + debit-credit
        sign = 1 if account.saldo_normal == 'DEBIT' else -1
        items.append({'kode_akun': account.kode, 'nama_akun': account.nama, 'saldo_normal': account.saldo_normal,
                      'total_debit': debit, 'total_kredit': credit, 'saldo': sign*(debit-credit),
                      'saldo_awal': sign*before, 'saldo_akhir': sign*after,
                      'saldo_debit': max(after, gl.ZERO), 'saldo_kredit': max(-after, gl.ZERO)})
    debit = sum((r['total_debit'] for r in items), gl.ZERO)
    credit = sum((r['total_kredit'] for r in items), gl.ZERO)
    return {'periode': gl.period(date_from, date_to), 'akun': items, 'total_debit': debit, 'total_kredit': credit,
            'selisih': debit-credit, 'total_saldo_debit': sum((r['saldo_debit'] for r in items), gl.ZERO),
            'total_saldo_kredit': sum((r['saldo_kredit'] for r in items), gl.ZERO)}


def get_neraca(db, tanggal):
    rows = gl.totals(db, end=tanggal)
    groups = {name: gl.header_items(rows, header) for name, header in
              (('aset', gl.HeaderCOA.AKTIVA), ('kewajiban', gl.HeaderCOA.KEWAJIBAN), ('ekuitas', gl.HeaderCOA.MODAL))}
    unclosed = gl.profit(rows)
    if unclosed:
        groups['ekuitas'].append({'kode_akun': 'LABA_BELUM_DITUTUP', 'nama_akun': 'Laba/rugi belum ditutup', 'total': unclosed})
    totals = {'total_'+name: sum((r['total'] for r in items), gl.ZERO) for name, items in groups.items()}
    return {'tanggal': gl.local_datetime(tanggal).date().isoformat(), **groups, **totals,
            'selisih': totals['total_aset']-totals['total_kewajiban']-totals['total_ekuitas']}


def get_perubahan_modal(db, date_from, date_to):
    before = gl.totals(db, before=date_from)
    after = gl.totals(db, end=date_to)
    movement = gl.totals(db, date_from, date_to, header=gl.HeaderCOA.MODAL)
    business = gl.totals(db, date_from, date_to, performance=True, header=gl.HeaderCOA.MODAL)
    profit = get_laba_rugi(db, date_from, date_to)['laba_bersih']
    items = []
    keys = {key for key, row in {**before, **after}.items() if row['account'].header == gl.HeaderCOA.MODAL}
    for key in sorted(keys, key=lambda k: (after.get(k) or before[k])['account'].kode):
        account = (after.get(key) or before[key])['account']
        row = movement.get(key, {'debit': gl.ZERO, 'kredit': gl.ZERO})
        items.append({'kode_akun': account.kode, 'nama_akun': account.nama, 'saldo_awal': -gl.net(before.get(key)),
                      'mutasi_debit': row['debit'], 'mutasi_kredit': row['kredit'], 'perubahan': -gl.net(row),
                      'saldo_akhir': -gl.net(after.get(key))})
    opening = sum((r['saldo_awal'] for r in items), gl.ZERO) + gl.profit(before)
    ending = sum((r['saldo_akhir'] for r in items), gl.ZERO) + gl.profit(after)
    owner = -sum((gl.net(r) for r in business.values()), gl.ZERO)
    closing = -sum((gl.net(r) for r in movement.values()), gl.ZERO)-owner
    return {'periode': gl.period(date_from, date_to), 'akun_modal': items, 'laba_rugi_berjalan': profit,
            'total_modal_awal': opening, 'total_modal_akhir': ending, 'mutasi_modal_non_penutupan': owner,
            'transfer_penutupan': closing, 'laba_belum_ditutup_awal': gl.profit(before),
            'laba_belum_ditutup_akhir': gl.profit(after), 'selisih_rekonsiliasi': opening+owner+profit-ending}


def get_arus_kas(db, date_from, date_to):
    return cashflow.report(db, date_from, date_to)


def get_umur_piutang(db, as_of):
    return aging(db, 'piutang', as_of)


def get_umur_hutang(db, as_of):
    return aging(db, 'hutang', as_of)


def get_buku_besar(db, akun_id, date_from, date_to):
    account = db.get(gl.Account, akun_id)
    if account is None:
        raise ValueError('Akun tidak ditemukan')
    sign = 1 if account.saldo_normal == 'DEBIT' else -1
    opening = sign * gl.net(gl.totals(db, before=date_from).get(akun_id))
    q = db.query(gl.Line, gl.Journal).join(gl.Journal, gl.Journal.id == gl.Line.jurnal_umum_id).filter(gl.Line.akun_perkiraan_id == akun_id)
    rows = gl.posted(db, q, date_from, date_to).order_by(gl.Journal.tanggal, gl.Journal.no_jurnal, gl.Line.id).all()
    balance, debit, credit, items = opening, gl.ZERO, gl.ZERO, []
    for line, journal in rows:
        balance += sign * (line.debit-line.kredit)
        debit += line.debit
        credit += line.kredit
        items.append({'tanggal': gl.local_datetime(journal.tanggal).date().isoformat(), 'no_jurnal': journal.no_jurnal,
                      'deskripsi': line.keterangan or journal.keterangan or '', 'debit': line.debit,
                      'kredit': line.kredit, 'saldo': balance})
    return {'akun': {'kode': account.kode, 'nama': account.nama}, 'periode': gl.period(date_from, date_to),
            'saldo_awal': opening, 'transaksi': items, 'total_debit': debit, 'total_kredit': credit, 'saldo_akhir': balance}


def _cash_mappings(db, jenis=None):
    q = db.query(KasBankAkun)
    if jenis:
        q = q.filter(KasBankAkun.jenis == jenis)
    mappings = {}
    for row in q.order_by(KasBankAkun.kode, KasBankAkun.id).all():
        if row.akun_perkiraan_id:
            mappings.setdefault(row.akun_perkiraan_id, row)
    return mappings


def get_mutasi_kas_bank(db, date_from, date_to, jenis=None):
    items = []
    for key in _cash_mappings(db, jenis):
        report = get_buku_besar(db, key, date_from, date_to)
        items.extend(dict(row, akun=report['akun']['nama']) for row in report['transaksi'])
    items.sort(key=lambda r: (r['tanggal'], r['no_jurnal'], r['akun']))
    return {'periode': gl.period(date_from, date_to), 'transaksi': items}


def get_rekap_kas_bank(db, date_from, date_to):
    opening = gl.totals(db, before=date_from)
    movement = gl.totals(db, date_from, date_to)
    items = []
    for key, mapping in _cash_mappings(db).items():
        account = db.get(gl.Account, key)
        row = movement.get(key, {'debit': gl.ZERO, 'kredit': gl.ZERO})
        amount = gl.net(opening.get(key))
        items.append({'kode': account.kode, 'nama': account.nama, 'jenis': mapping.jenis,
                      'saldo_awal': amount, 'total_masuk': row['debit'], 'total_keluar': row['kredit'],
                      'saldo_akhir': amount+gl.net(row)})
    return {'periode': gl.period(date_from, date_to), 'akun': items}


def get_dashboard_laba_rugi(db, bulan, tahun):
    report = get_laba_rugi(db, *gl.month_bounds(tahun, bulan))
    return {name: report['total_'+name] for name in ('pendapatan', 'hpp', 'beban')} | {
        name: report[name] for name in ('laba_kotor', 'laba_bersih')}


def get_dashboard_cashflow(db, bulan, tahun):
    start, end = gl.month_bounds(tahun, bulan)
    journals, ids = cashflow.cash_journals(db, start, end)
    amounts = [sum((r.debit-r.kredit for r in j.details if r.akun_perkiraan_id in ids), gl.ZERO) for j in journals]
    return {'saldo_awal': cashflow.cash_balance(db, ids, before=start),
            'penerimaan': sum((max(v, gl.ZERO) for v in amounts), gl.ZERO),
            'pengeluaran': sum((max(-v, gl.ZERO) for v in amounts), gl.ZERO),
            'saldo_akhir': cashflow.cash_balance(db, ids, end=end)}


def get_dashboard_beban_biaya(db, bulan, tahun):
    rows = _saldo_per_akun_list(db, gl.HeaderCOA.BEBAN, *gl.month_bounds(tahun, bulan))
    return {'items': [{'nama_beban': r['nama_akun'], 'jumlah': r['total']} for r in sorted(rows, key=lambda r: r['total'], reverse=True)]}


def get_dashboard_tren_penjualan(db, bulan, tahun):
    labels = ('Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des')
    items = []
    for offset in range(5, -1, -1):
        year, month = divmod(tahun*12+bulan-1-offset, 12)
        start, end = gl.month_bounds(year, month+1)
        active = settlement.active_documents(db, SalesInvoice, end)
        active = gl.apply_scope(db, active, gl.Journal)
        total = db.query(func.coalesce(func.sum(SalesInvoice.grand_total), 0)).filter(
            SalesInvoice.id.in_(active.scalar_subquery()), SalesInvoice.tanggal >= start, SalesInvoice.tanggal <= end).scalar()
        items.append({'bulan': f'{labels[month]} {year}', 'total': total})
    return {'items': items}


def get_dashboard_faktur_jatuh_tempo(db, as_of=None):
    report = aging(db, 'piutang', as_of or settlement.today())
    items = [dict(no_faktur=r['no_dokumen'], pelanggan=p['nama'], jumlah=r['nilai'],
                  jatuh_tempo=r['jatuh_tempo'], status='JATUH TEMPO' if r['umur_hari'] > 0 else r['status_pembayaran'])
             for p in report['items'] for r in p['rincian']]
    items.sort(key=lambda r: (r['jatuh_tempo'], r['no_faktur']))
    return {'items': items[:20]}


def get_dashboard_aktivitas_terbaru(db, as_of=None):
    q = gl.posted(db, db.query(gl.Journal), end=gl.day_end(as_of or settlement.today()))
    rows = q.order_by(gl.Journal.tanggal.desc(), gl.Journal.created_at.desc(), gl.Journal.id.desc()).limit(10).all()
    return {'items': [{'tipe': j.tipe_transaksi or 'JURNAL', 'deskripsi': j.keterangan or '', 'nomor': j.no_jurnal,
                       'tanggal': gl.local_datetime(j.tanggal).date().isoformat(), 'jumlah': j.total_debit} for j in rows]}


def validate_reports(db, date_from, date_to):
    trial = get_neraca_saldo(db, date_from, date_to)
    balance = get_neraca(db, date_to)
    equity = get_perubahan_modal(db, date_from, date_to)
    cash = get_arus_kas(db, date_from, date_to)
    # Include all history contributing to the closing balances, even zero-net corruption.
    journals = gl.posted(db, db.query(gl.Journal), end=date_to).options(
        selectinload(gl.Journal.details).selectinload(gl.Line.akun_perkiraan)).all()
    invalid = []
    for j in journals:
        debit = sum((r.debit for r in j.details), gl.ZERO)
        credit = sum((r.kredit for r in j.details), gl.ZERO)
        if (debit != credit or debit != j.total_debit or credit != j.total_kredit or len(j.details) < 2 or
            any(r.debit < 0 or r.kredit < 0 or (r.debit > 0) == (r.kredit > 0) or
                r.akun_perkiraan.tingkat != gl.TingkatAkun.DETAIL for r in j.details)):
            invalid.append({'journal_id': j.id, 'no_jurnal': j.no_jurnal})
    checks = {'neraca_saldo_mutasi': trial['selisih'],
              'neraca_saldo_akhir': trial['total_saldo_debit']-trial['total_saldo_kredit'],
              'persamaan_neraca': balance['selisih'], 'perubahan_modal': equity['selisih_rekonsiliasi'],
              'arus_kas': cash['selisih_rekonsiliasi']}
    return {'periode': gl.period(date_from, date_to), 'valid': not invalid and not any(checks.values()),
            'checks': checks, 'jurnal_tidak_valid': invalid, 'klasifikasi_arus_kas_lengkap': cash['klasifikasi_lengkap'],
            'jumlah_jurnal_belum_diklasifikasi': cash['jumlah_jurnal_belum_diklasifikasi']}
