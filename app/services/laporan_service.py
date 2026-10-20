"""Financial reports derive from the same posted general ledger and scope."""
from datetime import date, datetime, timedelta
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
    # M-02: klasifikasi per akun (level presentasi), bukan per header COA saja:
    # - PENDAPATAN dengan report_group OTHER_INCOME (421xxx bunga/sewa/selisih kurs)
    #   → grup "pendapatan_lain" (pendapatan lain-lain), bukan pendapatan usaha.
    # - HPP dengan kode 52xxxx (Biaya Overhead Pabrik, mis. 521002 penyusutan pabrik)
    #   → grup "beban" (beban operasional), bukan HPP — sesuai temuan audit E2E.
    #   Data COA TIDAK diubah; reclass hanya di level penyajian laporan.
    groups = {name: [] for name in ('pendapatan', 'pendapatan_lain', 'hpp', 'beban')}
    for r in rows.values():
        account, value = r['account'], gl.net(r)
        if not value:  # lewati akun net 0 (sama seperti header_items)
            continue
        header = account.header
        if header == gl.HeaderCOA.PENDAPATAN:
            name = 'pendapatan_lain' if account.report_group == 'OTHER_INCOME' else 'pendapatan'
        elif header == gl.HeaderCOA.HPP:
            # Overhead pabrik (52xxxx) dipresentasikan sebagai beban operasional.
            name = 'beban' if account.kode.startswith('52') else 'hpp'
        elif header == gl.HeaderCOA.BEBAN:
            name = 'beban'
        else:
            continue
        sign = -1 if name in ('pendapatan', 'pendapatan_lain') else 1
        groups[name].append({'kode_akun': account.kode, 'nama_akun': account.nama, 'total': sign * value})
    totals = {'total_'+name: sum((r['total'] for r in items), gl.ZERO) for name, items in groups.items()}
    gross = totals['total_pendapatan'] - totals['total_hpp']
    # laba_usaha = pendapatan usaha − HPP − beban (beban kini memuat overhead pabrik 52xxxx).
    operating = gross - totals['total_beban']
    # laba_bersih TIDAK berubah oleh reclass (hanya pindah wadah):
    #   laba_usaha + pendapatan_lain
    #   = (pendapatan_usaha − hpp − beban) + pendapatan_lain
    #   = (pendapatan_usaha + pendapatan_lain) − hpp − beban
    #   = total PENDAPATAN (semua) − hpp − beban   ← rumus lama laba_bersih.
    return {'periode': gl.period(date_from, date_to), **groups, **totals,
            'laba_kotor': gross, 'laba_usaha': operating,
            'laba_bersih': operating + totals['total_pendapatan_lain']}


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
    # m-10: urutkan tanggal ASC lalu kronologi posting (created_at/id) ASC supaya
    # baris terakhir adalah transaksi terakhir periode (saldo berjalan konsisten).
    rows = gl.posted(db, q, date_from, date_to).order_by(
        gl.Journal.tanggal, gl.Journal.created_at, gl.Journal.id, gl.Line.id).all()
    balance, debit, credit, items = opening, gl.ZERO, gl.ZERO, []
    for line, journal in rows:
        balance += sign * (line.debit-line.kredit)
        debit += line.debit
        credit += line.kredit
        # Kunci urut internal (dipakai get_mutasi_kas_bank; di-drop response schema).
        items.append({'tanggal': gl.local_datetime(journal.tanggal).date().isoformat(), 'no_jurnal': journal.no_jurnal,
                      'deskripsi': line.keterangan or journal.keterangan or '', 'debit': line.debit,
                      'kredit': line.kredit, 'saldo': balance,
                      '_created_at': journal.created_at, '_journal_id': journal.id, '_line_id': line.id})
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
    # m-10: urut tanggal ASC lalu kronologi posting (created_at/id) ASC — sebelumnya
    # pembanding kedua no_jurnal sehingga jurnal back-date muncul di urutan yang salah
    # dan baris terakhir belum tentu transaksi/saldo terakhir.
    items.sort(key=lambda r: (r['tanggal'], r['_created_at'], r['_journal_id'], r['_line_id']))
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
    # M-02: samakan klasifikasi dengan get_laba_rugi — overhead pabrik (52xxxx,
    # header HPP) dipresentasikan sebagai beban operasional di widget ini juga.
    bounds = gl.month_bounds(tahun, bulan)
    rows = _saldo_per_akun_list(db, gl.HeaderCOA.BEBAN, *bounds)
    rows += [r for r in _saldo_per_akun_list(db, gl.HeaderCOA.HPP, *bounds)
             if r['kode_akun'].startswith('52')]
    return {'items': [{'nama_beban': r['nama_akun'], 'jumlah': r['total']} for r in sorted(rows, key=lambda r: r['total'], reverse=True)]}


