"""Read-only current stock reconciliation and executed stock document audit."""
from datetime import datetime
from fastapi import HTTPException
from app.services.reporting_ledger import JAKARTA
from app.models.transaksi.stok_mutasi import StokMutasi
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun, SaldoNormal
from app.models.master.barang import Barang
from app.models.transaksi.stock_balance import StockBalance
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal, RefModule
from app.models.detail.jurnal_detail import JurnalDetail
from app.services.reporting_ledger import local_datetime, day_end, ZERO


# ==========================================
# Helpers
# ==========================================

def _nilai_stok_per_barang(db: Session, barang_id: UUID) -> Decimal:
    """Total nilai stok untuk satu barang (semua gudang)."""
    from app.services.stok_service import hitung_nilai_stok
    return hitung_nilai_stok(db, barang_id).quantize(Decimal('0.01'))


def _qty(db, barang):
    qty = db.query(func.sum(StockBalance.qty)).filter_by(barang_id=barang.id).scalar()
    return qty if qty is not None else (barang.stok or 0)


def _require_global_scope(db):
    if any(db.info.get('report_scope', {}).values()):
        raise HTTPException(400, 'Rekonsiliasi/audit persediaan hanya tersedia untuk seluruh organisasi; saldo stok belum memiliki dimensi organisasi')


def _saldo_akun_persediaan_di_buku_besar(db: Session, akun_id: UUID, as_of: Optional[datetime] = None) -> Decimal:
    """Saldo akun Persediaan di buku besar sampai tanggal as_of.

    Saldo = debit - kredit (untuk akun AKTIVA, saldo normal DEBIT).
    Hanya menghitung jurnal yang POSTED.
    """
    q = db.query(
        func.coalesce(func.sum(JurnalDetail.debit), ZERO),
        func.coalesce(func.sum(JurnalDetail.kredit), ZERO),
    ).join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id).filter(
        JurnalDetail.akun_perkiraan_id == akun_id,
        JurnalUmum.status == StatusJurnal.POSTED,
    )
    if as_of is not None:
        q = q.filter(JurnalUmum.tanggal <= local_datetime(as_of))

    debit, kredit = q.one()
    return (Decimal(str(debit or 0)) - Decimal(str(kredit or 0))).quantize(Decimal("0.01"))


def _list_akun_persediaan(db: Session) -> List[AkunPerkiraan]:
    """List semua akun yang pernah dipetakan ke barang (atau kandidat Persediaan).

    Sumber: barang.akun_persediaan_id yang tidak null, plus akun fallback
    kategori/default (PERSEDIAAN_BAHAN_BAKU, PERSEDIAAN_WIP, dll) yang sudah
    dipakai di jurnal.
    """
    # Akun dari mapping barang
    mapped = (
        db.query(AkunPerkiraan)
        .join(Barang, Barang.akun_persediaan_id == AkunPerkiraan.id)
        .filter(Barang.akun_persediaan_id.isnot(None))
        .distinct()
        .all()
    )

    # Akun fallback dari setting_akun (PERSEDIAAN_*)
    from app.models.master.setting_akun import SettingAkun
    fallback_keys = (
        "PERSEDIAAN_BAHAN_BAKU", "PERSEDIAAN_WIP", "PERSEDIAAN_BARANG_JADI",
        "PERSEDIAAN_BAHAN_PEMBANTU",
    )
    fallback_rows = (
        db.query(AkunPerkiraan)
        .join(SettingAkun, SettingAkun.akun_perkiraan_id == AkunPerkiraan.id)
        .filter(SettingAkun.key.in_(fallback_keys))
        .all()
    )

    historical = db.query(AkunPerkiraan).join(
        StokMutasi, StokMutasi.inventory_account_id == AkunPerkiraan.id).distinct().all()
    # Retain old accounts after master mappings change.
    # Dedup by id, preserve order
    seen = set()
    result = []
    for acc in list(mapped) + list(fallback_rows) + list(historical):
        if acc.id not in seen:
            seen.add(acc.id)
            result.append(acc)
    return result


# ==========================================
# Public API
# ==========================================

