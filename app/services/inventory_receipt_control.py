"""GRNI validation + three-way match clearing.

Catatan Audit Re-Audit P0-01:
    Service ``clearing_entries()`` sebelumnya memakai exact full-match logic:
        reject partial receipt / partial invoice / differently priced invoice.

    Padahal bridge table ``purchase_invoice_receipt_match`` sudah ada
    (Roadmap §20) tapi belum di-wire ke service.

    Update ini mengganti logic exact match dengan partial three-way match:
        - Auto-match invoice lines ke receipt lines by (barang_id, supplier)
        - Support: partial receipt, partial invoice, many-to-many matching
        - Validate: matched_qty <= receipt remaining, matched_qty <= invoice remaining
        - Persist match rows ke ``purchase_invoice_receipt_match``
        - Hitung GRNI clearing dari matched_value, BUKAN exact total compare
        - Selisih harga invoice vs receipt → tetap posted (matched_unit_cost pakai invoice price)
"""
from collections import defaultdict
from decimal import Decimal

from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun, SaldoNormal
from app.models.detail.purchase_invoice_receipt_match import PurchaseInvoiceReceiptMatch
from app.models.master.setting_akun import SettingAkun
from app.models.master.supplier import Supplier
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal
from app.services import organization_service
from app.services.reporting_ledger import local_datetime

KEY = 'PENERIMAAN_DALAM_PROSES'
CENT = Decimal('0.01')
# Toleransi selisih nilai (rounding) untuk validasi total debits == basis.
# Di-set ke 1.00 untuk akomodasi rounding per-line (qty * harga_perolehan).
VALUE_TOLERANCE = Decimal('1.00')


def validate_grni_account(db, account_id):
    account = db.get(AkunPerkiraan, account_id)
    if (not account or account.header != HeaderCOA.KEWAJIBAN
            or account.tingkat != TingkatAkun.DETAIL or account.status != 'AKTIF'
            or account.saldo_normal != SaldoNormal.KREDIT
            or getattr(account, 'is_subledger', False)
            or db.query(SettingAkun).filter(SettingAkun.key == 'HUTANG_USAHA',
                                            SettingAkun.akun_perkiraan_id == account_id).first()
            or db.query(Supplier).filter(Supplier.akun_hutang_id == account_id).first()):
        raise ValueError('GRNI harus akun kewajiban DETAIL AKTIF, saldo normal KREDIT, terpisah dari akun utang supplier')
    return account_id


def configured_grni(db):
    # Read current database settings, not process-local caches from another session.
    setting = db.query(SettingAkun).filter_by(key=KEY).first()
    return validate_grni_account(db, setting.akun_perkiraan_id) if setting else None


def validate_link(db, invoice_id, supplier_id, tanggal):
    if invoice_id is None:
        return
    inv = db.get(PurchaseInvoice, invoice_id)
    if not inv:
        raise ValueError('Purchase invoice terkait tidak ditemukan')
    if inv.jurnal_umum_id or str(getattr(inv.status, 'value', inv.status)) in ('SELESAI', 'DIBATALKAN'):
        raise ValueError('Hubungkan penerimaan ke invoice yang belum diposting atau dibatalkan')
    if inv.supplier_id != supplier_id:
        raise ValueError('Supplier penerimaan harus sama dengan invoice')
    if local_datetime(tanggal).date() > local_datetime(inv.tanggal).date():
        raise ValueError('Tanggal penerimaan tidak boleh setelah tanggal invoice terkait')