def get_dashboard_tren_penjualan(db, bulan, tahun):
    """Tren penjualan 6 bulan terakhir.

    Phase 10 — Master Roadmap §27:
        "Jika memakai Invoice Grand Total, label = Invoice Turnover"
        "Jangan label invoice grand total sebagai accounting Revenue"

    Catatan: Widget ini menampilkan INVOICE TURNOVER (sum of SalesInvoice.grand_total),
    BUKAN accounting Revenue dari GL. Frontend harus label sebagai "Invoice Turnover"
    atau "Penjualan (Faktur)", bukan "Revenue" atau "Pendapatan".

    Untuk accounting Revenue yang akurat, gunakan endpoint /laporan/laba-rugi
    yang derive dari GL.
    """
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
    return {'items': items, 'label_note': 'Invoice Turnover (bukan GL Revenue). Untuk Revenue akurat, gunakan /laporan/laba-rugi.'}


def get_dashboard_faktur_jatuh_tempo(db, as_of=None, overdue_only=False):
    """Widget faktur jatuh tempo.

    Phase 10 — Master Roadmap §27:
        "Overdue widget hanya overdue invoices"
        "Remaining outstanding, bukan original invoice amount"

    Parameter:
        overdue_only: True = hanya tampilkan invoice yang overdue (jatuh_tempo < as_of)
                      False (default) = tampilkan semua faktur jatuh tempo (existing behavior)

    Perubahan Phase 10:
    - Gunakan sisa_tagihan (remaining outstanding) bukan nilai (original amount)
    - Tambah filter overdue_only
    - Tambah flag 'is_overdue' di setiap item
    """
    report = aging(db, 'piutang', as_of or settlement.today())
    items = []
    as_of_date = as_of or settlement.today()
    as_of_day = settlement.local_day(as_of_date)

    for p in report['items']:
        for r in p['rincian']:
            # Phase 10: gunakan sisa_tagihan (remaining outstanding) bukan nilai (original)
            remaining = r.get('sisa_tagihan', r.get('nilai', 0))
            jatuh_tempo = r.get('jatuh_tempo')
            is_overdue = False
            if jatuh_tempo:
                try:
                    jt_date = jatuh_tempo if isinstance(jatuh_tempo, date) else datetime.strptime(str(jatuh_tempo), '%Y-%m-%d').date()
                    is_overdue = jt_date < as_of_day and remaining > 0
                except (ValueError, TypeError):
                    is_overdue = False

            # Skip kalau overdue_only=True dan not overdue
            if overdue_only and not is_overdue:
                continue

            items.append(dict(
                no_faktur=r['no_dokumen'],
                pelanggan=p['nama'],
                jumlah=remaining,  # Phase 10: remaining outstanding, bukan original
                jatuh_tempo=str(jatuh_tempo) if jatuh_tempo else '-',
                status='OVERDUE' if is_overdue else (r.get('status_pembayaran', '')),
                is_overdue=is_overdue,
                original_nilai=r.get('nilai', 0),  # keep original for reference
            ))

    # Sort: overdue dulu, lalu by jatuh_tempo
    items.sort(key=lambda r: (not r['is_overdue'], r['jatuh_tempo'], r['no_faktur']))
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


# ==========================================
# Update AUD-17 — Rekap Pajak PPN & PPh 23
# ==========================================