def get_rekonsiliasi_persediaan(
    db: Session,
    as_of: Optional[datetime] = None,
    only_mismatch: bool = False,
) -> dict:
    """Compare CURRENT global stock with ALL posted ledger entries, including future-dated postings.

    Historical dates are rejected until dated stock balance snapshots exist.
    """
    _require_global_scope(db)
    now = datetime.now(JAKARTA)
    if as_of is not None and local_datetime(as_of).date() != now.date():
        raise HTTPException(400, 'Rekonsiliasi historis belum tersedia; gunakan tanggal hari ini atau tanpa as_of')
    as_of = now
    akun_list = _list_akun_persediaan(db)
    barang_per_akun = {}
    barang_unmapped = []
    from app.services.persediaan_service import _get_akun_persediaan_id
    for b in db.query(Barang).order_by(Barang.kode).all():
        try:
            account_id = _get_akun_persediaan_id(db, b)
        except ValueError:
            barang_unmapped.append(b)
            continue
        if not any(a.id == account_id for a in akun_list):
            account = db.get(AkunPerkiraan, account_id)
            if account is None:
                barang_unmapped.append(b)
                continue
            akun_list.append(account)
        barang_per_akun.setdefault(account_id, []).append(b)

    # 5. Build items per akun
    items = []
    total_match = 0
    total_mismatch = 0
    total_selisih = ZERO

    for akun in akun_list:
        saldo_buku = _saldo_akun_persediaan_di_buku_besar(db, akun.id)

        # Total nilai stok untuk barang yang dipetakan ke akun ini
        total_nilai_stok = ZERO
        barang_details = []
        for b in barang_per_akun.get(akun.id, []):
            nilai_stok = _nilai_stok_per_barang(db, b.id)
            total_nilai_stok += nilai_stok
            status_mapping = "MAPPED" if b.akun_persediaan_id else "FALLBACK"
            barang_details.append({
                "id": str(b.id),
                "kode": b.kode,
                "nama": b.nama,
                "qty": _qty(db, b),
                "nilai_stok": nilai_stok,
                "akun_persediaan_id": str(akun.id),
                "status_mapping": status_mapping,
            })

        selisih = (saldo_buku - total_nilai_stok).quantize(Decimal("0.01"))
        status = "MATCH" if abs(selisih) < Decimal("0.01") else "MISMATCH"

        if status == "MATCH":
            total_match += 1
        else:
            total_mismatch += 1
            total_selisih += selisih

        # Filter only_mismatch
        if only_mismatch and status == "MATCH":
            continue

        items.append({
            "akun": {
                "id": str(akun.id),
                "kode": akun.kode,
                "nama": akun.nama,
                "status": akun.status,
            },
            "saldo_buku_besar": saldo_buku,
            "total_nilai_stok": total_nilai_stok,
            "selisih": selisih,
            "status": status,
            "barang": barang_details,
        })

    # 6. Build barang_belum_dipetakan
    barang_belum_dipetakan = []
    for b in barang_unmapped:
        nilai_stok = _nilai_stok_per_barang(db, b.id)
        # Hanya tampilkan yang punya nilai stok > 0 (skip barang kosong)
        if nilai_stok == 0 and _qty(db, b) == 0:
            continue
        kategori_nama = b.kategori.nama if b.kategori else None
        barang_belum_dipetakan.append({
            "id": str(b.id),
            "kode": b.kode,
            "nama": b.nama,
            "qty": _qty(db, b),
            "nilai_stok": nilai_stok,
            "kategori": kategori_nama,
        })

    return {
        "as_of": local_datetime(as_of).date().isoformat(),
        "basis": "CURRENT_ALL_POSTED",
        "includes_future_postings": True,
        "ringkasan": {
            "total_akun_diperiksa": total_match + total_mismatch,
            "total_akun_match": total_match,
            "total_akun_mismatch": total_mismatch,
            "total_akun_unmapped": len(barang_belum_dipetakan),
            "total_selisih": total_selisih.quantize(Decimal("0.01")),
        },
        "items": items,
        "barang_belum_dipetakan": barang_belum_dipetakan,
    }


