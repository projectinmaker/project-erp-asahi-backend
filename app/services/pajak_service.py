"""AUD-07 (update #04) — Rekap pajak: PPN Masukan/Keluaran & PPh 23.

Temuan audit 7-b #1: TIDAK ADA endpoint rekap pajak sama sekali. Service ini
menyediakan rekap per periode dari SATU sumber yang sama dengan laporan
keuangan (jurnal POSTED + payment_allocation + dokumen pajak):

- ``get_rekap_ppn``    : PPN Keluaran (jurnal SI + retur penjualan pada akun
  setting PPN_KELUARAN) vs PPN Masukan (jurnal PI + retur pembelian pada akun
  setting PPN_MASUKAN), per dokumen + ringkasan per masa pajak (YYYY-MM).
- ``get_rekap_pph23``  : PPh 23 dipotong pihak lain (kita dipotong pelanggan —
  dari payment_allocation sisi piutang, dengan bukti potong & masa pajak
  AUD-08), PPh 23 dipotong dari supplier (payment_allocation sisi hutang),
  plus accrual level-invoice: SI ber-PPh23 (Dr PPh23 Dibayar di Muka) dan PI
  ber-PPh23 (Cr Hutang PPh23 sejak invoice — AUD-09).

Semua query PPN memakai reporting_ledger (apply_scope + boundary Jakarta +
status POSTED) sehingga konsisten dengan neraca/buku besar.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from loguru import logger

from app.models.detail.jurnal_detail import JurnalDetail
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal, RefModule
from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
from app.models.transaksi.penjualan.sales_retur import SalesRetur
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.models.transaksi.pembelian.purchase_retur import PurchaseRetur
from app.models.transaksi.kas_bank.penerimaan import PenerimaanKas
from app.models.transaksi.kas_bank.pembayaran import PembayaranKas
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.transaksi.payment_allocation import PaymentAllocation
from app.services import reporting_ledger as gl
from app.services import setting_akun_service as sa_cfg
from app.services.organization_service import apply_scope


def _local_day(tanggal) -> date:
    return gl.local_datetime(tanggal).date()

ZERO = Decimal("0")


def _masa(tanggal) -> str:
    return f"{tanggal.year:04d}-{tanggal.month:02d}"


def _akun_pajak_ids(db: Session, key: str) -> set:
    """Kumpulkan id akun PPN dari setting (satu key → satu akun)."""
    akun_id = sa_cfg.get_setting_akun_id_optional(db, key)
    return {akun_id} if akun_id else set()


def _doc_info(db: Session, jurnal: JurnalUmum):
    """Enrichment sumber dokumen per jurnal PPN → (no_dokumen, jenis, pihak, dpp)."""
    ref_id, mod = jurnal.ref_id, jurnal.ref_module
    if mod == RefModule.SALES_INVOICE:
        inv = db.get(SalesInvoice, ref_id)
        if inv:
            pel = db.get(Pelanggan, inv.pelanggan_id)
            return inv.no_invoice, "FAKTUR_KELUARAN", pel.nama if pel else None, (inv.sub_total or ZERO) - (inv.total_diskon or ZERO)
    elif mod == RefModule.SALES_RETUR:
        r = db.get(SalesRetur, ref_id)
        if r:
            pel = db.get(Pelanggan, r.pelanggan_id)
            return r.no_retur, "RETUR_PENJUALAN", pel.nama if pel else None, (getattr(r, 'sub_total', ZERO) or ZERO)
    elif mod == RefModule.PURCHASE_INVOICE:
        inv = db.get(PurchaseInvoice, ref_id)
        if inv:
            sup = db.get(Supplier, inv.supplier_id)
            return inv.no_form, "FAKTUR_MASUKAN", sup.nama if sup else None, (inv.sub_total or ZERO) - (inv.total_diskon or ZERO)
    elif mod == RefModule.PURCHASE_RETUR:
        r = db.get(PurchaseRetur, ref_id)
        if r:
            sup = db.get(Supplier, r.supplier_id)
            return r.no_retur, "RETUR_PEMBELIAN", sup.nama if sup else None, (getattr(r, 'sub_total', ZERO) or ZERO)
    return jurnal.ref_no, (jurnal.tipe_transaksi or str(mod.value) if mod else "LAINNYA"), None, None


def get_rekap_ppn(db: Session, dari: date, sampai: date) -> dict:
    """Rekap PPN per periode — keluaran vs masukan dari GL + per masa pajak."""
    keluaran_ids = _akun_pajak_ids(db, sa_cfg.KEY_PPN_KELUARAN)
    masukan_ids = _akun_pajak_ids(db, sa_cfg.KEY_PPN_MASUKAN)
    akun_ids = keluaran_ids | masukan_ids

    rows = []
    if akun_ids:
        query = (
            db.query(JurnalDetail, JurnalUmum)
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                JurnalDetail.akun_perkiraan_id.in_(akun_ids),
                JurnalUmum.status == StatusJurnal.POSTED,
            )
        )
        query = apply_scope(db, query, JurnalUmum)
        # Boundary Jakarta inklusif akhir hari (pola reporting_ledger.day_end).
        start, end = gl.day_start(dari), gl.day_end(sampai)
        query = query.filter(JurnalUmum.tanggal >= start, JurnalUmum.tanggal <= end)
        for detail, jurnal in query.order_by(JurnalUmum.tanggal, JurnalUmum.no_jurnal).all():
            no_dokumen, jenis, pihak, dpp = _doc_info(db, jurnal)
            is_keluaran = detail.akun_perkiraan_id in keluaran_ids
            # keluaran: kredit menambah (SI), debit mengurangi (retur penjualan)
            # masukan: debit menambah (PI), kredit mengurangi (retur pembelian)
            if is_keluaran:
                ppn = (detail.kredit or ZERO) - (detail.debit or ZERO)
            else:
                ppn = (detail.debit or ZERO) - (detail.kredit or ZERO)
            rows.append({
                "tanggal": jurnal.tanggal,
                "no_jurnal": jurnal.no_jurnal,
                "no_dokumen": no_dokumen,
                "jenis": jenis,
                "pihak": pihak,
                "dpp": dpp,
                "ppn": ppn,
                "masukan": not is_keluaran,
            })

    keluaran_rows = [r for r in rows if not r["masukan"]]
    masukan_rows = [r for r in rows if r["masukan"]]
    total_keluaran = sum((r["ppn"] for r in keluaran_rows), ZERO)
    total_masukan = sum((r["ppn"] for r in masukan_rows), ZERO)

    # Ringkasan per masa pajak (YYYY-MM)
    per_masa_map: dict[str, dict] = {}
    for r in rows:
        masa = _masa(_local_day(r["tanggal"]))
        bucket = per_masa_map.setdefault(masa, {"masa_pajak": masa, "keluaran": ZERO, "masukan": ZERO, "netto": ZERO})
        if r["masukan"]:
            bucket["masukan"] += r["ppn"]
        else:
            bucket["keluaran"] += r["ppn"]
    for bucket in per_masa_map.values():
        bucket["netto"] = bucket["masukan"] - bucket["keluaran"]
    per_masa = [per_masa_map[k] for k in sorted(per_masa_map)]

    return {
        "periode": {"dari": dari.isoformat(), "sampai": sampai.isoformat()},
        "keluaran": [{k: v for k, v in r.items() if k != "masukan"} for r in keluaran_rows],
        "total_keluaran": total_keluaran,
        "masukan": [{k: v for k, v in r.items() if k != "masukan"} for r in masukan_rows],
        "total_masukan": total_masukan,
        "ppn_netto": total_masukan - total_keluaran,
        "per_masa": per_masa,
        # akun pajak yang dipakai rekap ini (transparansi setting)
        "akun": {
            "ppn_keluaran": [str(x) for x in keluaran_ids],
            "ppn_masukan": [str(x) for x in masukan_ids],
        },
    }


def _allocation_rows(db: Session, dari: date, sampai: date, side: str):
    """Baris payment_allocation ber-pph23 pada satu sisi (piutang/hutang).

    sumber: PELUNASAN (dokumen kas langsung) / IDENTIFIKASI (sesi identifikasi
    dokumen Belum Teridentifikasi — tanpa jurnal bank ganda).
    """
    start, end = gl.day_start(dari), gl.day_end(sampai)
    query = db.query(PaymentAllocation).filter(PaymentAllocation.pph23 > 0)
    if side == "piutang":
        query = query.filter(PaymentAllocation.penerimaan_id.isnot(None))
        query = query.options(joinedload(PaymentAllocation.penerimaan))
    else:
        query = query.filter(PaymentAllocation.pembayaran_id.isnot(None))
        query = query.options(joinedload(PaymentAllocation.pembayaran))
    out = []
    for alloc in query.all():
        if side == "piutang":
            dok = alloc.penerimaan
            inv = db.get(SalesInvoice, alloc.sales_invoice_id) if alloc.sales_invoice_id else None
            pihak = db.get(Pelanggan, inv.pelanggan_id).nama if inv and inv.pelanggan_id else None
            no_inv = inv.no_invoice if inv else None
        else:
            dok = alloc.pembayaran
            inv = db.get(PurchaseInvoice, alloc.purchase_invoice_id) if alloc.purchase_invoice_id else None
            pihak = db.get(Supplier, inv.supplier_id).nama if inv and inv.supplier_id else None
            no_inv = inv.no_form if inv else None
        if dok is None:
            continue
        if not (dari <= _local_day(dok.tanggal) <= sampai):
            continue
        out.append({
            "tanggal": dok.tanggal,
            "no_dokumen": dok.no_bukti,
            "no_invoice": no_inv,
            "pihak": pihak,
            "nilai": alloc.nilai,
            "pph23": alloc.pph23,
            "no_bukti_potong": alloc.no_bukti_potong,   # AUD-08
            "masa_pajak": alloc.masa_pajak,             # AUD-08
            "sumber": "IDENTIFIKASI" if alloc.kas_identifikasi_id else "PELUNASAN",
        })
    out.sort(key=lambda r: r["tanggal"])
    return out


def get_rekap_pph23(db: Session, dari: date, sampai: date) -> dict:
    """Rekap PPh 23 per periode — potongan keluar/masuk + accrual per invoice.

    AUD-12 (update #05): accrual invoice DI-NETTING retur atas invoice tsb
    (retur membalik Dr 116003 / Cr 213002). AUD-01 (update #05): invoice
    DIBATALKAN (jurnal dibalik) dikecualikan dari accrual.
    """
    dipotong_pihak_lain = _allocation_rows(db, dari, sampai, "piutang")
    dipotong_dari_supplier = _allocation_rows(db, dari, sampai, "hutang")

    # Accrual SI (PPh 23 dibayar di muka terbentuk saat invoice ber-PPh23)
    start, end = gl.day_start(dari), gl.day_end(sampai)
    from app.models.transaksi.penjualan.sales_order import StatusPenjualan
    si_rows = (
        db.query(SalesInvoice)
        .filter(SalesInvoice.total_pph23 > 0,
                SalesInvoice.status != StatusPenjualan.DIBATALKAN,  # AUD-01
                SalesInvoice.tanggal >= start, SalesInvoice.tanggal <= end)
        .order_by(SalesInvoice.tanggal)
        .all()
    )
    pph23_invoice_penjualan = [{
        "tanggal": inv.tanggal,
        "no_dokumen": inv.no_invoice,
        "pihak": (db.get(Pelanggan, inv.pelanggan_id).nama if inv.pelanggan_id else None),
        "nilai": (inv.grand_total or ZERO) + (inv.total_pph23 or ZERO),
        "pph23": inv.total_pph23,
        "masa_pajak": _masa(_local_day(inv.tanggal)),
    } for inv in si_rows]

    # AUD-12 — retur penjualan membalik PPh23 dibayar di muka (Cr 116003):
    # tampilkan sebagai baris pengurang accrual periode retur terjadi.
    sr_rows = (
        db.query(SalesRetur)
        .filter(SalesRetur.total_pph23 > 0,
                SalesRetur.status != StatusPenjualan.DIBATALKAN,
                SalesRetur.tanggal >= start, SalesRetur.tanggal <= end)
        .order_by(SalesRetur.tanggal)
        .all()
    )
    pph23_retur_penjualan = [{
        "tanggal": r.tanggal,
        "no_dokumen": r.no_retur,
        "no_invoice": (r.sales_invoice.no_invoice if r.sales_invoice else None),
        "pihak": (db.get(Pelanggan, r.pelanggan_id).nama if r.pelanggan_id else None),
        "pph23": -(r.total_pph23 or ZERO),
        "masa_pajak": _masa(_local_day(r.tanggal)),
    } for r in sr_rows]

    # Accrual PI (hutang PPh 23 terbentuk sejak invoice — AUD-09).
    # AUD-08: bukti potong & masa pajak dibaca dari alokasi pelunasannya
    # (potongan dicatat di invoice, bukti potong menyusul saat dibayar).
    pi_rows = (
        db.query(PurchaseInvoice)
        .filter(PurchaseInvoice.total_pph23 > 0,
                PurchaseInvoice.status != StatusPenjualan.DIBATALKAN,  # AUD-01
                PurchaseInvoice.tanggal >= start, PurchaseInvoice.tanggal <= end)
        .order_by(PurchaseInvoice.tanggal)
        .all()
    )
    pph23_invoice_pembelian = []
    for inv in pi_rows:
        alloc = (db.query(PaymentAllocation)
                 .filter(PaymentAllocation.purchase_invoice_id == inv.id)
                 .order_by(PaymentAllocation.created_at.desc())
                 .first())
        pph23_invoice_pembelian.append({
            "tanggal": inv.tanggal,
            "no_dokumen": inv.no_form,
            "pihak": (db.get(Supplier, inv.supplier_id).nama if inv.supplier_id else None),
            "nilai": (inv.grand_total or ZERO) + (inv.total_pph23 or ZERO),
            "pph23": inv.total_pph23,
            "no_bukti_potong": getattr(alloc, "no_bukti_potong", None) if alloc else None,
            "masa_pajak": getattr(alloc, "masa_pajak", None) if alloc else None,
            "sumber": "INVOICE",
        })

    # AUD-12 — retur pembelian membalik hutang PPh 23 (Dr 213002):
    pr_rows = (
        db.query(PurchaseRetur)
        .filter(PurchaseRetur.total_pph23 > 0,
                PurchaseRetur.status != StatusPenjualan.DIBATALKAN,
                PurchaseRetur.tanggal >= start, PurchaseRetur.tanggal <= end)
        .order_by(PurchaseRetur.tanggal)
        .all()
    )
    pph23_retur_pembelian = [{
        "tanggal": r.tanggal,
        "no_dokumen": r.no_retur,
        "no_invoice": (r.purchase_invoice.no_form if r.purchase_invoice else None),
        "pihak": (db.get(Supplier, r.supplier_id).nama if r.supplier_id else None),
        "pph23": -(r.total_pph23 or ZERO),
        "masa_pajak": _masa(_local_day(r.tanggal)),
    } for r in pr_rows]

    total_dipotong_pihak_lain = sum((r["pph23"] for r in dipotong_pihak_lain), ZERO)
    total_dipotong_dari_supplier = sum((r["pph23"] for r in dipotong_dari_supplier), ZERO)
    total_invoice_penjualan = sum((r["pph23"] for r in pph23_invoice_penjualan), ZERO) \
        + sum((r["pph23"] for r in pph23_retur_penjualan), ZERO)
    total_invoice_pembelian = sum((r["pph23"] for r in pph23_invoice_pembelian), ZERO) \
        + sum((r["pph23"] for r in pph23_retur_pembelian), ZERO)

    # Ringkasan per masa pajak dari bukti potong (allocation rows)
    per_masa_map: dict[str, dict] = {}
    for r in dipotong_pihak_lain + dipotong_dari_supplier:
        masa = r.get("masa_pajak") or _masa(_local_day(r["tanggal"]))
        bucket = per_masa_map.setdefault(
            masa, {"masa_pajak": masa, "dipotong_pihak_lain": ZERO, "dipotong_dari_supplier": ZERO})
        if r in dipotong_pihak_lain:
            bucket["dipotong_pihak_lain"] += r["pph23"]
        else:
            bucket["dipotong_dari_supplier"] += r["pph23"]
    per_masa = [per_masa_map[k] for k in sorted(per_masa_map)]

    return {
        "periode": {"dari": dari.isoformat(), "sampai": sampai.isoformat()},
        "dipotong_pihak_lain": dipotong_pihak_lain,
        "total_dipotong_pihak_lain": total_dipotong_pihak_lain,
        "dipotong_dari_supplier": dipotong_dari_supplier,
        "total_dipotong_dari_supplier": total_dipotong_dari_supplier,
        "pph23_invoice_penjualan": pph23_invoice_penjualan,
        "total_pph23_invoice_penjualan": total_invoice_penjualan,
        "pph23_retur_penjualan": pph23_retur_penjualan,
        "total_pph23_retur_penjualan": sum((r["pph23"] for r in pph23_retur_penjualan), ZERO),
        "pph23_invoice_pembelian": pph23_invoice_pembelian,
        "total_pph23_invoice_pembelian": total_invoice_pembelian,
        "pph23_retur_pembelian": pph23_retur_pembelian,
        "total_pph23_retur_pembelian": sum((r["pph23"] for r in pph23_retur_pembelian), ZERO),
        "per_masa": per_masa,
    }
