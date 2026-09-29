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


# ============================================================
# Revisi Arus Kas — helpers identitas COA & alokasi
# ============================================================

def _cash_movement_rows(journal, ids):
    """Line jurnal yang menyentuh akun kas/bank (non-zero)."""
    return [r for r in journal.details if r.akun_perkiraan_id in ids and r.debit != r.kredit]


def _counter_movement_rows(journal, ids):
    """Line jurnal non-kas dengan nilai (akun lawan kas)."""
    return [r for r in journal.details if r.akun_perkiraan_id not in ids and r.debit != r.kredit]


def _cash_account_identity(cash_rows):
    """Identitas akun kas/bank yang terdampak jurnal.

    Return (id, kode, nama). Jurnal multi-bank (beberapa akun kas bergerak)
    → (None, None, 'Multi kas/bank') supaya frontend tidak salah label.
    """
    distinct = {r.akun_perkiraan_id for r in cash_rows}
    if len(distinct) == 1:
        acc = cash_rows[0].akun_perkiraan
        return (acc.id, acc.kode, acc.nama)
    return (None, None, 'Multi kas/bank')


def _counter_snapshot(counter_rows):
    """[(account, delta_kredit_debit, keterangan_line)] untuk akun lawan."""
    return [(r.akun_perkiraan, r.kredit - r.debit, r.keterangan) for r in counter_rows]


def _allocate_cashflow_rows(cash_delta, counters):
    """Alokasi nilai arus kas ke baris per akun lawan.

    Return list of dict: {account, jumlah, line_description, allocation_status,
    counter_accounts}.

    Aturan (mempertahankan total == cash_delta, tanpa angka palsu):
    - Tidak ada akun lawan → satu baris mixed tanpa counter detail.
    - Jumlah akun lawan tidak match cash_delta (jurnal tidak balance) →
      satu baris MIXED_UNALLOCATED senilai cash_delta (review, tidak dipaksa
      split).
    - Satu akun lawan → EXACT dengan identitas akun tersebut.
    - Beberapa akun lawan, semua searah dengan net cash → split per akun,
      masing-masing EXACT (SUM = cash_delta).
    - Debit/kredit lawan campuran (ada lawan berlawanan arah) → satu baris
      MIXED_UNALLOCATED senilai cash_delta + counter_accounts detail.
    """
    counter_accounts = [
        {'account_id': acc.id, 'account_code': acc.kode, 'account_name': acc.nama, 'jumlah': value}
        for acc, value, _ in counters
    ]
    if not counters:
        return [{'account': None, 'jumlah': cash_delta, 'line_description': None,
                 'allocation_status': 'MIXED_UNALLOCATED', 'counter_accounts': [],
                 'force_unclassified': True}]

    counter_sum = sum((value for _, value, _ in counters), gl.ZERO)
    if counter_sum != cash_delta:
        # Jurnal tidak balance — jangan percaya alokasi; seluruh nilai masuk
        # review (BELUM_DIKLASIFIKASIKAN) kecuali ada override jurnal eksplisit.
        return [{'account': None, 'jumlah': cash_delta, 'line_description': None,
                 'allocation_status': 'MIXED_UNALLOCATED', 'counter_accounts': counter_accounts,
                 'force_unclassified': True}]

    if len(counters) == 1:
        acc, value, keterangan = counters[0]
        return [{'account': acc, 'jumlah': value, 'line_description': keterangan,
                 'allocation_status': 'EXACT', 'counter_accounts': []}]

    if all(value * cash_delta > 0 for _, value, _ in counters):
        return [{'account': acc, 'jumlah': value, 'line_description': keterangan,
                 'allocation_status': 'EXACT', 'counter_accounts': []}
                for acc, value, keterangan in counters]

    return [{'account': None, 'jumlah': cash_delta, 'line_description': None,
             'allocation_status': 'MIXED_UNALLOCATED', 'counter_accounts': counter_accounts}]


def _classify_rows(rows, counters, explicit, source, category):
    """Kategori tiap baris alokasi (tanpa menghilangkan detail akun lawan).

    - Override jurnal eksplisit → kategori override untuk semua baris
      (identitas akun tetap ditampilkan).
    - ASET_KAPITALISASI/ASET_PELEPASAN → INVESTASI.
    - Baris EXACT → kategori akun lawan baris tersebut.
    - Baris MIXED → kategori tunggal jika semua akun lawan satu kategori,
      selain itu BELUM_DIKLASIFIKASIKAN (kecuali override jurnal).
    """
    if explicit:
        return [explicit for _ in rows]
    if source.tipe_transaksi in ('ASET_KAPITALISASI', 'ASET_PELEPASAN'):
        return ['INVESTASI' for _ in rows]
    result = []
    for row in rows:
        if row.get('force_unclassified'):
            result.append('BELUM_DIKLASIFIKASIKAN')
            continue
        if row['account'] is not None:
            result.append(category(row['account']))
            continue
        kinds = {category(acc) for acc, _, _ in counters}
        result.append(next(iter(kinds)) if len(kinds) == 1 else 'BELUM_DIKLASIFIKASIKAN')
    return result


