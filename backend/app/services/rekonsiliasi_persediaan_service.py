"""rekonsiliasi_persediaan_service.py

Tahap 3 — Rekonsiliasi nilai persediaan vs buku besar.

Membandingkan:
- Sisi stok: total nilai persediaan per barang (dari stock_balance + stok_mutasi)
- Sisi buku besar: saldo akun Persediaan (dari jurnal_detail yang sudah POSTED,
  dikelompokkan per akun_persediaan_id barang)

Output:
- Per akun Persediaan: total saldo buku besar vs total nilai stok barang
  yang dipetakan ke akun tersebut.
- Per barang: nilai stoknya vs mapping akunnya.
- Selisih dan indikator status (MATCH / MISMATCH / UNMAPPED).

Catatan:
- Akun Persediaan di sisi buku besar = akun DETAIL di header AKTIVA yang
  pernah dipakai di jurnal_detail. Tidak terbatas pada akun yang dipetakan
  di barang — bisa juga akun dari fallback kategori/default.
- Barang tanpa mapping (akun_persediaan_id NULL) tetap dihitung di sisi stok,
  tapi tidak punya akun pembanding di sisi buku besar -> status UNMAPPED.
- Riwayat jurnal POSTED tetap dipertahankan; rekonsiliasi bersifat read-only.
"""
from datetime import datetime
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
    result = db.query(
        func.coalesce(func.sum(StockBalance.nilai), ZERO)
    ).filter(StockBalance.barang_id == barang_id).scalar()
    return Decimal(str(result or 0)).quantize(Decimal("0.01"))


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

    # Dedup by id, preserve order
    seen = set()
    result = []
    for acc in list(mapped) + list(fallback_rows):
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
    """Rekonsiliasi nilai persediaan vs buku besar per akun Persediaan.

    Parameter:
        db: SQLAlchemy Session
        as_of: Tanggal cutoff (default: now). Hanya jurnal sampai tanggal ini
               yang dihitung di sisi buku besar.
        only_mismatch: Jika True, hanya tampilkan akun/barang yang selisihnya != 0.

    Return:
        {
            "as_of": "2026-09-11",
            "ringkasan": {
                "total_akun_diperiksa": int,
                "total_akun_match": int,
                "total_akun_mismatch": int,
                "total_akun_unmapped": int,
                "total_selisih": Decimal,
            },
            "items": [
                {
                    "akun": {"id", "kode", "nama", "status"},
                    "saldo_buku_besar": Decimal,
                    "total_nilai_stok": Decimal,
                    "selisih": Decimal,  # buku_besar - stok
                    "status": "MATCH" | "MISMATCH",
                    "barang": [
                        {
                            "id", "kode", "nama",
                            "qty": int,
                            "nilai_stok": Decimal,
                            "akun_persediaan_id": UUID,
                            "status_mapping": "MAPPED" | "FALLBACK"
                        }, ...
                    ]
                }, ...
            ],
            "barang_belum_dipetakan": [
                {
                    "id", "kode", "nama",
                    "qty": int,
                    "nilai_stok": Decimal,
                    "kategori": str | None,
                }, ...
            ]
        }
    """
    if as_of is None:
        as_of = datetime.now()

    # 1. Ambil semua akun Persediaan yang relevan
    akun_list = _list_akun_persediaan(db)

    # 2. Ambil semua barang dengan mapping
    barang_mapped = (
        db.query(Barang)
        .filter(Barang.akun_persediaan_id.isnot(None))
        .order_by(Barang.kode)
        .all()
    )

    # Group barang by akun_persediaan_id
    barang_per_akun = {}
    for b in barang_mapped:
        barang_per_akun.setdefault(b.akun_persediaan_id, []).append(b)

    # 3. Ambil barang belum dipetakan
    barang_unmapped = (
        db.query(Barang)
        .filter(Barang.akun_persediaan_id.is_(None))
        .order_by(Barang.kode)
        .all()
    )

    # 4. Ambil fallback akun IDs (untuk flagging status_mapping)
    from app.models.master.setting_akun import SettingAkun
    fallback_keys = (
        "PERSEDIAAN_BAHAN_BAKU", "PERSEDIAAN_WIP", "PERSEDIAAN_BARANG_JADI",
        "PERSEDIAAN_BAHAN_PEMBANTU",
    )
    fallback_akun_ids = set(
        row[0] for row in db.query(SettingAkun.akun_perkiraan_id)
        .filter(SettingAkun.key.in_(fallback_keys)).all()
    )

    # 5. Build items per akun
    items = []
    total_match = 0
    total_mismatch = 0
    total_selisih = ZERO

    for akun in akun_list:
        saldo_buku = _saldo_akun_persediaan_di_buku_besar(db, akun.id, as_of)

        # Total nilai stok untuk barang yang dipetakan ke akun ini
        total_nilai_stok = ZERO
        barang_details = []
        for b in barang_per_akun.get(akun.id, []):
            nilai_stok = _nilai_stok_per_barang(db, b.id)
            total_nilai_stok += nilai_stok
            status_mapping = "FALLBACK" if akun.id in fallback_akun_ids else "MAPPED"
            barang_details.append({
                "id": str(b.id),
                "kode": b.kode,
                "nama": b.nama,
                "qty": b.stok or 0,
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
        if nilai_stok <= 0:
            continue
        kategori_nama = b.kategori.nama if b.kategori else None
        barang_belum_dipetakan.append({
            "id": str(b.id),
            "kode": b.kode,
            "nama": b.nama,
            "qty": b.stok or 0,
            "nilai_stok": nilai_stok,
            "kategori": kategori_nama,
        })

    return {
        "as_of": local_datetime(as_of).date().isoformat(),
        "ringkasan": {
            "total_akun_diperiksa": len(items),
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
    """Audit trail transaksi persediaan: penerimaan, pengiriman, retur, penyesuaian.

    Verifikasi bahwa setiap transaksi yang mengubah stok juga menghasilkan
    jurnal Persediaan yang sesuai (atau konsisten dengan legacy mode).

    Return:
        {
            "periode": {"dari": ..., "sampai": ...},
            "summary": {
                "penerimaan_total": int,
                "penerimaan_dengan_jurnal": int,
                "penerimaan_tanpa_jurnal": int,
                "pengiriman_total": int,
                "pengiriman_dengan_jurnal": int,
                "pengiriman_tanpa_jurnal": int,
                "retur_pembelian_total": int,
                "retur_pembelian_dengan_jurnal": int,
                "retur_pembelian_tanpa_jurnal": int,
                "retur_penjualan_total": int,
                "retur_penjualan_dengan_jurnal": int,
                "retur_penjualan_tanpa_jurnal": int,
                "penyesuaian_total": int,
                "penyesuaian_dengan_jurnal": int,
                "penyesuaian_tanpa_jurnal": int,
            },
            "anomali": [
                {"tipe": "PENERIMAAN" | "PENGIRIMAN" | ...,
                 "id": UUID, "no_dokumen": str, "status": str,
                 "jurnal_umum_id": UUID | None,
                 "catatan": str}, ...
            ]
        }
    """
    from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
    from app.models.transaksi.penjualan.pengiriman_barang import PengirimanBarang
    from app.models.transaksi.pembelian.purchase_retur import PurchaseRetur
    from app.models.transaksi.penjualan.sales_retur import SalesRetur
    from app.models.transaksi.persediaan.penyesuaian_stok import PenyesuaianStok
    from app.models.transaksi.penjualan.sales_order import StatusPenjualan

    if date_from is None:
        date_from = datetime(2000, 1, 1)
    if date_to is None:
        date_to = datetime.now()

    anomali = []
    summary = {}

    # Helper untuk audit satu model
    def audit_model(model, label, ref_module_filter=None):
        q = db.query(model).filter(model.tanggal >= date_from, model.tanggal <= date_to)
        rows = q.all()
        total = len(rows)
        with_jurnal = 0
        without_jurnal = 0
        for row in rows:
            jurnal_id = getattr(row, "jurnal_umum_id", None)
            # Penyesuaian hanya punya jurnal kalau auto_post_jurnal True dan total > 0
            auto_post = getattr(row, "auto_post_jurnal", True)
            total_value = getattr(row, "total", None) or getattr(row, "grand_total", None) or 0

            if jurnal_id is not None:
                with_jurnal += 1
            elif auto_post and total_value and Decimal(str(total_value)) > 0:
                # Seharusnya ada jurnal tapi tidak ada -> anomali
                without_jurnal += 1
                anomali.append({
                    "tipe": label,
                    "id": str(row.id),
                    "no_dokumen": getattr(row, "no_form", None) or getattr(row, "no_surat_jalan", None)
                                  or getattr(row, "no_retur", None) or getattr(row, "no_adj", None),
                    "status": getattr(row.status, "value", str(row.status)) if row.status else None,
                    "jurnal_umum_id": None,
                    "catatan": f"{label} dengan nilai > 0 tetapi tidak ada jurnal (cek auto_post_jurnal atau error posting)",
                })
            else:
                # OK: tidak ada jurnal karena memang tidak perlu (auto_post False atau nilai 0)
                without_jurnal += 1

        summary[f"{label.lower()}_total"] = total
        summary[f"{label.lower()}_dengan_jurnal"] = with_jurnal
        summary[f"{label.lower()}_tanpa_jurnal"] = without_jurnal

    audit_model(PenerimaanBarang, "PENERIMAAN")
    audit_model(PengirimanBarang, "PENGIRIMAN")
    audit_model(PurchaseRetur, "RETUR_PEMBELIAN")
    audit_model(SalesRetur, "RETUR_PENJUALAN")
    audit_model(PenyesuaianStok, "PENYESUAIAN")

    from app.services.reporting_ledger import period
    return {
        "periode": period(date_from, date_to),
        "summary": summary,
        "anomali": anomali,
    }