def validate_execution(db, receipt, grni):
    """Validate goods receipt before execution.

    Phase C fix (Catatan Audit Goods Receipt §5):
    - REMOVED dependency on purchase_invoice_id — Goods Receipt harus independen
    - GRNI sekarang MANDATORY: kalau belum configured, reject (bukan fallback legacy)
    - purchase_invoice_id tetap optional (untuk backward compat), tapi tidak required
    - Validasi invoice link hanya dijalankan kalau purchase_invoice_id diisi
    """
    # === Phase C: GRNI MANDATORY ===
    if not grni:
        raise ValueError(
            'Akun GRNI (PENERIMAAN_DALAM_PROSES) belum di-configure. '
            'Goods Receipt wajib menggunakan GRNI architecture '
            '(Dr Persediaan / Cr GRNI). Configure via PUT /master/setting-akun/PENERIMAAN_DALAM_PROSES. '
            '(Catatan Audit Goods Receipt §5-6: Goods Receipt tidak boleh bergantung pada Purchase Invoice)'
        )

    # === Phase C: invoice link menjadi optional ===
    # Kalau purchase_invoice_id diisi, validasi link. Kalau null, skip (invoice datang kemudian)
    if receipt.purchase_invoice_id:
        validate_link(db, receipt.purchase_invoice_id, receipt.supplier_id, receipt.tanggal)

    # === Validasi PO ===
    if not receipt.purchase_order:
        raise ValueError('Purchase Order wajib ada untuk Goods Receipt')
    if receipt.purchase_order.supplier_id != receipt.supplier_id:
        raise ValueError('Supplier purchase order harus sama dengan penerimaan')

    # === Validasi satuan ===
    if any(line.satuan_id != line.barang.satuan_id for line in receipt.details):
        raise ValueError('Satuan penerimaan harus sama dengan satuan master barang; konversi satuan belum didukung')

    # === Validasi qty > 0 (Phase C) ===
    for line in receipt.details:
        if line.qty <= 0:
            raise ValueError(f'Qty penerimaan harus > 0 (baris {line.barang.kode if line.barang else "unknown"}: {line.qty})')

    # === Validasi gudang required (Phase C) ===
    if not receipt.gudang_id:
        raise ValueError('Gudang wajib diisi untuk Goods Receipt (transaksi stok baru harus warehouse-explicit)')


# ============================================================
# P0-01 — Partial three-way match (Roadmap §20)
# ============================================================
def _eligible_receipts_for_invoice(db, inv):
    """Return receipts yang eligible untuk match dengan invoice ``inv``.

    Eligible criteria:
        - Status != DIBATALKAN
        - Supplier == invoice.supplier
        - Tanggal receipt <= tanggal invoice
        - Punya jurnal GRNI POSTED yang belum direversal
        - Dimensi organisasi sama dengan invoice
    """
    rows = (
        db.query(PenerimaanBarang)
        .filter(
            PenerimaanBarang.supplier_id == inv.supplier_id,
        )
        .all()
    )
    eligible = []
    for row in rows:
        if getattr(row.status, 'value', row.status) == 'DIBATALKAN':
            continue
        if local_datetime(row.tanggal).date() > local_datetime(inv.tanggal).date():
            continue
        journal = db.get(JurnalUmum, row.jurnal_umum_id) if row.jurnal_umum_id else None
        if (getattr(row.status, 'value', row.status) != 'SELESAI' or not journal
                or journal.status != StatusJurnal.POSTED
                or journal.tipe_transaksi != 'PENERIMAAN_GRNI'
                or journal.ref_id != row.id
                or db.query(JurnalUmum).filter_by(reversal_of_id=journal.id).first()):
            continue
        scope = organization_service.for_source(db, inv.id)
        if any(getattr(journal, key) != value for key, value in scope.items()):
            continue
        # Validasi nilai jurnal penerimaan cocok dengan detail (invariant lama, tetap dipertahankan)
        value = Decimal(0)
        for line in row.details:
            value += (Decimal(line.harga_perolehan or 0) * line.qty).quantize(CENT)
        credits = sum((line.kredit for line in journal.details), Decimal(0))
        if credits != value:
            continue
        # Pastikan GRNI account != supplier account
        for line in journal.details:
            if line.kredit and line.akun_perkiraan_id == inv.supplier.akun_hutang_id:
                raise ValueError('Akun GRNI tidak boleh sama dengan utang supplier')
        eligible.append((row, journal))
    return eligible