def _build_display_item(journal, row, kind, cash_identity, reversal_map):
    """Bangun payload item arus kas dengan identitas COA lengkap."""
    account = row['account']
    if account is not None:
        account_id, account_code, account_name = account.id, account.kode, account.nama
        nama = f"{account_code} — {account_name}"
        line_description = row['line_description']
    else:
        account_id = account_code = None
        account_name = 'Transaksi multi-akun'
        nama = journal.keterangan or journal.no_jurnal or 'Transaksi multi-akun'
        line_description = None

    jumlah = row['jumlah']
    return {
        # legacy (backward compatible)
        'nama': nama,
        'jumlah': jumlah,
        'journal_id': journal.id,
        'no_jurnal': journal.no_jurnal,
        # identitas COA & narasi
        'category': kind,
        'direction': 'INFLOW' if jumlah > 0 else 'OUTFLOW',
        'account_id': account_id,
        'account_code': account_code,
        'account_name': account_name,
        'cash_account_id': cash_identity[0],
        'cash_account_code': cash_identity[1],
        'cash_account_name': cash_identity[2],
        'transaction_description': journal.keterangan,
        'line_description': line_description,
        'source_module': journal.ref_module.value if journal.ref_module else None,
        'source_id': str(journal.ref_id) if journal.ref_id else None,
        'source_no': journal.ref_no,
        'tanggal': gl.local_datetime(journal.tanggal).date().isoformat(),
        'reversal_of_id': journal.reversal_of_id,
        'reversal_of_no_jurnal': reversal_map.get(journal.reversal_of_id),
        'allocation_status': row['allocation_status'],
        'counter_accounts': row['counter_accounts'],
    }


def _aggregate_by_account(items):
    """Ringkasan group per akun lawan untuk satu section."""
    groups = {}
    for item in items:
        key = item['account_id'] or '__mixed__'
        group = groups.setdefault(key, {
            'account_id': item['account_id'],
            'account_code': item['account_code'],
            'account_name': item['account_name'] if item['account_id'] else 'Multi-akun / tanpa alokasi',
            'inflow': gl.ZERO, 'outflow': gl.ZERO, 'net': gl.ZERO, 'transaction_count': 0,
        })
        jumlah = item['jumlah']
        if jumlah > 0:
            group['inflow'] += jumlah
        else:
            group['outflow'] += -jumlah
        group['net'] += jumlah
        group['transaction_count'] += 1
    return sorted(groups.values(), key=lambda g: (g['account_code'] or 'zzz', g['account_name']))


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

    # Trace no_jurnal sumber untuk jurnal pembalik (batch, hindari N+1).
    reversal_ids = {j.reversal_of_id for j in journals if j.reversal_of_id}
    reversal_map = dict(db.query(gl.Journal.id, gl.Journal.no_jurnal).filter(
        gl.Journal.id.in_(reversal_ids)).all()) if reversal_ids else {}

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
        cash_rows = _cash_movement_rows(journal, ids)
        amount = sum((r.debit - r.kredit for r in cash_rows), gl.ZERO)
        if amount == 0:  # Includes internal cash transfers.
            continue
        cash_identity = _cash_account_identity(cash_rows)

        source = db.get(gl.Journal, journal.reversal_of_id) if journal.reversal_of_id else journal
        explicit = overrides.get(('JOURNAL', source.id))
        counters = _counter_snapshot(_counter_movement_rows(journal, ids))

        rows = _allocate_cashflow_rows(amount, counters)
        row_categories = _classify_rows(rows, counters, explicit, source, category)
        if any(c == 'BELUM_DIKLASIFIKASIKAN' for c in row_categories):
            unknown += 1
        for row, kind in zip(rows, row_categories):
            sections[kind]['items'].append(
                _build_display_item(journal, row, kind, cash_identity, reversal_map))
            sections[kind]['total'] += row['jumlah']
    opening = cash_balance(db, ids, before=start)
    ending = cash_balance(db, ids, end=end)
    change = sum((r['total'] for r in sections.values()), gl.ZERO)

    def section_payload(name):
        section = sections[name]
        return {'items': section['items'], 'total': section['total'],
                'account_groups': _aggregate_by_account(section['items'])}

    return {'periode': gl.period(start, end), 'operasional': section_payload('OPERASIONAL'),
            'investasi': section_payload('INVESTASI'), 'pembiayaan': section_payload('PEMBIAYAAN'),
            'belum_diklasifikasikan': section_payload('BELUM_DIKLASIFIKASIKAN'),
            'jumlah_jurnal_belum_diklasifikasi': unknown, 'klasifikasi_lengkap': unknown == 0,
            'net_change': change, 'saldo_awal': opening, 'saldo_akhir': ending,
            'selisih_rekonsiliasi': opening + change - ending}
