"""Cash-flow classification with explicit, audited overrides and reconciliation."""
from sqlalchemy.orm import selectinload
from app.models import KasBankAkun, Pelanggan, Supplier, SalesInvoice, PurchaseInvoice
from app.models.master.setting_akun import SettingAkun
from app.models.organization import CashFlowClassification
from app.services import reporting_ledger as gl
from app.services.accounting_control import atomic_accounting_write
from app.services.organization_service import audit

CATEGORIES = ('OPERASIONAL', 'INVESTASI', 'PEMBIAYAAN', 'BELUM_DIKLASIFIKASIKAN')


def cash_accounts(db):
    return {r[0] for r in db.query(KasBankAkun.akun_perkiraan_id).all() if r[0]}


@atomic_accounting_write
def set_classification(db, data, user):
    model = gl.Account if data['target_type'] == 'ACCOUNT' else gl.Journal
    obj = db.get(model, data['target_id'])
    if obj is None:
        raise ValueError('Akun/jurnal tidak ditemukan')
    if model == gl.Account and (obj.tingkat != gl.TingkatAkun.DETAIL or obj.id in cash_accounts(db)):
        raise ValueError('Klasifikasi akun memakai akun DETAIL lawan kas/bank')
    if model == gl.Journal and (obj.status != gl.StatusJurnal.POSTED or obj.reversal_of_id):
        raise ValueError('Klasifikasi jurnal hanya pada jurnal sumber POSTED; pembalik mengikuti sumber')
    row = db.query(CashFlowClassification).filter_by(target_type=data['target_type'], target_id=obj.id).first()
    before = {'category': row.category} if row else None
    if row is None:
        row = CashFlowClassification(**data)
        db.add(row)
    else:
        row.category = data['category']
    db.flush()
    audit(db, 'cash_flow_classification', row.id, 'classify', before, data, user)
    return row


def cash_journals(db, start, end):
    ids = cash_accounts(db)
    q = db.query(gl.Journal).filter(gl.Journal.details.any(gl.Line.akun_perkiraan_id.in_(ids)))
    return gl.posted(db, q, start, end).options(
        selectinload(gl.Journal.details).selectinload(gl.Line.akun_perkiraan)
    ).order_by(gl.Journal.tanggal, gl.Journal.no_jurnal, gl.Journal.id).all(), ids


def cash_balance(db, ids, end=None, before=None):
    return sum((gl.net(r) for key, r in gl.totals(db, end=end, before=before).items() if key in ids), gl.ZERO)


def report(db, start, end):
    journals, ids = cash_journals(db, start, end)
    overrides = {(r.target_type, r.target_id): r.category for r in db.query(CashFlowClassification).all()}
    operating = {r[0] for r in db.query(Pelanggan.akun_piutang_id).all()} | {r[0] for r in db.query(Supplier.akun_hutang_id).all()}
    # Invoice snapshots preserve historical AR/AP classification after master remapping.
    for model in (SalesInvoice, PurchaseInvoice):
        operating |= {r[0] for r in db.query(model.akun_kontrol_id).distinct().all() if r[0]}
    operating |= {r.akun_perkiraan_id for r in db.query(SettingAkun).filter(SettingAkun.key.in_(
        ('PIUTANG_USAHA', 'HUTANG_USAHA', 'PPN_MASUKAN', 'PPN_KELUARAN', 'PEMBELIAN',
         'PERSEDIAAN_BAHAN_BAKU', 'PERSEDIAAN_WIP', 'PERSEDIAAN_BARANG_JADI', 'PERSEDIAAN_BAHAN_PEMBANTU'))).all()}
    sections = {name: {'items': [], 'total': gl.ZERO} for name in CATEGORIES}

    def category(account):
        explicit = overrides.get(('ACCOUNT', account.id))
        if explicit:
            return explicit
        if account.id in operating or account.header in gl.PROFIT:
            return 'OPERASIONAL'
        # Assets and liabilities also contain loans, deposits, advances, etc.
        # Do not infer their category from their COA header alone.
        return 'BELUM_DIKLASIFIKASIKAN'

    unknown = 0
    for journal in journals:
        amount = sum((r.debit-r.kredit for r in journal.details if r.akun_perkiraan_id in ids), gl.ZERO)
        if amount == 0:  # Includes internal cash transfers.
            continue
        source = db.get(gl.Journal, journal.reversal_of_id) if journal.reversal_of_id else journal
        explicit = overrides.get(('JOURNAL', source.id))
        if explicit:
            parts = [(explicit, amount)]
        elif source.tipe_transaksi in ('ASET_KAPITALISASI', 'ASET_PELEPASAN'):
            parts = [('INVESTASI', amount)]
        else:
            counters = [(category(r.akun_perkiraan), r.kredit-r.debit)
                        for r in journal.details if r.akun_perkiraan_id not in ids and r.debit != r.kredit]
            kinds = {c for c, _ in counters}
            if sum((v for _, v in counters), gl.ZERO) != amount:
                parts = [('BELUM_DIKLASIFIKASIKAN', amount)]
            elif len(kinds) == 1:
                parts = [(next(iter(kinds)), amount)]
            elif counters and all(v * amount > 0 for _, v in counters):
                parts = counters
            else:
                parts = [('BELUM_DIKLASIFIKASIKAN', amount)]
        if any(c == 'BELUM_DIKLASIFIKASIKAN' for c, _ in parts):
            unknown += 1
        for kind, value in parts:
            sections[kind]['items'].append({'nama': journal.keterangan or journal.no_jurnal,
                'jumlah': value, 'journal_id': journal.id, 'no_jurnal': journal.no_jurnal})
            sections[kind]['total'] += value
    opening = cash_balance(db, ids, before=start)
    ending = cash_balance(db, ids, end=end)
    change = sum((r['total'] for r in sections.values()), gl.ZERO)
    return {'periode': gl.period(start, end), 'operasional': sections['OPERASIONAL'],
            'investasi': sections['INVESTASI'], 'pembiayaan': sections['PEMBIAYAAN'],
            'belum_diklasifikasikan': sections['BELUM_DIKLASIFIKASIKAN'],
            'jumlah_jurnal_belum_diklasifikasi': unknown, 'klasifikasi_lengkap': unknown == 0,
            'net_change': change, 'saldo_awal': opening, 'saldo_akhir': ending,
            'selisih_rekonsiliasi': opening + change - ending}