def _already_matched_receipt_qty(db, receipt_detail_id, exclude_invoice_id=None):
    """Return total qty dari receipt detail yang sudah di-match ke invoice LAIN.

    Parameter ``exclude_invoice_id`` dipakai untuk exclude match rows milik invoice
    yang sedang di-posting (idempotensi saat retry posting).
    """
    rows = (
        db.query(PurchaseInvoiceReceiptMatch)
        .filter(PurchaseInvoiceReceiptMatch.penerimaan_barang_detail_id == receipt_detail_id)
        .all()
    )
    total = 0
    for m in rows:
        inv_detail = m.purchase_invoice_detail
        if inv_detail is None:
            total += m.matched_qty
            continue
        if exclude_invoice_id is not None and inv_detail.purchase_invoice_id == exclude_invoice_id:
            continue
        total += m.matched_qty
    return total


def _already_matched_invoice_qty(db, invoice_detail_id):
    """Return total qty dari invoice detail yang sudah di-match (untuk invoice LAIN/current)."""
    rows = (
        db.query(PurchaseInvoiceReceiptMatch)
        .filter(PurchaseInvoiceReceiptMatch.purchase_invoice_detail_id == invoice_detail_id)
        .all()
    )
    return sum((m.matched_qty for m in rows), 0)


def _match_invoice_to_receipts(db, inv):
    """Auto-match setiap invoice line ke receipt lines yang eligible (same barang).

    Strategy:
        - Untuk setiap invoice line, cari receipt detail (barang sama) yang masih ada
          remaining qty (receipt_qty - already_matched_to_other_invoice).
        - Match greedily sampai invoice line fully matched atau receipts habis.
        - Kalau invoice line tidak dapat match full → sisa tetap diposting
          sebagai direct purchase (akan ditangani caller sebagai 'unmatched portion').
        - matched_unit_cost = invoice line harga (bukan receipt harga), karena
          PPN input VAT dihitung dari invoice price.
        - matched_value = matched_qty * matched_unit_cost (dari invoice price)

    Idempotency:
        - Existing match rows milik invoice ini dihitung sebagai 'already matched',
          jadi retry posting tidak akan create duplicate matches.
        - Clearing dihitung dari ALL match rows (existing + new) for this invoice,
          bukan hanya new matches, agar retry tetap menghasilkan clearing yang sama.

    Return:
        {
            "matches": [ ... new matches created in this call ... ],
            "all_matches": [ ... all matches for this invoice (existing + new) ... ],
            "unmatched_invoice_detail_ids": [ ... ],
            "grni_clearing_by_account": { akun_id: Decimal(total_matched_value) },
        }
    """
    eligible = _eligible_receipts_for_invoice(db, inv)
    if not eligible:
        return {
            "matches": [],
            "all_matches": [],
            "unmatched_invoice_detail_ids": [d.id for d in inv.details],
            "grni_clearing_by_account": {},
        }

    # Pre-load existing matches for this invoice (idempotency on retry)
    existing_matches_by_inv_detail = defaultdict(list)
    existing_match_rows = (
        db.query(PurchaseInvoiceReceiptMatch)
        .filter(
            PurchaseInvoiceReceiptMatch.purchase_invoice_detail_id.in_(
                [d.id for d in inv.details]
            )
        )
        .all()
    )
    for m in existing_match_rows:
        existing_matches_by_inv_detail[m.purchase_invoice_detail_id].append(m)

    # Build receipt detail pool: {barang_id: [(receipt_detail, journal, remaining_avail)]}
    receipt_pool = defaultdict(list)
    for receipt, journal in eligible:
        for rd in receipt.details:
            # Exclude matches milik invoice ini (idempotency: retry tidak double-count)
            already = _already_matched_receipt_qty(db, rd.id, exclude_invoice_id=inv.id)
            remaining = rd.qty - already
            if remaining > 0:
                receipt_pool[rd.barang_id].append({
                    "receipt_detail": rd,
                    "journal": journal,
                    "remaining": remaining,
                })

    new_matches = []
    unmatched_invoice_detail_ids = []

    for inv_line in inv.details:
        if inv_line.qty <= 0:
            continue
        # Already matched qty (existing rows for THIS invoice line)
        already_inv_matched = sum(
            (m.matched_qty for m in existing_matches_by_inv_detail.get(inv_line.id, [])),
            0,
        )
        to_match = inv_line.qty - already_inv_matched
        if to_match <= 0:
            continue

        pool = receipt_pool.get(inv_line.barang_id, [])
        if not pool:
            unmatched_invoice_detail_ids.append(inv_line.id)
            continue

        invoice_unit_cost = Decimal(inv_line.harga or 0)
        # Match greedy
        for entry in pool:
            if to_match <= 0:
                break
            take = min(to_match, entry["remaining"])
            if take <= 0:
                continue

            rd = entry["receipt_detail"]
            receipt_unit_cost = Decimal(rd.harga_perolehan or 0)
            matched_value = (invoice_unit_cost * take).quantize(CENT)

            new_matches.append({
                "invoice_detail_id": inv_line.id,
                "receipt_detail_id": rd.id,
                "receipt_journal_id": entry["journal"].id,
                "matched_qty": take,
                "matched_unit_cost": invoice_unit_cost,
                "matched_value": matched_value,
                "receipt_unit_cost": receipt_unit_cost,
            })

            # Update pool
            entry["remaining"] -= take
            to_match -= take

        if to_match > 0:
            unmatched_invoice_detail_ids.append(inv_line.id)

    return {
        "matches": new_matches,
        "all_matches": list(existing_match_rows) + [
            # Convert new_matches dict → pseudo-row for clearing calc
            _NewMatchProxy(m) for m in new_matches
        ],
        "unmatched_invoice_detail_ids": unmatched_invoice_detail_ids,
        "grni_clearing_by_account": _build_clearing_from_matches(db, inv, eligible,
                                                                  list(existing_match_rows),
                                                                  new_matches),
    }


