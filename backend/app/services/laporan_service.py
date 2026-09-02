"""Service untuk Dashboard & Laporan.

Semua query agregasi untuk dashboard widget dan laporan keuangan.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Tuple, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func, extract, and_, or_, case, text

from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun, SaldoNormal
from app.models.detail.jurnal_detail import JurnalDetail
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal, RefModule
from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
from app.models.transaksi.penjualan.sales_order import StatusPenjualan
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.models.transaksi.kas_bank.penerimaan import PenerimaanKas
from app.models.transaksi.kas_bank.pembayaran import PembayaranKas, StatusTransaksi
from app.models.transaksi.kas_bank.transfer_bank import TransferBank
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank
from app.models.master.pelanggan import Pelanggan
from app.models.master.syarat_bayar import SyaratBayar


# ============================================================
# HELPER: Ambil saldo akun dari jurnal (POSTED only)
# ============================================================

def _saldo_per_akun_list(
    db: Session,
    header: HeaderCOA,
    date_from: datetime,
    date_to: Optional[datetime] = None,
    only_detail: bool = True,
) -> List[dict]:
    """Return list of {kode, nama, total} per akun for a given header."""
    q = (
        db.query(
            AkunPerkiraan.kode,
            AkunPerkiraan.nama,
            AkunPerkiraan.saldo_normal,
            func.coalesce(func.sum(JurnalDetail.debit), 0).label("total_debit"),
            func.coalesce(func.sum(JurnalDetail.kredit), 0).label("total_kredit"),
        )
        .join(JurnalDetail, JurnalDetail.akun_perkiraan_id == AkunPerkiraan.id)
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            AkunPerkiraan.header == header,
            AkunPerkiraan.status == "AKTIF",
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal >= date_from,
        )
    )
    if date_to:
        q = q.filter(JurnalUmum.tanggal <= date_to)
    if only_detail:
        q = q.filter(AkunPerkiraan.tingkat == TingkatAkun.DETAIL)
    q = q.group_by(AkunPerkiraan.id, AkunPerkiraan.kode, AkunPerkiraan.nama, AkunPerkiraan.saldo_normal)
    q = q.having(
        or_(
            func.coalesce(func.sum(JurnalDetail.debit), 0) != 0,
            func.coalesce(func.sum(JurnalDetail.kredit), 0) != 0,
        )
    )

    rows = q.all()
    result = []
    for r in rows:
        if r.saldo_normal == SaldoNormal.DEBIT:
            total = Decimal(str(r.total_debit)) - Decimal(str(r.total_kredit))
        else:
            total = Decimal(str(r.total_kredit)) - Decimal(str(r.total_debit))
        if total != Decimal("0"):
            result.append({
                "kode_akun": r.kode,
                "nama_akun": r.nama,
                "total": total,
            })
    return result


def _total_by_header(
    db: Session,
    header: HeaderCOA,
    date_from: datetime,
    date_to: Optional[datetime] = None,
) -> Decimal:
    """Total saldo untuk semua akun di bawah suatu header."""
    items = _saldo_per_akun_list(db, header, date_from, date_to, only_detail=True)
    return sum((i["total"] for i in items), Decimal("0"))


# ============================================================
# DASHBOARD
# ============================================================

def get_dashboard_laba_rugi(
    db: Session, bulan: int, tahun: int
) -> dict:
    """Widget Laba/Rugi untuk bulan tertentu."""
    date_from = datetime(tahun, bulan, 1)
    if bulan == 12:
        date_to = datetime(tahun, 12, 31, 23, 59, 59)
    else:
        date_to = datetime(tahun, bulan + 1, 1) - timedelta(seconds=1)

    pendapatan = _total_by_header(db, HeaderCOA.PENDAPATAN, date_from, date_to)
    hpp = _total_by_header(db, HeaderCOA.HPP, date_from, date_to)
    beban = _total_by_header(db, HeaderCOA.BEBAN, date_from, date_to)
    laba_kotor = pendapatan - hpp
    laba_bersih = laba_kotor - beban

    return {
        "pendapatan": pendapatan,
        "hpp": hpp,
        "laba_kotor": laba_kotor,
        "beban": beban,
        "laba_bersih": laba_bersih,
    }


def get_dashboard_cashflow(
    db: Session, bulan: int, tahun: int
) -> dict:
    """Widget Cashflow dari tabel penerimaan/pembayaran kas."""
    date_from = datetime(tahun, bulan, 1)
    if bulan == 12:
        date_to = datetime(tahun, 12, 31, 23, 59, 59)
    else:
        date_to = datetime(tahun, bulan + 1, 1) - timedelta(seconds=1)

    # Saldo awal = sum kas_bank_akun.saldo at start of month (from jurnal before this month)
    # Subquery: hitung net saldo per akun kas/bank sebelum bulan ini
    subq = (
        db.query(
            AkunPerkiraan.id,
            AkunPerkiraan.saldo_normal,
            func.coalesce(func.sum(JurnalDetail.debit), 0).label("td"),
            func.coalesce(func.sum(JurnalDetail.kredit), 0).label("tk"),
        )
        .join(JurnalDetail, JurnalDetail.akun_perkiraan_id == AkunPerkiraan.id)
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .join(KasBankAkun, KasBankAkun.akun_perkiraan_id == AkunPerkiraan.id)
        .filter(
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal < date_from,
        )
        .group_by(AkunPerkiraan.id, AkunPerkiraan.saldo_normal)
        .subquery()
    )

    rows = db.query(subq).all()
    saldo_awal = Decimal("0")
    for r in rows:
        td = Decimal(str(r.td))
        tk = Decimal(str(r.tk))
        if r.saldo_normal == SaldoNormal.DEBIT:
            saldo_awal += td - tk
        else:
            saldo_awal += tk - td


    # Penerimaan kas bulan ini
    penerimaan_q = (
        db.query(func.coalesce(func.sum(PenerimaanKas.total_nilai), 0))
        .filter(
            PenerimaanKas.tanggal >= date_from,
            PenerimaanKas.tanggal <= date_to,
            PenerimaanKas.status == StatusTransaksi.SELESAI,
        )
    )
    penerimaan = Decimal(str(penerimaan_q.scalar() or 0))

    # Pengeluaran kas bulan ini
    pengeluaran_q = (
        db.query(func.coalesce(func.sum(PembayaranKas.total_nilai), 0))
        .filter(
            PembayaranKas.tanggal >= date_from,
            PembayaranKas.tanggal <= date_to,
            PembayaranKas.status == StatusTransaksi.SELESAI,
        )
    )
    pengeluaran = Decimal(str(pengeluaran_q.scalar() or 0))

    saldo_akhir = saldo_awal + penerimaan - pengeluaran

    return {
        "saldo_awal": saldo_awal,
        "penerimaan": penerimaan,
        "pengeluaran": pengeluaran,
        "saldo_akhir": saldo_akhir,
    }


def get_dashboard_beban_biaya(
    db: Session, bulan: int, tahun: int
) -> dict:
    """Widget Beban Biaya (pie/bar chart)."""
    date_from = datetime(tahun, bulan, 1)
    if bulan == 12:
        date_to = datetime(tahun, 12, 31, 23, 59, 59)
    else:
        date_to = datetime(tahun, bulan + 1, 1) - timedelta(seconds=1)

    items = _saldo_per_akun_list(db, HeaderCOA.BEBAN, date_from, date_to, only_detail=True)
    # Sort descending by total
    items.sort(key=lambda x: x["total"], reverse=True)

    return {
        "items": [
            {"nama_beban": i["nama_akun"], "jumlah": i["total"]} for i in items
        ]
    }


def get_dashboard_tren_penjualan(
    db: Session, bulan: int, tahun: int
) -> dict:
    """Widget Tren Penjualan 6 bulan terakhir."""
    bulan_names = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
                   "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

    items = []
    for i in range(5, -1, -1):
        m = bulan - i
        y = tahun
        while m < 1:
            m += 12
            y -= 1

        date_from = datetime(y, m, 1)
        if m == 12:
            date_to = datetime(y, 12, 31, 23, 59, 59)
        else:
            date_to = datetime(y, m + 1, 1) - timedelta(seconds=1)

        total_q = (
            db.query(func.coalesce(func.sum(SalesInvoice.grand_total), 0))
            .filter(
                SalesInvoice.tanggal >= date_from,
                SalesInvoice.tanggal <= date_to,
                SalesInvoice.status == StatusPenjualan.SELESAI,
            )
        )
        total = Decimal(str(total_q.scalar() or 0))
        items.append({
            "bulan": f"{bulan_names[m-1]} {y}",
            "total": total,
        })

    return {"items": items}


def get_dashboard_faktur_jatuh_tempo(db: Session) -> dict:
    """Widget Faktur Jatuh Tempo."""
    today = datetime.now()

    invoices = (
        db.query(
            SalesInvoice.no_invoice,
            Pelanggan.nama.label("pelanggan"),
            SalesInvoice.grand_total,
            SalesInvoice.tanggal,
            SalesInvoice.status,
            SyaratBayar.hari,
        )
        .join(Pelanggan, Pelanggan.id == SalesInvoice.pelanggan_id)
        .outerjoin(SyaratBayar, SyaratBayar.id == SalesInvoice.syarat_bayar_id)
        .filter(
            SalesInvoice.status != StatusPenjualan.DIBATALKAN,
            SalesInvoice.status != StatusPenjualan.DRAFT,
            SalesInvoice.grand_total > 0,
        )
        .order_by(SalesInvoice.tanggal.asc())
        .limit(20)
        .all()
    )

    items = []
    for inv in invoices:
        # Hitung jatuh tempo
        if inv.hari and inv.hari > 0:
            jt = inv.tanggal + timedelta(days=inv.hari)
        else:
            jt = inv.tanggal  # Tunai

        # Cek apakah belum lunas (belum ada pembayaran penuh - sederhana untuk demo)
        is_overdue = jt.date() < today.date()

        items.append({
            "no_faktur": inv.no_invoice,
            "pelanggan": inv.pelanggan,
            "jumlah": Decimal(str(inv.grand_total)),
            "jatuh_tempo": jt.strftime("%Y-%m-%d"),
            "status": "JATUH TEMPO" if is_overdue else str(inv.status.value if hasattr(inv.status, 'value') else inv.status),
        })

    # Sort: jatuh tempo paling dekat duluan
    items.sort(key=lambda x: x["jatuh_tempo"])

    return {"items": items}


def get_dashboard_aktivitas_terbaru(db: Session) -> dict:
    """Widget Aktivitas Terbaru (10 transaksi terakhir)."""
    activities = []

    # Sales Invoice
    si_list = (
        db.query(
            SalesInvoice.no_invoice,
            SalesInvoice.tanggal,
            SalesInvoice.grand_total,
            Pelanggan.nama,
        )
        .join(Pelanggan, Pelanggan.id == SalesInvoice.pelanggan_id)
        .filter(SalesInvoice.status != StatusPenjualan.DIBATALKAN)
        .order_by(SalesInvoice.created_at.desc())
        .limit(5)
        .all()
    )
    for si in si_list:
        activities.append({
            "tipe": "PENJUALAN",
            "deskripsi": f"Invoice {si.nama}",
            "nomor": si.no_invoice,
            "tanggal": si.tanggal.strftime("%Y-%m-%d") if si.tanggal else "",
            "jumlah": Decimal(str(si.grand_total)),
            "_sort": si.created_at if si.created_at else si.tanggal,
        })

    # Purchase Invoice
    pi_list = (
        db.query(
            PurchaseInvoice.no_faktur,
            PurchaseInvoice.tanggal,
            PurchaseInvoice.grand_total,
        )
        .filter(PurchaseInvoice.status != StatusPenjualan.DIBATALKAN)
        .order_by(PurchaseInvoice.created_at.desc())
        .limit(5)
        .all()
    )
    for pi in pi_list:
        activities.append({
            "tipe": "PEMBELIAN",
            "deskripsi": f"Faktur {pi.no_faktur}",
            "nomor": pi.no_faktur,
            "tanggal": pi.tanggal.strftime("%Y-%m-%d") if pi.tanggal else "",
            "jumlah": Decimal(str(pi.grand_total)),
            "_sort": pi.created_at if pi.created_at else pi.tanggal,
        })

    # Penerimaan Kas
    pk_list = (
        db.query(
            PenerimaanKas.no_bukti,
            PenerimaanKas.tanggal,
            PenerimaanKas.total_nilai,
            PenerimaanKas.pemberi,
        )
        .filter(PenerimaanKas.status == StatusTransaksi.SELESAI)
        .order_by(PenerimaanKas.created_at.desc())
        .limit(3)
        .all()
    )
    for pk in pk_list:
        activities.append({
            "tipe": "PENERIMAAN",
            "deskripsi": f"Penerimaan dari {pk.pemberi or '-'}",
            "nomor": pk.no_bukti,
            "tanggal": pk.tanggal.strftime("%Y-%m-%d") if pk.tanggal else "",
            "jumlah": Decimal(str(pk.total_nilai)),
            "_sort": pk.created_at if pk.created_at else pk.tanggal,
        })

    # Pembayaran Kas
    pb_list = (
        db.query(
            PembayaranKas.no_bukti,
            PembayaranKas.tanggal,
            PembayaranKas.total_nilai,
            PembayaranKas.penerima,
        )
        .filter(PembayaranKas.status == StatusTransaksi.SELESAI)
        .order_by(PembayaranKas.created_at.desc())
        .limit(3)
        .all()
    )
    for pb in pb_list:
        activities.append({
            "tipe": "PEMBAYARAN",
            "deskripsi": f"Pembayaran ke {pb.penerima or '-'}",
            "nomor": pb.no_bukti,
            "tanggal": pb.tanggal.strftime("%Y-%m-%d") if pb.tanggal else "",
            "jumlah": Decimal(str(pb.total_nilai)),
            "_sort": pb.created_at if pb.created_at else pb.tanggal,
        })

    # Sort by _sort desc, take top 10
    activities.sort(key=lambda x: x.get("_sort") or datetime.min, reverse=True)
    top10 = activities[:10]

    # Remove _sort key
    for a in top10:
        a.pop("_sort", None)

    return {"items": top10}

# ============================================================
# LAPORAN: NERACA SALDO (TRIAL BALANCE)
# ============================================================

def get_neraca_saldo(
    db: Session, date_from: datetime, date_to: datetime
) -> dict:
    """Neraca Saldo: semua akun DETAIL AKTIF, total debit & kredit POSTED.
    Validasi total_debit == total_kredit (selisih = 0)."""
    q = (
        db.query(
            AkunPerkiraan.kode,
            AkunPerkiraan.nama,
            AkunPerkiraan.saldo_normal,
            func.coalesce(func.sum(JurnalDetail.debit), 0).label("total_debit"),
            func.coalesce(func.sum(JurnalDetail.kredit), 0).label("total_kredit"),
        )
        .join(JurnalDetail, JurnalDetail.akun_perkiraan_id == AkunPerkiraan.id)
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            AkunPerkiraan.tingkat == TingkatAkun.DETAIL,
            AkunPerkiraan.status == "AKTIF",
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal >= date_from,
            JurnalUmum.tanggal <= date_to,
        )
        .group_by(
            AkunPerkiraan.id,
            AkunPerkiraan.kode,
            AkunPerkiraan.nama,
            AkunPerkiraan.saldo_normal,
        )
        .having(
            or_(
                func.coalesce(func.sum(JurnalDetail.debit), 0) != 0,
                func.coalesce(func.sum(JurnalDetail.kredit), 0) != 0,
            )
        )
        .order_by(AkunPerkiraan.kode)
        .all()
    )

    items = []
    grand_debit = Decimal("0")
    grand_kredit = Decimal("0")

    for r in q:
        td = Decimal(str(r.total_debit))
        tk = Decimal(str(r.total_kredit))
        sn = r.saldo_normal

        if sn == SaldoNormal.DEBIT:
            saldo = td - tk
        else:
            saldo = tk - td

        items.append({
            "kode_akun": r.kode,
            "nama_akun": r.nama,
            "saldo_normal": sn.value if hasattr(sn, "value") else str(sn),
            "total_debit": td,
            "total_kredit": tk,
            "saldo": saldo,
        })

        grand_debit += td
        grand_kredit += tk

    return {
        "periode": {
            "dari": date_from.strftime("%Y-%m-%d"),
            "sampai": date_to.strftime("%Y-%m-%d"),
        },
        "akun": items,
        "total_debit": grand_debit,
        "total_kredit": grand_kredit,
        "selisih": grand_debit - grand_kredit,
    }

# ============================================================
# LAPORAN: PERUBAHAN MODAL (Statement of Changes in Equity)
# ============================================================

def get_perubahan_modal(
    db: Session, date_from: datetime, date_to: datetime
) -> dict:
    """Perubahan Modal: saldo awal, mutasi, dan saldo akhir per akun MODAL,
    ditambah laba/rugi berjalan periode ini.

    Akun MODAL saldo_normal = KREDIT, jadi:
      saldo_awal  = kumulatif (kredit - debit) sebelum date_from
      perubahan   = kredit - debit dalam periode
      saldo_akhir = saldo_awal + perubahan

    Laba/rugi berjalan = Pendapatan - HPP - Beban (dalam periode).
    """
    # --- 1. Ambil semua akun MODAL DETAIL AKTIF ---
    modal_akun = (
        db.query(
            AkunPerkiraan.id,
            AkunPerkiraan.kode,
            AkunPerkiraan.nama,
            AkunPerkiraan.saldo_normal,
        )
        .filter(
            AkunPerkiraan.header == HeaderCOA.MODAL,
            AkunPerkiraan.tingkat == TingkatAkun.DETAIL,
            AkunPerkiraan.status == "AKTIF",
        )
        .order_by(AkunPerkiraan.kode)
        .all()
    )

    if not modal_akun:
        # Tidak ada akun MODAL, tetap hitung laba/rugi
        laba_rugi = (
            _total_by_header(db, HeaderCOA.PENDAPATAN, date_from, date_to)
            - _total_by_header(db, HeaderCOA.HPP, date_from, date_to)
            - _total_by_header(db, HeaderCOA.BEBAN, date_from, date_to)
        )
        return {
            "periode": {
                "dari": date_from.strftime("%Y-%m-%d"),
                "sampai": date_to.strftime("%Y-%m-%d"),
            },
            "akun_modal": [],
            "laba_rugi_berjalan": laba_rugi,
            "total_modal_awal": Decimal("0"),
            "total_modal_akhir": laba_rugi,
        }

    modal_ids = [m.id for m in modal_akun]

    # --- 2. Saldo awal: kumulatif SEBELUM periode ---
    awal_rows = (
        db.query(
            JurnalDetail.akun_perkiraan_id,
            func.coalesce(func.sum(JurnalDetail.debit), 0).label("td"),
            func.coalesce(func.sum(JurnalDetail.kredit), 0).label("tk"),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id.in_(modal_ids),
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal < date_from,
        )
        .group_by(JurnalDetail.akun_perkiraan_id)
        .all()
    )
    saldo_awal_map = {}
    for r in awal_rows:
        td = Decimal(str(r.td))
        tk = Decimal(str(r.tk))
        # MODAL normal = KREDIT, tapi kita respect saldo_normal masing-masing akun
        akun_obj = next((m for m in modal_akun if m.id == r.akun_perkiraan_id), None)
        sn = akun_obj.saldo_normal if akun_obj else SaldoNormal.KREDIT
        if sn == SaldoNormal.KREDIT:
            saldo_awal_map[r.akun_perkiraan_id] = tk - td
        else:
            saldo_awal_map[r.akun_perkiraan_id] = td - tk

    # --- 3. Mutasi dalam periode ---
    mutasi_rows = (
        db.query(
            JurnalDetail.akun_perkiraan_id,
            func.coalesce(func.sum(JurnalDetail.debit), 0).label("td"),
            func.coalesce(func.sum(JurnalDetail.kredit), 0).label("tk"),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id.in_(modal_ids),
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal >= date_from,
            JurnalUmum.tanggal <= date_to,
        )
        .group_by(JurnalDetail.akun_perkiraan_id)
        .all()
    )
    mutasi_map = {}
    for r in mutasi_rows:
        mutasi_map[r.akun_perkiraan_id] = {
            "debit": Decimal(str(r.td)),
            "kredit": Decimal(str(r.tk)),
        }

    # --- 4. Gabungkan ---
    items = []
    total_modal_awal = Decimal("0")
    total_modal_akhir = Decimal("0")

    for m in modal_akun:
        sa = saldo_awal_map.get(m.id, Decimal("0"))
        mut = mutasi_map.get(m.id, {"debit": Decimal("0"), "kredit": Decimal("0")})
        md = mut["debit"]
        mk = mut["kredit"]

        # Perubahan = kredit - debit (sesuai saldo_normal KREDIT)
        if m.saldo_normal == SaldoNormal.KREDIT:
            perubahan = mk - md
        else:
            perubahan = md - mk

        saldo_akhir = sa + perubahan

        # Hanya tampilkan yang ada perubahannya atau saldo awalnya != 0
        if sa != Decimal("0") or perubahan != Decimal("0"):
            items.append({
                "kode_akun": m.kode,
                "nama_akun": m.nama,
                "saldo_awal": sa,
                "mutasi_debit": md,
                "mutasi_kredit": mk,
                "perubahan": perubahan,
                "saldo_akhir": saldo_akhir,
            })

        total_modal_awal += sa
        total_modal_akhir += saldo_akhir

    # --- 5. Laba/rugi berjalan periode ini ---
    laba_rugi_berjalan = (
        _total_by_header(db, HeaderCOA.PENDAPATAN, date_from, date_to)
        - _total_by_header(db, HeaderCOA.HPP, date_from, date_to)
        - _total_by_header(db, HeaderCOA.BEBAN, date_from, date_to)
    )

    # Laba/rugi berjalan masuk ke total_modal_akhir
    grand_modal_akhir = total_modal_akhir + laba_rugi_berjalan

    return {
        "periode": {
            "dari": date_from.strftime("%Y-%m-%d"),
            "sampai": date_to.strftime("%Y-%m-%d"),
        },
        "akun_modal": items,
        "laba_rugi_berjalan": laba_rugi_berjalan,
        "total_modal_awal": total_modal_awal,
        "total_modal_akhir": grand_modal_akhir,
    }


# ============================================================
# LAPORAN: LABA RUGI
# ============================================================

def get_laba_rugi(
    db: Session, date_from: datetime, date_to: datetime
) -> dict:
    pendapatan = _saldo_per_akun_list(db, HeaderCOA.PENDAPATAN, date_from, date_to)
    hpp = _saldo_per_akun_list(db, HeaderCOA.HPP, date_from, date_to)
    beban = _saldo_per_akun_list(db, HeaderCOA.BEBAN, date_from, date_to)

    total_pendapatan = sum((i["total"] for i in pendapatan), Decimal("0"))
    total_hpp = sum((i["total"] for i in hpp), Decimal("0"))
    total_beban = sum((i["total"] for i in beban), Decimal("0"))
    laba_kotor = total_pendapatan - total_hpp
    laba_bersih = laba_kotor - total_beban

    return {
        "periode": {
            "dari": date_from.strftime("%Y-%m-%d"),
            "sampai": date_to.strftime("%Y-%m-%d"),
        },
        "pendapatan": pendapatan,
        "hpp": hpp,
        "beban": beban,
        "total_pendapatan": total_pendapatan,
        "total_hpp": total_hpp,
        "total_beban": total_beban,
        "laba_kotor": laba_kotor,
        "laba_bersih": laba_bersih,
    }


# ============================================================
# LAPORAN: NERACA
# ============================================================

def get_neraca(db: Session, tanggal: datetime) -> dict:
    # Saldo akumulatif dari awal sampai tanggal
    aset = _saldo_per_akun_list(db, HeaderCOA.AKTIVA, datetime(2000, 1, 1), tanggal)
    kewajiban = _saldo_per_akun_list(db, HeaderCOA.KEWAJIBAN, datetime(2000, 1, 1), tanggal)
    ekuitas = _saldo_per_akun_list(db, HeaderCOA.MODAL, datetime(2000, 1, 1), tanggal)

    # Juga ambil pendapatan & beban kumulatif untuk dimasukkan ke ekuitas (laba ditahan)
    laba_ditahan = (
        _total_by_header(db, HeaderCOA.PENDAPATAN, datetime(2000, 1, 1), tanggal)
        - _total_by_header(db, HeaderCOA.HPP, datetime(2000, 1, 1), tanggal)
        - _total_by_header(db, HeaderCOA.BEBAN, datetime(2000, 1, 1), tanggal)
    )

    if laba_ditahan != Decimal("0"):
        ekuitas.append({
            "kode_akun": "-",
            "nama_akun": "Laba/(Rugi) Berjalan",
            "total": laba_ditahan,
        })

    total_aset = sum((i["total"] for i in aset), Decimal("0"))
    total_kewajiban = sum((i["total"] for i in kewajiban), Decimal("0"))
    total_ekuitas = sum((i["total"] for i in ekuitas), Decimal("0"))

    return {
        "tanggal": tanggal.strftime("%Y-%m-%d"),
        "aset": aset,
        "kewajiban": kewajiban,
        "ekuitas": ekuitas,
        "total_aset": total_aset,
        "total_kewajiban": total_kewajiban,
        "total_ekuitas": total_ekuitas,
    }


# ============================================================
# LAPORAN: ARUS KAS (jurnal-based classification)
# ============================================================

def get_arus_kas(
    db: Session, date_from: datetime, date_to: datetime
) -> dict:
    """Arus Kas: klasifikasi berdasarkan counter-account header.

    Untuk setiap jurnal yang menyentuh akun KAS/BANK:
      1. Hitung net cash effect (debit - kredit di sisi KAS/BANK)
      2. Lihat akun counter (sisi non-KAS/BANK) → baca header-nya
      3. Klasifikasi:
         - PENDAPATAN / HPP / BEBAN   → Operasional
         - AKTIVA (non-cash)           → Investasi
         - MODAL / KEWAJIBAN           → Pembiayaan
    """
    # 1. Ambil KAS/BANK akun IDs dari master KasBankAkun
    kas_akun_ids = [
        r[0] for r in
        db.query(KasBankAkun.akun_perkiraan_id)
        .filter(KasBankAkun.status == "AKTIF")
        .all()
    ]

    operasional = {"items": [], "total": Decimal("0")}
    investasi = {"items": [], "total": Decimal("0")}
    pembiayaan = {"items": [], "total": Decimal("0")}

    if kas_akun_ids:
        # 2. Cari semua jurnal ID di periode yang menyentuh KAS/BANK
        jurnal_ids_rows = (
            db.query(JurnalDetail.jurnal_umum_id)
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                JurnalDetail.akun_perkiraan_id.in_(kas_akun_ids),
                JurnalUmum.status == StatusJurnal.POSTED,
                JurnalUmum.tanggal >= date_from,
                JurnalUmum.tanggal <= date_to,
            )
            .distinct()
            .all()
        )
        jurnal_ids = [r[0] for r in jurnal_ids_rows]

        if jurnal_ids:
            # 3. Net cash effect per jurnal (sisi KAS/BANK saja)
            cash_rows = (
                db.query(
                    JurnalDetail.jurnal_umum_id,
                    func.coalesce(func.sum(JurnalDetail.debit), 0).label("td"),
                    func.coalesce(func.sum(JurnalDetail.kredit), 0).label("tk"),
                )
                .filter(
                    JurnalDetail.jurnal_umum_id.in_(jurnal_ids),
                    JurnalDetail.akun_perkiraan_id.in_(kas_akun_ids),
                )
                .group_by(JurnalDetail.jurnal_umum_id)
                .all()
            )
            cash_map = {}
            for r in cash_rows:
                td = Decimal(str(r.td))
                tk = Decimal(str(r.tk))
                cash_map[r.jurnal_umum_id] = {
                    "debit": td,
                    "kredit": tk,
                    "net": td - tk,  # positif = masuk, negatif = keluar
                }

            # 4. Counter-account (sisi non-KAS/BANK) per jurnal
            counter_rows = (
                db.query(
                    JurnalDetail.jurnal_umum_id,
                    AkunPerkiraan.header,
                    AkunPerkiraan.kode,
                    AkunPerkiraan.nama,
                    JurnalUmum.no_jurnal,
                    JurnalUmum.tanggal,
                    func.coalesce(func.sum(JurnalDetail.debit), 0).label("td"),
                    func.coalesce(func.sum(JurnalDetail.kredit), 0).label("tk"),
                )
                .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
                .join(AkunPerkiraan, AkunPerkiraan.id == JurnalDetail.akun_perkiraan_id)
                .filter(
                    JurnalDetail.jurnal_umum_id.in_(jurnal_ids),
                    JurnalDetail.akun_perkiraan_id.notin_(kas_akun_ids),
                )
                .group_by(
                    JurnalDetail.jurnal_umum_id,
                    AkunPerkiraan.id,
                    AkunPerkiraan.header,
                    AkunPerkiraan.kode,
                    AkunPerkiraan.nama,
                    JurnalUmum.no_jurnal,
                    JurnalUmum.tanggal,
                )
                .all()
            )

            # Group counter-accounts per jurnal_id
            counter_by_jurnal = {}
            for r in counter_rows:
                jid = r.jurnal_umum_id
                if jid not in counter_by_jurnal:
                    counter_by_jurnal[jid] = []
                counter_by_jurnal[jid].append(r)

            # 5. Klasifikasi per jurnal
            OPERASIONAL = {HeaderCOA.PENDAPATAN, HeaderCOA.HPP, HeaderCOA.BEBAN}
            INVESTASI = {HeaderCOA.AKTIVA}
            PEMBIAYAAN = {HeaderCOA.MODAL, HeaderCOA.KEWAJIBAN}

            for jid in jurnal_ids:
                cash_info = cash_map.get(jid)
                if not cash_info or cash_info["net"] == Decimal("0"):
                    continue

                net_cash = cash_info["net"]
                counters = counter_by_jurnal.get(jid, [])
                if not counters:
                    continue

                # Tentukan klasifikasi berdasarkan counter-account terbesar
                best_header = None
                best_amount = Decimal("0")
                best_desc = ""
                no_jurnal_str = counters[0].no_jurnal or ""

                for c in counters:
                    amt = max(Decimal(str(c.td)), Decimal(str(c.tk)))
                    if amt > best_amount:
                        best_amount = amt
                        best_header = c.header
                        best_desc = f"{c.kode} - {c.nama}"

                if best_header is None:
                    continue

                item_name = f"{no_jurnal_str} ({best_desc})"
                item = {"nama": item_name, "jumlah": net_cash}

                if best_header in OPERASIONAL:
                    operasional["items"].append(item)
                    operasional["total"] += net_cash
                elif best_header in INVESTASI:
                    investasi["items"].append(item)
                    investasi["total"] += net_cash
                elif best_header in PEMBIAYAAN:
                    pembiayaan["items"].append(item)
                    pembiayaan["total"] += net_cash
                else:
                    # Fallback ke operasional jika header tidak dikenali
                    operasional["items"].append(item)
                    operasional["total"] += net_cash

    # 6. Saldo awal kas (kumulatif sebelum periode)
    saldo_awal = Decimal("0")
    if kas_akun_ids:
        saldo_awal_rows = (
            db.query(
                AkunPerkiraan.kode,
                AkunPerkiraan.nama,
                AkunPerkiraan.saldo_normal,
                func.coalesce(func.sum(JurnalDetail.debit), 0).label("td"),
                func.coalesce(func.sum(JurnalDetail.kredit), 0).label("tk"),
            )
            .join(JurnalDetail, JurnalDetail.akun_perkiraan_id == AkunPerkiraan.id)
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                AkunPerkiraan.id.in_(kas_akun_ids),
                JurnalUmum.status == StatusJurnal.POSTED,
                JurnalUmum.tanggal < date_from,
            )
            .group_by(AkunPerkiraan.id, AkunPerkiraan.kode, AkunPerkiraan.nama, AkunPerkiraan.saldo_normal)
            .all()
        )
        for r in saldo_awal_rows:
            if r.saldo_normal == SaldoNormal.DEBIT:
                saldo_awal += Decimal(str(r.td)) - Decimal(str(r.tk))
            else:
                saldo_awal += Decimal(str(r.tk)) - Decimal(str(r.td))

    net_change = operasional["total"] + investasi["total"] + pembiayaan["total"]
    saldo_akhir = saldo_awal + net_change

    return {
        "periode": {
            "dari": date_from.strftime("%Y-%m-%d"),
            "sampai": date_to.strftime("%Y-%m-%d"),
        },
        "operasional": operasional,
        "investasi": investasi,
        "pembiayaan": pembiayaan,
        "net_change": net_change,
        "saldo_awal": saldo_awal,
        "saldo_akhir": saldo_akhir,
    }


# ============================================================
# LAPORAN: BUKU BESAR
# ============================================================

def get_buku_besar(
    db: Session, akun_id: UUID, date_from: datetime, date_to: datetime
) -> dict:
    akun = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == akun_id).first()
    if not akun:
        raise ValueError("Akun tidak ditemukan")

    # Saldo awal
    saldo_awal = Decimal("0")
    awal_rows = (
        db.query(
            func.coalesce(func.sum(JurnalDetail.debit), 0),
            func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id == akun_id,
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal < date_from,
        )
        .first()
    )
    if awal_rows:
        td = Decimal(str(awal_rows[0]))
        tk = Decimal(str(awal_rows[1]))
        if akun.saldo_normal == SaldoNormal.DEBIT:
            saldo_awal = td - tk
        else:
            saldo_awal = tk - td

    # Transaksi dalam periode
    rows = (
        db.query(
            JurnalUmum.tanggal,
            JurnalUmum.no_jurnal,
            JurnalDetail.keterangan,
            JurnalDetail.debit,
            JurnalDetail.kredit,
        )
        .join(JurnalDetail, JurnalDetail.jurnal_umum_id == JurnalUmum.id)
        .filter(
            JurnalDetail.akun_perkiraan_id == akun_id,
            JurnalUmum.status == StatusJurnal.POSTED,
            JurnalUmum.tanggal >= date_from,
            JurnalUmum.tanggal <= date_to,
        )
        .order_by(JurnalUmum.tanggal, JurnalUmum.no_jurnal)
        .all()
    )

    transaksi = []
    running_saldo = saldo_awal
    total_debit = Decimal("0")
    total_kredit = Decimal("0")
    for r in rows:
        d = Decimal(str(r.debit))
        k = Decimal(str(r.kredit))
        if akun.saldo_normal == SaldoNormal.DEBIT:
            running_saldo += d - k
        else:
            running_saldo += k - d
        total_debit += d
        total_kredit += k
        transaksi.append({
            "tanggal": r.tanggal.strftime("%Y-%m-%d") if r.tanggal else "",
            "no_jurnal": r.no_jurnal or "",
            "deskripsi": r.keterangan or "",
            "debit": d,
            "kredit": k,
            "saldo": running_saldo,
        })

    return {
        "akun": {"kode": akun.kode, "nama": akun.nama},
        "periode": {
            "dari": date_from.strftime("%Y-%m-%d"),
            "sampai": date_to.strftime("%Y-%m-%d"),
        },
        "saldo_awal": saldo_awal,
        "transaksi": transaksi,
        "total_debit": total_debit,
        "total_kredit": total_kredit,
        "saldo_akhir": running_saldo,
    }


# ============================================================
# LAPORAN: MUTASI KAS / BANK / REKAP
# ============================================================

def get_mutasi_kas_bank(
    db: Session, date_from: datetime, date_to: datetime, jenis: str = None
) -> dict:
    """Buku besar untuk semua akun KAS atau BANK."""
    q = db.query(KasBankAkun).filter(KasBankAkun.status == "AKTIF")
    if jenis:
        if jenis.upper() == "KAS":
            q = q.filter(KasBankAkun.jenis == JenisKasBank.KAS)
        elif jenis.upper() == "BANK":
            q = q.filter(KasBankAkun.jenis == JenisKasBank.BANK)
    kas_bank_list = q.all()

    all_transaksi = []
    for kb in kas_bank_list:
        akun_id = kb.akun_perkiraan_id
        if not akun_id:
            continue
        akun = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == akun_id).first()
        if not akun:
            continue

        result = get_buku_besar(db, akun_id, date_from, date_to)
        for t in result["transaksi"]:
            t["akun"] = akun.nama
        all_transaksi.extend(result["transaksi"])

    all_transaksi.sort(key=lambda x: x["tanggal"])

    return {
        "periode": {
            "dari": date_from.strftime("%Y-%m-%d"),
            "sampai": date_to.strftime("%Y-%m-%d"),
        },
        "transaksi": all_transaksi,
    }


def get_rekap_kas_bank(
    db: Session, date_from: datetime, date_to: datetime
) -> dict:
    """Rekap saldo per akun kas/bank."""
    kas_bank_list = (
        db.query(KasBankAkun)
        .filter(KasBankAkun.status == "AKTIF")
        .order_by(KasBankAkun.jenis, KasBankAkun.nama)
        .all()
    )

    items = []
    for kb in kas_bank_list:
        akun_id = kb.akun_perkiraan_id
        if not akun_id:
            continue
        akun = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == akun_id).first()
        if not akun:
            continue

        # Saldo awal
        saldo_awal = Decimal("0")
        awal = (
            db.query(
                func.coalesce(func.sum(JurnalDetail.debit), 0),
                func.coalesce(func.sum(JurnalDetail.kredit), 0),
            )
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                JurnalDetail.akun_perkiraan_id == akun_id,
                JurnalUmum.status == StatusJurnal.POSTED,
                JurnalUmum.tanggal < date_from,
            )
            .first()
        )
        if awal and akun.saldo_normal == SaldoNormal.DEBIT:
            saldo_awal = Decimal(str(awal[0])) - Decimal(str(awal[1]))
        elif awal:
            saldo_awal = Decimal(str(awal[1])) - Decimal(str(awal[0]))

        # Masuk (debit for DEBIT normal)
        masuk = (
            db.query(func.coalesce(func.sum(JurnalDetail.debit), 0))
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                JurnalDetail.akun_perkiraan_id == akun_id,
                JurnalUmum.status == StatusJurnal.POSTED,
                JurnalUmum.tanggal >= date_from,
                JurnalUmum.tanggal <= date_to,
            )
            .scalar() or 0
        )
        # Keluar (kredit for DEBIT normal)
        keluar = (
            db.query(func.coalesce(func.sum(JurnalDetail.kredit), 0))
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                JurnalDetail.akun_perkiraan_id == akun_id,
                JurnalUmum.status == StatusJurnal.POSTED,
                JurnalUmum.tanggal >= date_from,
                JurnalUmum.tanggal <= date_to,
            )
            .scalar() or 0
        )

        masuk_d = Decimal(str(masuk))
        keluar_d = Decimal(str(keluar))

        if akun.saldo_normal == SaldoNormal.DEBIT:
            saldo_akhir = saldo_awal + masuk_d - keluar_d
        else:
            saldo_akhir = saldo_awal + keluar_d - masuk_d

        items.append({
            "kode": akun.kode,
            "nama": akun.nama,
            "jenis": kb.jenis.value if kb.jenis else "-",
            "saldo_awal": saldo_awal,
            "total_masuk": masuk_d,
            "total_keluar": keluar_d,
            "saldo_akhir": saldo_akhir,
        })

    return {
        "periode": {
            "dari": date_from.strftime("%Y-%m-%d"),
            "sampai": date_to.strftime("%Y-%m-%d"),
        },
        "akun": items,
    }