from app.models import PurchaseInvoice, SalesRetur as _SalesRetur, PurchaseRetur as _PurchaseRetur
from app.models import PenerimaanKas as _PenerimaanKas, PembayaranKas as _PembayaranKas
from app.models import PaymentAllocation as _PaymentAllocation
from app.models.transaksi.kas_bank.kas_identifikasi import (
    KasIdentifikasi as _KasIdentifikasi,
    KasIdentifikasiRincian as _KasIdentifikasiRincian,
)
from app.models import Pelanggan as _Pelanggan, Supplier as _Supplier, JurnalUmum as _JurnalUmum


def _tax_day(value):
    """Tanggal lokal (Asia/Jakarta) tanpa waktu — untuk baris rekap pajak."""
    return gl.local_datetime(value).date() if value is not None else None
from sqlalchemy import or_ as _or_


def _active_tax_documents(db, model, date_from, date_to):
    """Dokumen pajak efektif: posted (ber-jurnal POSTED) dan belum dibatalkan
    (tidak punya jurnal pembalik POSTED — pola settlement_service.active_documents),
    dalam rentang tanggal. Scope perusahaan (AUD-12) diterapkan bila ada."""
    from sqlalchemy.orm import aliased as _aliased
    reversal = _aliased(_JurnalUmum)
    query = (
        db.query(model)
        .join(_JurnalUmum, model.jurnal_umum_id == _JurnalUmum.id)
        .filter(_JurnalUmum.status == 'POSTED')
        .filter(model.tanggal >= date_from, model.tanggal <= date_to)
        .filter(~db.query(reversal.id)
                .filter(reversal.reversal_of_id == _JurnalUmum.id,
                        reversal.status == 'POSTED').exists())
    )
    scope = gl._report_scope(db) if hasattr(gl, '_report_scope') else None
    company_id = (scope or {}).get('company_id') if scope else None
    if company_id is not None and hasattr(model, 'company_id'):
        query = query.filter(model.company_id == company_id)
    return query.order_by(model.tanggal, model.id).all()


def get_rekap_ppn(db, date_from, date_to):
    """Rekap PPN per masa pajak — DPP & PPN keluaran (SI), masukan (PI),
    beserta koreksi retur penjualan/pembelian. Dokumen dibatalkan (jurnal
    dibalik) otomatis dikecualikan."""
    def _dpp(obj):
        ppn = obj.total_ppn or gl.ZERO
        return (obj.grand_total or gl.ZERO) - ppn

    rincian = []
    for inv in _active_tax_documents(db, SalesInvoice, date_from, date_to):
        rincian.append({
            'tanggal': _tax_day(inv.tanggal), 'no_dokumen': inv.no_invoice,
            'pihak': inv.pelanggan.nama if inv.pelanggan else None,
            'jenis': 'KELUARAN', 'dpp': _dpp(inv), 'ppn': inv.total_ppn or gl.ZERO,
        })
    for inv in _active_tax_documents(db, PurchaseInvoice, date_from, date_to):
        rincian.append({
            'tanggal': _tax_day(inv.tanggal), 'no_dokumen': inv.no_form,
            'pihak': inv.supplier.nama if inv.supplier else None,
            'jenis': 'MASUKAN', 'dpp': _dpp(inv), 'ppn': inv.total_ppn or gl.ZERO,
        })
    for r in _active_tax_documents(db, _SalesRetur, date_from, date_to):
        rincian.append({
            'tanggal': _tax_day(r.tanggal), 'no_dokumen': r.no_retur,
            'pihak': r.pelanggan.nama if getattr(r, 'pelanggan', None) else None,
            'jenis': 'RETUR_KELUARAN', 'dpp': -_dpp(r), 'ppn': -(r.total_ppn or gl.ZERO),
        })
    for r in _active_tax_documents(db, _PurchaseRetur, date_from, date_to):
        rincian.append({
            'tanggal': _tax_day(r.tanggal), 'no_dokumen': r.no_retur,
            'pihak': r.supplier.nama if getattr(r, 'supplier', None) else None,
            'jenis': 'RETUR_MASUKAN', 'dpp': -_dpp(r), 'ppn': -(r.total_ppn or gl.ZERO),
        })
    rincian.sort(key=lambda x: (x['tanggal'], x['no_dokumen']))

    masa_map = {}
    for row in rincian:
        masa = f"{row['tanggal'].year:04d}-{row['tanggal'].month:02d}"
        m = masa_map.setdefault(masa, {
            'masa': masa, 'dpp_keluaran': gl.ZERO, 'ppn_keluaran': gl.ZERO,
            'dpp_masukan': gl.ZERO, 'ppn_masukan': gl.ZERO,
            'dpp_retur_keluaran': gl.ZERO, 'ppn_retur_keluaran': gl.ZERO,
            'dpp_retur_masukan': gl.ZERO, 'ppn_retur_masukan': gl.ZERO, 'ppn_neto': gl.ZERO,
        })
        jenis = row['jenis'].lower()  # keluaran / masukan / retur_keluaran / retur_masukan
        m[f'dpp_{jenis}'] += row['dpp']
        m[f'ppn_{jenis}'] += row['ppn']

    ringkasan = []
    total = {
        'masa': 'TOTAL', 'dpp_keluaran': gl.ZERO, 'ppn_keluaran': gl.ZERO,
        'dpp_masukan': gl.ZERO, 'ppn_masukan': gl.ZERO,
        'dpp_retur_keluaran': gl.ZERO, 'ppn_retur_keluaran': gl.ZERO,
        'dpp_retur_masukan': gl.ZERO, 'ppn_retur_masukan': gl.ZERO, 'ppn_neto': gl.ZERO,
    }
    for masa in sorted(masa_map):
        m = masa_map[masa]
        m['ppn_neto'] = m['ppn_keluaran'] - m['ppn_masukan']
        ringkasan.append(m)
        for k in total:
            if k != 'masa':
                total[k] += m[k]
    return {
        'periode': {'dari': _tax_day(date_from).isoformat(), 'sampai': _tax_day(date_to).isoformat()},
        'ringkasan_masa': ringkasan,
        'rincian': rincian,
        'total': total,
    }