class _NewMatchProxy:
    """Lightweight proxy untuk unify existing ORM rows & new match dicts saat clearing calc."""
    def __init__(self, m):
        self.matched_qty = m["matched_qty"]
        self.matched_unit_cost = m["matched_unit_cost"]
        self.matched_value = m["matched_value"]
        self.purchase_invoice_detail_id = m["invoice_detail_id"]
        self.penerimaan_barang_detail_id = m["receipt_detail_id"]
        # Cache untuk receipt_detail lookup
        self._receipt_detail = None
        self._receipt_journal_id = m.get("receipt_journal_id")


def _build_clearing_from_matches(db, inv, eligible, existing_rows, new_matches):
    """Build {akun_id: Decimal(total_matched_value)} dari all matches (existing + new).

    Allocation strategy:
        - Index credit lines dari setiap eligible receipt journal: {journal_id: {akun_id: kredit_total}}
        - Untuk setiap match row, lookup receipt_detail → dapatkan journal_id receipt-nya
        - Allocate matched_value ke akun GRNI yang ada di journal receipt tsb,
          proporsional terhadap kredit per akun.
    """
    # Build journal credit index
    journal_credit_index = defaultdict(lambda: defaultdict(Decimal))
    receipt_total_value = {}
    journal_by_id = {}
    for receipt, journal in eligible:
        journal_by_id[journal.id] = journal
        for line in journal.details:
            if line.kredit:
                journal_credit_index[journal.id][line.akun_perkiraan_id] += Decimal(line.kredit)
        total = sum(
            (Decimal(rd.harga_perolehan or 0) * rd.qty).quantize(CENT)
            for rd in receipt.details
        )
        receipt_total_value[journal.id] = total

    # Build receipt_detail → journal_id index (untuk lookup existing match rows)
    rd_to_journal = {}
    for receipt, journal in eligible:
        for rd in receipt.details:
            rd_to_journal[rd.id] = journal.id

    # Aggregate matched_value per journal_id
    matched_value_per_journal = defaultdict(Decimal)

    def _process_match(match_row):
        rd_id = match_row.penerimaan_barang_detail_id
        journal_id = rd_to_journal.get(rd_id)
        if journal_id is None:
            # Receipt detail mungkin dari receipt yang sudah di-reverse;
            # skip (tidak eligible lagi).
            return
        matched_value_per_journal[journal_id] += Decimal(match_row.matched_value)

    for m in existing_rows:
        _process_match(m)
    for m in new_matches:
        # new_matches is list of dicts; build proxy
        _process_match(_NewMatchProxy(m))

    # Allocate matched_value per journal → akun GRNI proporsional
    grni_clearing = defaultdict(Decimal)
    for journal_id, matched_total in matched_value_per_journal.items():
        receipt_total = receipt_total_value.get(journal_id, Decimal(0))
        if receipt_total <= 0:
            continue
        credit_lines = journal_credit_index.get(journal_id, {})
        for akun_id, kredit_amount in credit_lines.items():
            if kredit_amount <= 0:
                continue
            allocated = (kredit_amount * matched_total / receipt_total).quantize(CENT)
            grni_clearing[akun_id] += allocated

    return dict(grni_clearing)