def get_ringkasan_rekonsiliasi(db: Session, as_of: Optional[datetime] = None) -> dict:
    """Ringkasan cepat rekonsiliasi (tanpa detail per barang).

    Dipakai untuk dashboard / alerting.
    """
    full = get_rekonsiliasi_persediaan(db, as_of=as_of, only_mismatch=False)
    return {
        "as_of": full["as_of"],
        "basis": full["basis"],
        "includes_future_postings": full["includes_future_postings"],
        "ringkasan": full["ringkasan"],
    }


# ==========================================
# Audit trail: cek konsistensi transaksi
# ==========================================

def audit_transaksi_persediaan(
    db: Session,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Inspect executed stock movements and their stock journals; draft documents are excluded."""
    from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
    from app.models.transaksi.penjualan.pengiriman_barang import PengirimanBarang
    from app.models.transaksi.pembelian.purchase_retur import PurchaseRetur
    from app.models.transaksi.penjualan.sales_retur import SalesRetur
    from app.models.transaksi.persediaan.penyesuaian_stok import PenyesuaianStok
    from app.models.transaksi.penjualan.sales_order import StatusPenjualan

    _require_global_scope(db)
    from app.services.reporting_ledger import day_start
    date_from = day_start(date_from or datetime(2000, 1, 1))
    date_to = day_end(date_to or datetime.now(JAKARTA))
    if date_from > date_to:
        raise HTTPException(400, 'Tanggal awal tidak boleh melebihi tanggal akhir')
    anomali, summary = [], {}

    def audit_model(model, label, journal_type=None):
        rows = db.query(model).filter(model.tanggal >= date_from, model.tanggal <= date_to).all()
        total = with_jurnal = without_jurnal = 0
        for row in rows:
            state = getattr(row.status, 'value', row.status)
            movements = db.query(StokMutasi).filter_by(ref_id=row.id).all()
            if state not in ('SELESAI', 'DISETUJUI') and not movements:
                continue
            total += 1
            if journal_type:
                journal = db.query(JurnalUmum).filter_by(ref_id=row.id, tipe_transaksi=journal_type,
                                                        status=StatusJurnal.POSTED).first()
            else:
                journal = db.get(JurnalUmum, row.jurnal_umum_id) if row.jurnal_umum_id else None
            valid = bool(journal and journal.status == StatusJurnal.POSTED and
                         not db.query(JurnalUmum).filter_by(reversal_of_id=journal.id).first())
            if valid:
                with_jurnal += 1
            else:
                without_jurnal += 1
                value = sum((abs(m.total_nilai or ZERO) for m in movements), ZERO)
                if value or (state in ('SELESAI', 'DISETUJUI') and not movements):
                    legacy = label == 'PENERIMAAN' and not row.purchase_invoice_id
                    anomali.append({
                        'tipe': label, 'id': str(row.id),
                        'no_dokumen': getattr(row, 'no_form', None) or getattr(row, 'no_surat_jalan', None)
                                       or getattr(row, 'no_retur', None) or getattr(row, 'no_adj', None),
                        'status': state, 'jurnal_umum_id': str(journal.id) if journal else None,
                        'catatan': ('Penerimaan tanpa jurnal; periksa mode legacy dan saldo pembukaan persediaan'
                                    if legacy else 'Transaksi stok tidak memiliki jurnal stok POSTED aktif; periksa mutasi/posting'),
                    })
        summary[f'{label.lower()}_total'] = total
        summary[f'{label.lower()}_dengan_jurnal'] = with_jurnal
        summary[f'{label.lower()}_tanpa_jurnal'] = without_jurnal

    audit_model(PenerimaanBarang, 'PENERIMAAN', 'PENERIMAAN_GRNI')
    audit_model(PengirimanBarang, 'PENGIRIMAN')
    audit_model(PurchaseRetur, 'RETUR_PEMBELIAN', 'RETUR_STOCK')
    audit_model(SalesRetur, 'RETUR_PENJUALAN', 'RETUR_HPP')
    audit_model(PenyesuaianStok, 'PENYESUAIAN')

    from app.services.reporting_ledger import period
    return {
        "periode": period(date_from, date_to),
        "summary": summary,
        "anomali": anomali,
    }