def get_rekap_pph23(db, date_from, date_to):
    """Rekap PPh 23 per pihak — dua sisi:
    - DIPOTONG_DARI_KITA (AR): pelanggan memotong PPh23 dari pembayaran kita (Dr 116003).
    - KITA_POTONG (AP): kita memotong PPh23 dari pembayaran supplier (Cr 213002).
    Sumber: alokasi pelunasan (payment_allocation) + identifikasi kas (rincian)."""
    from app.models import SalesInvoice as _SI, PurchaseInvoice as _PI

    scope = gl._report_scope(db) if hasattr(gl, '_report_scope') else None
    company_id = (scope or {}).get('company_id') if scope else None

    # --- sumber 1: alokasi pelunasan (penerimaan AR / pembayaran AP) ---
    # Iterasi alokasi ber-pph23 lalu validasi efektivitas dokumen kas sumber
    # (posted + tanpa jurnal pembalik) dan rentang tanggal.
    d0, d1 = _tax_day(date_from), _tax_day(date_to)
    rows = []
    for a in (db.query(_PaymentAllocation)
              .filter(_PaymentAllocation.pph23 > 0).all()):
        if a.penerimaan_id is not None:
            kas = db.get(_PenerimaanKas, a.penerimaan_id)
        elif a.pembayaran_id is not None:
            kas = db.get(_PembayaranKas, a.pembayaran_id)
        else:
            continue
        if kas is None or kas.jurnal_umum_id is None:
            continue
        jrn = db.get(_JurnalUmum, kas.jurnal_umum_id)
        if jrn is None or jrn.status != 'POSTED':
            continue
        reversed_ = (db.query(_JurnalUmum.id)
                     .filter(_JurnalUmum.reversal_of_id == jrn.id,
                             _JurnalUmum.status == 'POSTED').first())
        if reversed_:
            continue  # dokumen kas dibatalkan → alokasi non-efektif
        tgl = _tax_day(kas.tanggal)
        if not (d0 <= tgl <= d1):
            continue
        if company_id is not None and getattr(kas, 'company_id', None) != company_id:
            continue  # scope perusahaan (AUD-12)
        if a.penerimaan_id is not None:
            pihak = db.get(_Pelanggan, kas.pelanggan_id) if getattr(kas, 'pelanggan_id', None) else None
            sisi, no_bukti = 'DIPOTONG_DARI_KITA', kas.no_bukti
            inv = db.get(_SI, a.sales_invoice_id) if a.sales_invoice_id else None
            no_inv = inv.no_invoice if inv else None
        else:
            pihak = db.get(_Supplier, kas.supplier_id) if getattr(kas, 'supplier_id', None) else None
            sisi, no_bukti = 'KITA_POTONG', kas.no_bukti
            inv = db.get(_PI, a.purchase_invoice_id) if a.purchase_invoice_id else None
            no_inv = inv.no_form if inv else None
        rows.append({
            'tanggal': tgl, 'pihak': pihak.nama if pihak else None,
            'npwp': (pihak.npwp if pihak else None),
            'no_bukti': no_bukti, 'no_invoice': no_inv,
            'dasar': a.nilai or gl.ZERO, 'pph23': a.pph23 or gl.ZERO,
            'sumber': 'PELUNASAN', 'sisi': sisi,
        })

    # --- sumber 2: identifikasi kas (rincian PIUTANG / HUTANG) ---
    q = (db.query(_KasIdentifikasiRincian)
         .join(_KasIdentifikasi, _KasIdentifikasiRincian.identifikasi_id == _KasIdentifikasi.id)
         .filter(_KasIdentifikasiRincian.pph23 > 0)
         .filter(_KasIdentifikasi.tanggal >= date_from, _KasIdentifikasi.tanggal <= date_to))
    for r in q.all():
        ident = db.get(_KasIdentifikasi, r.identifikasi_id)
        if ident is None:
            continue
        tgl = _tax_day(ident.tanggal)
        if not (d0 <= tgl <= d1):
            continue
        if ident.jenis == 'PIUTANG':
            pihak = db.get(_Pelanggan, ident.pelanggan_id) if ident.pelanggan_id else None
            sisi = 'DIPOTONG_DARI_KITA'
            inv = db.get(_SI, r.sales_invoice_id) if r.sales_invoice_id else None
            no_inv = inv.no_invoice if inv else None
        elif ident.jenis == 'HUTANG':
            pihak = db.get(_Supplier, ident.supplier_id) if ident.supplier_id else None
            sisi = 'KITA_POTONG'
            inv = db.get(_PI, r.purchase_invoice_id) if r.purchase_invoice_id else None
            no_inv = inv.no_form if inv else None
        else:
            continue
        rows.append({
            'tanggal': tgl, 'pihak': pihak.nama if pihak else None,
            'npwp': (pihak.npwp if pihak else None),
            'no_bukti': ident.no_bukti, 'no_invoice': no_inv,
            'dasar': r.nilai or gl.ZERO, 'pph23': r.pph23 or gl.ZERO,
            'sumber': 'IDENTIFIKASI', 'sisi': sisi,
        })

    rows.sort(key=lambda x: (x['tanggal'], x['no_bukti'] or ''))

    # agregasi per pihak + sisi
    agg = {}
    for r in rows:
        key = (r['pihak'] or '(tanpa nama)', r['npwp'] or '', r['sisi'])
        a = agg.setdefault(key, {'pihak': r['pihak'], 'npwp': r['npwp'], 'sisi': r['sisi'],
                                 'total_dasar': gl.ZERO, 'total_pph23': gl.ZERO})
        a['total_dasar'] += r['dasar']
        a['total_pph23'] += r['pph23']
    per_pihak = sorted(agg.values(), key=lambda a: (a['sisi'], a['pihak'] or ''))

    total_dari_kita = sum((r['pph23'] for r in rows if r['sisi'] == 'DIPOTONG_DARI_KITA'), gl.ZERO)
    total_kita_potong = sum((r['pph23'] for r in rows if r['sisi'] == 'KITA_POTONG'), gl.ZERO)

    return {
        'periode': {'dari': _tax_day(date_from).isoformat(), 'sampai': _tax_day(date_to).isoformat()},
        'rincian': rows,
        'per_pihak': per_pihak,
        'total_dipotong_dari_kita': total_dari_kita,
        'total_kita_potong': total_kita_potong,
    }