def _persist_match_rows(db, inv, match_result):
    """Persist match rows ke ``purchase_invoice_receipt_match`` table.

    Idempotent: kalau sudah ada match row untuk kombinasi yang sama, skip.
    Tidak overwrite existing match (misalnya kalau posting di-retry).
    """
    existing_keys = set()
    existing_rows = (
        db.query(PurchaseInvoiceReceiptMatch)
        .join(
            PurchaseInvoiceReceiptMatch.purchase_invoice_detail,
            isouter=True,
        )
        .filter(
            PurchaseInvoiceReceiptMatch.purchase_invoice_detail_id.in_(
                [d.id for d in inv.details]
            )
        )
        .all()
    )
    for row in existing_rows:
        existing_keys.add((row.purchase_invoice_detail_id, row.penerimaan_barang_detail_id))

    for m in match_result["matches"]:
        key = (m["invoice_detail_id"], m["receipt_detail_id"])
        if key in existing_keys:
            continue
        new_row = PurchaseInvoiceReceiptMatch(
            purchase_invoice_detail_id=m["invoice_detail_id"],
            penerimaan_barang_detail_id=m["receipt_detail_id"],
            matched_qty=m["matched_qty"],
            matched_unit_cost=m["matched_unit_cost"],
            matched_value=m["matched_value"],
        )
        db.add(new_row)
        existing_keys.add(key)
    db.flush()


