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
# LAPORAN: ARUS KAS
# ============================================================

def get_arus_kas(
    db: Session, date_from: datetime, date_to: datetime
) -> dict:
    # Operasional: jurnal yang terkait COA PENDAPATAN, HPP, BEBAN, PIUTANG, HUTANG
    # Investasi: jurnal terkait ASET TETAP
    # Pembiayaan: jurnal terkait MODAL

    # Simple approach: classify by header of akun_perkiraan in jurnal_detail
    # where akun is under KAS DAN SETARA KAS (the cash account side)
    # We look at the OTHER side of the journal entry to classify

    # For demo, let's use a simpler approach:
    # Query all jurnal entries that affect KAS accounts, classify by counter-account header

    kas_coa_ids = [
        r[0] for r in
        db.query(AkunPerkiraan.id)
        .filter(
            AkunPerkiraan.header == HeaderCOA.AKTIVA,
            AkunPerkiraan.status == "AKTIF",
            AkunPerkiraan.kode.like("1%"),
        )
        .all()
    ]

    operasional = {"items": [], "total": Decimal("0")}
    investasi = {"items": [], "total": Decimal("0")}
    pembiayaan = {"items": [], "total": Decimal("0")}

    # Penerimaan kas
    penerimaan_list = (
        db.query(PenerimaanKas)
        .filter(
            PenerimaanKas.tanggal >= date_from,
            PenerimaanKas.tanggal <= date_to,
            PenerimaanKas.status == StatusTransaksi.SELESAI,
        )
        .all()
    )
    for p in penerimaan_list:
        operasional["items"].append({
            "nama": f"Penerimaan kas - {p.no_bukti}",
            "jumlah": Decimal(str(p.total_nilai)),
        })
        operasional["total"] += Decimal(str(p.total_nilai))

    # Pembayaran kas
    pembayaran_list = (
        db.query(PembayaranKas)
        .filter(
            PembayaranKas.tanggal >= date_from,
            PembayaranKas.tanggal <= date_to,
            PembayaranKas.status == StatusTransaksi.SELESAI,
        )
        .all()
    )
    for p in pembayaran_list:
        operasional["items"].append({
            "nama": f"Pembayaran kas - {p.no_bukti}",
            "jumlah": -Decimal(str(p.total_nilai)),
        })
        operasional["total"] -= Decimal(str(p.total_nilai))

    # Transfer (net effect on kas = biaya_transfer saja, transfer antar kas net 0)
    transfer_list = (
        db.query(TransferBank)
        .filter(
            TransferBank.tanggal >= date_from,
            TransferBank.tanggal <= date_to,
            TransferBank.status == StatusTransaksi.SELESAI,
        )
        .all()
    )
    for t in transfer_list:
        if t.biaya_transfer and Decimal(str(t.biaya_transfer)) > 0:
            operasional["items"].append({
                "nama": f"Biaya transfer - {t.no_transfer}",
                "jumlah": -Decimal(str(t.biaya_transfer)),
            })
            operasional["total"] -= Decimal(str(t.biaya_transfer))

    # Saldo awal kas
    saldo_awal = Decimal("0")
    kas_akun_ids = [
        r[0] for r in
        db.query(KasBankAkun.akun_perkiraan_id).filter(KasBankAkun.status == "AKTIF").all()
    ]
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