def clearing_entries(db, inv, basis):
    """Return GRNI clearing account totals using partial three-way match.

    P0-01 (Re-Audit): Wire ``purchase_invoice_receipt_match`` bridge ke service.

    Behavior baru:
        - Auto-match invoice lines ke eligible receipt lines (same barang).
        - Support partial receipt, partial invoice, many-to-many matching.
        - Persist match rows ke ``purchase_invoice_receipt_match`` table.
        - Hitung GRNI clearing per-account dari matched_value (proporsional
          terhadap nilai kredit journal GRNI receipt).
        - Sisa invoice yang tidak dapat match → tetap diposting sebagai direct
          purchase portion di akun PEMBELIAN (di-handle caller post_purchase_invoice).
        - Toleransi rounding 1.00 untuk validasi total debits vs basis.

    Return:
        Dict {akun_perkiraan_id: Decimal(debit_amount)} untuk GRNI clearing.
        None kalau GRNI tidak configured dan tidak ada receipts sama sekali
        (legacy fallback path di caller).

    Raises:
        ValueError:
            - Kalau GRNI configured tapi tidak ada eligible receipts
            - Kalau ada receipt GRNI yang sudah direversal
            - Kalau supplier akun GRNI == supplier akun utang
            - Kalau diskon global diisi (clearing belum support diskon global)
    """
    # Diskon global masih belum didukung (sama seperti versi lama)
    if inv.total_diskon:
        raise ValueError(
            'Clearing GRNI belum mendukung diskon global; '
            'gunakan harga penerimaan dan diskon per baris yang sesuai'
        )

    grni_account_id = configured_grni(db)

    rows = [r for r in db.query(PenerimaanBarang).filter_by(purchase_invoice_id=inv.id).all()
            if getattr(r.status, 'value', r.status) != 'DIBATALKAN']
    if not rows and not _has_other_eligible_receipts(db, inv):
        # Tidak ada receipt terkait sama sekali
        if grni_account_id:
            raise ValueError(
                'GRNI aktif: hubungkan dan selesaikan penerimaan sebelum posting invoice '
                '(atau hubungkan invoice ke penerimaan via purchase_invoice_id)'
            )
        return None

    # Jalankan partial three-way match
    match_result = _match_invoice_to_receipts(db, inv)

    # Persist match rows untuk audit trail
    _persist_match_rows(db, inv, match_result)

    grni_clearing = match_result["grni_clearing_by_account"]

    # Kalau GRNI configured, wajib ada minimal satu match (agar clearing bisa terjadi).
    # Kalau ada unmatched invoice lines dan GRNI configured, tetap diizinkan
    # asalkan ada match lain — sisa unmatched akan masuk akun PEMBELIAN (caller handle).
    if grni_account_id and not grni_clearing:
        raise ValueError(
            'GRNI aktif tetapi tidak ada penerimaan GRNI yang eligible untuk invoice ini. '
            'Pastikan penerimaan sudah SELESAI dan jurnal GRNI sudah POSTED serta belum direversal.'
        )

    # Validasi total clearing vs basis (dengan toleransi rounding)
    total_clearing = sum(grni_clearing.values(), Decimal(0))
    basis_dec = Decimal(basis) if not isinstance(basis, Decimal) else basis

    # Kalau total clearing > basis + toleransi → over-clearing (error)
    # Kalau total clearing < basis - toleransi → under-clearing;
    #   sisa akan di-handle caller sebagai direct purchase portion.
    #   Tidak error asalkan unmatched_invoice_detail_ids non-empty.
    if total_clearing > basis_dec + VALUE_TOLERANCE:
        raise ValueError(
            f'Total GRNI clearing ({total_clearing}) melebihi basis invoice ({basis_dec}). '
            f'Kemungkinan ada penerimaan yang nilainya lebih besar dari invoice.'
        )

    if total_clearing < basis_dec - VALUE_TOLERANCE:
        # Under-clearing — diizinkan hanya kalau ada unmatched invoice lines
        if not match_result["unmatched_invoice_detail_ids"]:
            # Semua invoice line seharusnya match tapi total tidak cukup → error
            raise ValueError(
                f'Total GRNI clearing ({total_clearing}) kurang dari basis invoice ({basis_dec}) '
                f'tetapi tidak ada unmatched invoice lines. Periksa konsistensi data penerimaan.'
            )
        # Sisa akan jadi direct purchase portion — caller handle

    return grni_clearing if grni_clearing else None


def _has_other_eligible_receipts(db, inv):
    """Check apakah ada receipt eligible untuk invoice (selain yang sudah di-link via purchase_invoice_id)."""
    try:
        return len(_eligible_receipts_for_invoice(db, inv)) > 0
    except ValueError:
        return False


def require_no_completed_receipts(db, invoice):
    if any(getattr(r.status, 'value', r.status) == 'SELESAI' for r in
           db.query(PenerimaanBarang).filter_by(purchase_invoice_id=invoice.id).all()):
        raise ValueError('Invoice memiliki penerimaan selesai; gunakan alur retur, bukan pembatalan invoice')
