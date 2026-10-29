from datetime import datetime, date
from decimal import Decimal
from typing import Optional
import calendar

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.laporan import (
    NeracaSaldoResponse,
    PerubahanModalResponse,
    LabaRugiResponse,
    NeracaResponse,
    ArusKasResponse,
    BukuBesarResponse,
    MutasiKasBankResponse,
    RekapKasBankResponse,
    UmurPiutangResponse,
    UmurHutangResponse,
    ReportValidationResponse,
    RekonsiliasiPersediaanResponse,
    RekonsiliasiPersediaanRingkasanResponse,
    AuditTransaksiPersediaanResponse,
    RekapPpnResponse, RekapPph23Response,
)
from app.services import laporan_service
from app.services import rekonsiliasi_persediaan_service
from app.services import excel_service
from app.services.reporting_ledger import day_start, day_end

router = APIRouter()


# ==========================================
# EXPORT EXCEL — HELPERS (Update #023)
# ==========================================
def _xlsx_response(wb, filename: str) -> StreamingResponse:
    """Bungkus workbook menjadi StreamingResponse .xlsx (pola export Update #5)."""
    res = excel_service.xlsx_response(wb, filename)
    return StreamingResponse(res["stream"], media_type=res["media_type"], headers=res["headers"])


def _num(value):
    """Decimal/angka → float untuk sel Excel; None tetap None (sel kosong)."""
    return float(value) if value is not None else None


def _as_date(value: str) -> date:
    """'YYYY-MM-DD' → date (sel tanggal ber-format DD/MM/YYYY)."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def _fmt_date(value: str) -> str:
    """'2026-10-28' → '28/10/2026' (teks periode di kop laporan)."""
    return _as_date(value).strftime("%d/%m/%Y")


def _slug(text: str, max_len: int = 24) -> str:
    """Slug ASCII aman untuk nama file (karakter lain → '-', dirapatkan)."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in str(text or "").strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return (cleaned or "akun")[:max_len]


def _parse_range(dari: str, sampai: str):
    """Validasi pasangan dari/sampai (pola endpoint view) → (date_from, date_to)."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD") from None
    return date_from, date_to


# ===== Update #024 — helper bulanan (gaya workbook pembukuan ASAHI/Master MAS) =====

_BULAN_PANJANG = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
                  "Agustus", "September", "Oktober", "November", "Desember"]
_BULAN_PENDEK = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul",
                  "Agu", "Sep", "Okt", "Nov", "Des"]


def _month_ranges(date_from: date, date_to: date, max_months: int = 14):
    """Bulan kalender dari bulan(date_from) s.d. bulan(date_to).

    Return list (awal_bulan: date, akhir_bulan: date). Blok mengikuti bulan
    KALENDER (bukan hari persis dari/sampai) — sama seperti sheet NS/LR
    workbook ASAHI yang memakai EOMONTH per bulan.
    """
    months = []
    cur_y, cur_m = date_from.year, date_from.month
    while (cur_y, cur_m) <= (date_to.year, date_to.month):
        last = calendar.monthrange(cur_y, cur_m)[1]
        months.append((date(cur_y, cur_m, 1), date(cur_y, cur_m, last)))
        cur_y, cur_m = (cur_y + 1, 1) if cur_m == 12 else (cur_y, cur_m + 1)
    if len(months) > max_months:
        raise HTTPException(400, f"Rentang terlalu panjang (maks. {max_months} bulan per export) "
                                 "— persempit dari/sampai atau pecah per tahun.")
    return months


def _month_bounds(m_start: date, m_end: date):
    """(awal, akhir) bulan → pasangan datetime day_start/day_end untuk service."""
    return (day_start(datetime.combine(m_start, datetime.min.time())),
            day_end(datetime.combine(m_end, datetime.min.time())))


def _month_label(m_end: date, short: bool = True) -> str:
    """Label bulan Indonesia: 'Okt 2026' (pendek) / 'Oktober 2026' (panjang)."""
    names = _BULAN_PENDEK if short else _BULAN_PANJANG
    return f"{names[m_end.month - 1]} {m_end.year}"


def _sides_db_kr(value, saldo_normal: str):
    """Saldo ternormalisasi service → (Db, Kr) positif keduanya.

    Konvensi get_neraca_saldo: nilai positif = saldo di SISI NORMAL akun.
    Maka sisi normal memegang nilai positif, sisi lawan menampung saldo
    abnormal (nilai negatif dibalik tanda).
    """
    v = float(value or 0)
    if saldo_normal == "DEBIT":
        return (v if v > 0 else 0.0), (-v if v < 0 else 0.0)
    return (-v if v < 0 else 0.0), (v if v > 0 else 0.0)


# ===== Update #025 — helper blok bulanan kas/bank (gaya PU/HU Master MAS) =====

def _month_key_label(mkey: str) -> str:
    """Kunci bulan 'YYYY-MM' → label panjang Indonesia ('Januari 2026')."""
    try:
        y, m = mkey.split("-")[:2]
        return f"{_BULAN_PANJANG[int(m) - 1]} {int(y)}"
    except (ValueError, IndexError):
        return mkey or "-"


def _kas_bank_monthly(db, months, jenis: str | None = None):
    """Rekap kas & bank per blok bulan kalender — pola sheet PU/HU Master MAS.

    Memanggil get_rekap_kas_bank per bulan (blok kalender penuh, saldo awal
    bulan = saldo akhir bulan sebelumnya secara alami dari GL). Return
    (accounts: dict[kode → {'nama','jenis'}], monthly: list[dict[kode → row]]
    dengan row = row 'akun' service asli: saldo_awal/total_masuk/total_keluar/
    saldo_akhir, nilai Decimal).
    """
    accounts: dict[str, dict] = {}
    monthly: list[dict] = []
    for m_start, m_end in months:
        data = laporan_service.get_rekap_kas_bank(db, *_month_bounds(m_start, m_end))
        rows_m: dict[str, dict] = {}
        for a in data["akun"]:
            if jenis is not None and (a["jenis"] or "").upper() != jenis:
                continue
            rows_m[a["kode"]] = a
            accounts.setdefault(a["kode"], {"nama": a["nama"], "jenis": a["jenis"]})
        monthly.append(rows_m)
    return accounts, monthly


# ==========================================
# LAPORAN KEUANGAN
# ==========================================

@router.get("/neraca-saldo", response_model=NeracaSaldoResponse)
def get_neraca_saldo(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Neraca Saldo (Trial Balance) — verifikasi Debit = Kredit."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_neraca_saldo(db, date_from, date_to)


# ===== EXPORT EXCEL (Update #024 — gaya workbook ASAHI/Master MAS) =====
@router.get("/neraca-saldo/export")
def export_neraca_saldo(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Neraca Saldo ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #024 — layout meniru sheet "NS" Master MAS: kolom Kode/Nama/Normal
    + BLOK BULANAN (maks. 14) masing-masing 6 sub-kolom: Saldo Awal Db/Kr,
    Mutasi Db/Kr, Saldo Akhir Db/Kr. Nilai per bulan = komposisi
    get_neraca_saldo bulan kalender penuh (blok = EOMONTH, bukan hari persis).
    Angka memakai format akuntansi (nol tampil "-") + baris TOTAL per bulan.
    """
    date_from, date_to = _parse_range(dari, sampai)
    months = _month_ranges(date_from, date_to)

    # Data per bulan (blok kalender penuh) + universe akun union.
    monthly = []
    accounts: dict[str, dict] = {}
    for m_start, m_end in months:
        data = laporan_service.get_neraca_saldo(db, *_month_bounds(m_start, m_end))
        monthly.append(data)
        for akun in data["akun"]:
            accounts.setdefault(akun["kode_akun"], {
                "kode": akun["kode_akun"], "nama": akun["nama_akun"],
                "normal": akun["saldo_normal"]})
    lookups = [{a["kode_akun"]: a for a in data["akun"]} for data in monthly]

    rows: list[tuple[str, list]] = []
    for kode in sorted(accounts):
        info = accounts[kode]
        vals = [info["kode"], info["nama"], "D" if info["normal"] == "DEBIT" else "K"]
        for lookup in lookups:
            item = lookup.get(kode)
            if item is None:
                vals += [None] * 6
                continue
            sa_db, sa_kr = _sides_db_kr(item["saldo_awal"], item["saldo_normal"])
            sak_db, sak_kr = _sides_db_kr(item["saldo_akhir"], item["saldo_normal"])
            vals += [sa_db, sa_kr,
                     _num(item["total_debit"]), _num(item["total_kredit"]),
                     sak_db, sak_kr]
        rows.append(("item", vals))

    # Baris TOTAL per bulan: mutasi Db/Kr (uji seimbang D=K) + saldo akhir Db/Kr.
    vals = ["", "TOTAL", ""]
    for data in monthly:
        vals += [None, None,
                 _num(data["total_debit"]), _num(data["total_kredit"]),
                 _num(data["total_saldo_debit"]), _num(data["total_saldo_kredit"])]
    rows.append(("total", vals))

    blocks = [(_month_label(m_end, short=False),
               ["Saldo Awal Db.", "Saldo Awal Kr.", "Mutasi Db.", "Mutasi Kr.",
                "Saldo Akhir Db.", "Saldo Akhir Kr."])
              for _, m_end in months]
    n_month_cols = 6 * len(months)
    wb = excel_service.block_report_sheet(
        left_headers=["Kode Akun", "Nama Akun", "Normal"],
        blocks=blocks, rows=rows, sheet="Neraca Saldo",
        company=laporan_service.get_company_name(db),
        title="Neraca Saldo",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns=set(range(3, 3 + n_month_cols)),
        wide_columns={1},
    )
    return _xlsx_response(wb, f"neraca-saldo_{dari}_sd_{sampai}.xlsx")


@router.get("/perubahan-modal", response_model=PerubahanModalResponse)
def get_perubahan_modal(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Perubahan Modal (Statement of Changes in Equity)."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_perubahan_modal(db, date_from, date_to)


# ===== EXPORT EXCEL (Update #025 — gaya workbook ASAHI/Master MAS) =====
@router.get("/perubahan-modal/export")
def export_perubahan_modal(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Laporan Perubahan Modal ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #025 — layout meniru pola blok bulanan sheet PU Master MAS: kolom
    Kode/Nama Akun + BLOK BULANAN (maks. 14) masing-masing 3 sub-kolom
    (Saldo Awal / Perubahan / Saldo Akhir) + blok TOTAL (rentang penuh —
    angka sama seperti web). Baris Laba/(Rugi) Berjalan menaruh nilai di
    sub-kolom Perubahan; baris Total Modal Akhir memakai total_modal_*
    service (termasuk laba belum ditutup akumulasi).
    """
    date_from, date_to = _parse_range(dari, sampai)
    months = _month_ranges(date_from, date_to)
    monthly = [laporan_service.get_perubahan_modal(db, *_month_bounds(m_start, m_end))
               for m_start, m_end in months]
    total_data = laporan_service.get_perubahan_modal(db, date_from, date_to)

    # Universe akun modal (union semua bulan + total) — urut kode.
    akun_modal: dict[str, str] = {}
    for data in [*monthly, total_data]:
        for a in data["akun_modal"]:
            akun_modal.setdefault(a["kode_akun"], a["nama_akun"])

    def _block_vals(data: dict, kode: str) -> list:
        a = next((x for x in data["akun_modal"] if x["kode_akun"] == kode), None)
        if a is None:
            return [None] * 3
        return [_num(a["saldo_awal"]), _num(a["perubahan"]), _num(a["saldo_akhir"])]

    def _period_block(data: dict) -> list:
        perubahan = sum((a["perubahan"] for a in data["akun_modal"]), Decimal(0)) \
            + data["laba_rugi_berjalan"]
        return [_num(data["total_modal_awal"]), _num(perubahan), _num(data["total_modal_akhir"])]

    rows: list[tuple[str, list]] = []
    for kode in sorted(akun_modal):
        vals = [kode, akun_modal[kode]]
        for data in monthly:
            vals += _block_vals(data, kode)
        vals += _block_vals(total_data, kode)
        rows.append(("item", vals))

    # Laba/(Rugi) Berjalan: nilai di sub-kolom Perubahan tiap blok.
    vals = ["", "Laba/(Rugi) Berjalan"]
    for data in monthly:
        vals += [None, _num(data["laba_rugi_berjalan"]), None]
    vals += [None, _num(total_data["laba_rugi_berjalan"]), None]
    rows.append(("subtotal", vals))

    vals = ["", "Total Modal Akhir"]
    for data in monthly:
        vals += _period_block(data)
    vals += _period_block(total_data)
    rows.append(("total", vals))

    blocks = [(_month_label(m_end, short=False), ["Saldo Awal", "Perubahan", "Saldo Akhir"])
              for _, m_end in months]
    blocks.append(("TOTAL (Periode)", ["Saldo Awal", "Perubahan", "Saldo Akhir"]))
    n_num = 3 * (len(months) + 1)
    wb = excel_service.block_report_sheet(
        left_headers=["Kode Akun", "Nama Akun"],
        blocks=blocks, rows=rows, sheet="Perubahan Modal",
        company=laporan_service.get_company_name(db),
        title="Perubahan Modal",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns=set(range(2, 2 + n_num)),
        wide_columns={1},
    )
    return _xlsx_response(wb, f"perubahan-modal_{dari}_sd_{sampai}.xlsx")


@router.get("/laba-rugi", response_model=LabaRugiResponse)
def get_laba_rugi(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Laba Rugi."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_laba_rugi(db, date_from, date_to)


# ===== EXPORT EXCEL (Update #024 — gaya workbook ASAHI/Master MAS) =====
@router.get("/laba-rugi/export")
def export_laba_rugi(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Laporan Laba Rugi ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #024 — layout meniru sheet "LR" Master MAS: seksi berjenjang
    (Pendapatan Usaha → HPP → Laba Kotor → Biaya Operasional → Laba Usaha →
    Pendapatan/Biaya Lain-lain → Laba Bersih) × KOLOM BULANAN (maks. 14,
    masing-masing diikuti kolom "%" terhadap pendapatan usaha bulan itu) +
    kolom TOTAL (rentang penuh dari-sampai). Angka format akuntansi (nol
    tampil "-"). Kolom KOREKSI FISKAL/FISKAL template tidak direplikasi
    (ASAHI Books belum menyimpan data koreksi fiskal).
    """
    date_from, date_to = _parse_range(dari, sampai)
    months = _month_ranges(date_from, date_to)

    monthly = [laporan_service.get_laba_rugi(db, *_month_bounds(m_start, m_end))
               for m_start, m_end in months]
    total_data = laporan_service.get_laba_rugi(db, date_from, date_to)

    # Universe akun per grup (union semua bulan + total) — urut kode.
    def _union(group):
        acc: dict[str, str] = {}
        for data in [*monthly, total_data]:
            for it in data[group]:
                acc.setdefault(it["kode_akun"], it["nama_akun"])
        return acc

    pendapatan, hpp = _union("pendapatan"), _union("hpp")
    beban, lain = _union("beban"), _union("pendapatan_lain")

    # Nilai per bulan (0 bila akun tak muncul di bulan itu) + persentase
    # terhadap pendapatan usaha bulan tsb (None bila pendapatan 0 — template
    # menampilkan #DIV/0!, di sini dibiarkan kosong).
    def _item_row(kode: str, nama: str, group: str):
        vals = [kode, nama]
        for data in monthly:
            item = next((it for it in data[group] if it["kode_akun"] == kode), None)
            value = _num(item["total"]) if item else 0.0
            if value is None:
                value = 0.0
            base = float(data["total_pendapatan"] or 0)
            vals += [value, (value / base) if base else None]
        tval = _num(next((it["total"] for it in total_data[group] if it["kode_akun"] == kode), 0) or 0)
        vals.append(tval)
        return vals

    def _summary_row(label: str, month_key: str, total_key: str):
        vals = ["", label]
        for data in monthly:
            value = _num(data[month_key])
            base = float(data["total_pendapatan"] or 0)
            vals += [value, (value / base) if base else None]
        vals.append(_num(total_data[total_key]))
        return vals

    rows: list[tuple[str, list]] = [("section", ["Pendapatan Usaha"])]
    for kode in sorted(pendapatan):
        rows.append(("item", _item_row(kode, pendapatan[kode], "pendapatan")))
    rows.append(("subtotal", _summary_row("Jumlah Pendapatan Usaha :", "total_pendapatan", "total_pendapatan")))

    rows.append(("section", ["Harga Pokok Penjualan"]))
    for kode in sorted(hpp):
        rows.append(("item", _item_row(kode, hpp[kode], "hpp")))
    rows.append(("subtotal", _summary_row("Jumlah Harga Pokok Penjualan :", "total_hpp", "total_hpp")))
    rows.append(("total", _summary_row("Laba (Rugi) Kotor", "laba_kotor", "laba_kotor")))

    rows.append(("section", ["Biaya Operasional"]))
    for kode in sorted(beban):
        rows.append(("item", _item_row(kode, beban[kode], "beban")))
    rows.append(("subtotal", _summary_row("Jumlah Biaya Operasional :", "total_beban", "total_beban")))
    rows.append(("total", _summary_row("Laba (Rugi) Usaha", "laba_usaha", "laba_usaha")))

    if lain:
        rows.append(("section", ["Pendapatan (Biaya) Lain-lain"]))
        for kode in sorted(lain):
            rows.append(("item", _item_row(kode, lain[kode], "pendapatan_lain")))
        rows.append(("subtotal", _summary_row("Jumlah Pendapatan (Biaya) Lain-lain :",
                                              "total_pendapatan_lain", "total_pendapatan_lain")))
    rows.append(("total", _summary_row("Laba (Rugi) Bersih", "laba_bersih", "laba_bersih")))

    headers = ["Kode Akun", "Nama Akun"]
    for _, m_end in months:
        headers += [_month_label(m_end), "%"]
    headers.append("TOTAL")
    n = len(months)
    decimal_cols = {2 + 2 * i for i in range(n)} | {2 + 2 * n}
    percent_cols = {3 + 2 * i for i in range(n)}
    wb = excel_service.report_sheet(
        headers=headers, rows=rows, sheet="Laba Rugi",
        company=laporan_service.get_company_name(db),
        title="Laporan Laba / Rugi (Rupiah)",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns=decimal_cols, percent_columns=percent_cols,
        wide_columns={1},
    )
    return _xlsx_response(wb, f"laba-rugi_{dari}_sd_{sampai}.xlsx")


@router.get("/neraca", response_model=NeracaResponse)
def get_neraca(
    tanggal: str = Query(..., description="Tanggal posisi neraca (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Neraca (posisi keuangan)."""
    try:
        dt = day_end(datetime.strptime(tanggal, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_neraca(db, dt)


# ===== EXPORT EXCEL (Update #024 — gaya workbook ASAHI/Master MAS) =====
@router.get("/neraca/export")
def export_neraca(
    tanggal: str = Query(..., description="Tanggal posisi neraca (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Neraca ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #024 — layout meniru sheet "NRC" Master MAS: seksi Aset /
    Kewajiban / Ekuitas + kolom posisi per akhir bulan Januari s.d. bulan
    tanggal (evolusi bulanan sepanjang tahun berjalan; template aslinya 13
    blok berdampingan). Angka format akuntansi (nol tampil "-"). Baris
    "Laba/rugi belum ditutup" (jurnal penutup belum diproses) tetap
    ditampilkan sebagai bagian ekuitas.
    """
    try:
        dt = day_end(datetime.strptime(tanggal, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD") from None

    target = dt.date()
    months = []
    for m in range(1, target.month + 1):
        last = calendar.monthrange(target.year, m)[1]
        months.append((date(target.year, m, 1), date(target.year, m, last)))

    monthly = [laporan_service.get_neraca(db, day_end(datetime.combine(m_end, datetime.min.time())))
               for _, m_end in months]

    # Universe akun per grup (union semua bulan) — urut kode.
    def _union(group):
        acc: dict[str, str] = {}
        for data in monthly:
            for it in data[group]:
                acc.setdefault(it["kode_akun"], it["nama_akun"])
        return acc

    aset, kewajiban, ekuitas = _union("aset"), _union("kewajiban"), _union("ekuitas")

    def _item_row(kode: str, nama: str, group: str):
        vals = ["" if kode.startswith("LABA_BELUM") else kode, nama]
        for data in monthly:
            item = next((it for it in data[group] if it["kode_akun"] == kode), None)
            vals.append(_num(item["total"]) if item else 0.0)
        return vals

    def _summary_row(label: str, key: str):
        return ["", label] + [_num(data[key]) for data in monthly]

    rows: list[tuple[str, list]] = [("section", ["Aset"])]
    for kode in sorted(aset):
        rows.append(("item", _item_row(kode, aset[kode], "aset")))
    rows.append(("total", _summary_row("Total Aset :", "total_aset")))

    rows.append(("section", ["Kewajiban"]))
    for kode in sorted(kewajiban):
        rows.append(("item", _item_row(kode, kewajiban[kode], "kewajiban")))
    rows.append(("subtotal", _summary_row("Jumlah Kewajiban :", "total_kewajiban")))

    rows.append(("section", ["Ekuitas"]))
    for kode in sorted(ekuitas):
        rows.append(("item", _item_row(kode, ekuitas[kode], "ekuitas")))
    rows.append(("subtotal", _summary_row("Jumlah Ekuitas :", "total_ekuitas")))
    # Total K+G dihitung langsung (tidak tersedia sebagai satu key di service).
    rows.append(("total", ["", "Total Kewajiban dan Ekuitas :"]
                + [_num(data["total_kewajiban"] + data["total_ekuitas"]) for data in monthly]))

    headers = ["Kode Akun", "Nama Akun"] + [_month_label(m_end) for _, m_end in months]
    n = len(months)
    wb = excel_service.report_sheet(
        headers=headers, rows=rows, sheet="Neraca",
        company=laporan_service.get_company_name(db),
        title="Neraca",
        period=f"Posisi per {_fmt_date(tanggal)} (kolom bulanan Januari - {_month_label(months[-1][1], short=False)})",
        decimal_columns=set(range(2, 2 + n)),
        wide_columns={1},
    )
    return _xlsx_response(wb, f"neraca_{tanggal}.xlsx")


@router.get("/arus-kas", response_model=ArusKasResponse)
def get_arus_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    view: str = Query("detail", description="detail = items + account_groups; summary = hanya account_groups + total (nilai tidak berubah)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Arus Kas.

    Setiap item menyertakan identitas COA akun lawan (account_code/name),
    akun kas/bank, narasi transaksi, referensi dokumen sumber, direction,
    allocation_status (EXACT / MIXED_UNALLOCATED) dan counter_accounts untuk
    jurnal mixed. Field legacy (nama, jumlah, journal_id, no_jurnal) tetap
    dikirim untuk backward compatibility.

    view=summary mengosongkan `items` (volume besar) tapi tetap menyertakan
    account_groups + total; seluruh nilai total identik dengan view=detail.
    """
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    if view not in ("detail", "summary"):
        raise HTTPException(status_code=400, detail="view harus 'detail' atau 'summary'")

    result = laporan_service.get_arus_kas(db, date_from, date_to)
    if view == "summary":
        for section in ("operasional", "investasi", "pembiayaan", "belum_diklasifikasikan"):
            result[section]["items"] = []
    return result


# ===== EXPORT EXCEL (Update #025 — gaya sheet "CP" Master MAS) =====
@router.get("/arus-kas/export")
def export_arus_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Laporan Arus Kas ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #025 — sheet "Arus Kas" meniru sheet "CP" Master MAS: kolom
    BULANAN (blok kalender penuh, maks. 14) + kolom Total (rentang penuh):
    Saldo Awal Kas → total tiap klasifikasi (Operasional / Investasi /
    Pembiayaan / Belum Diklasifikasi) → Kenaikan (Penurunan) Kas → Saldo
    Akhir Kas → Selisih Rekonsiliasi. Sheet "Rincian Transaksi" memuat
    detail transaksi tiap klasifikasi (kolom sama seperti web).
    """
    date_from, date_to = _parse_range(dari, sampai)
    months = _month_ranges(date_from, date_to)
    monthly = [laporan_service.get_arus_kas(db, *_month_bounds(m_start, m_end))
               for m_start, m_end in months]
    total_data = laporan_service.get_arus_kas(db, date_from, date_to)

    sections = (
        ("Arus Kas dari Aktivitas Operasional", "operasional"),
        ("Arus Kas dari Aktivitas Investasi", "investasi"),
        ("Arus Kas dari Aktivitas Pembiayaan", "pembiayaan"),
        ("Belum Diklasifikasi", "belum_diklasifikasikan"),
    )

    def _any_belum(mdata: dict) -> bool:
        bag = mdata["belum_diklasifikasikan"]
        return bool(bag["items"]) or bool(mdata.get("jumlah_jurnal_belum_diklasifikasi"))

    company = laporan_service.get_company_name(db)
    period = f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}"

    # ── Sheet 1: ringkasan bulanan ala sheet CP ──
    headers = ["Uraian"] + [_month_label(m_end, short=False) for _, m_end in months] + ["Total"]
    rows: list[tuple[str, list]] = [
        ("subtotal", ["Saldo Awal Kas"] + [_num(m["saldo_awal"]) for m in monthly]
         + [_num(total_data["saldo_awal"])]),
    ]
    for section_title, key in sections:
        if key == "belum_diklasifikasikan" \
                and not any(_any_belum(m) for m in [*monthly, total_data]):
            continue
        rows.append(("item", [section_title]
                     + [_num(m[key]["total"]) for m in monthly]
                     + [_num(total_data[key]["total"])]))
    rows.append(("subtotal", ["Kenaikan (Penurunan) Kas"]
                 + [_num(m["net_change"]) for m in monthly]
                 + [_num(total_data["net_change"])]))
    rows.append(("total", ["Saldo Akhir Kas"] + [_num(m["saldo_akhir"]) for m in monthly]
                 + [_num(total_data["saldo_akhir"])]))
    rows.append(("item", ["Selisih Rekonsiliasi"]
                 + [_num(m["selisih_rekonsiliasi"]) for m in monthly]
                 + [_num(total_data["selisih_rekonsiliasi"])]))
    wb = excel_service.report_sheet(
        headers=headers, rows=rows, sheet="Arus Kas",
        company=company, title="Laporan Arus Kas", period=period,
        decimal_columns=set(range(1, 2 + len(months))), wide_columns={0},
    )

    # ── Sheet 2: rincian transaksi per klasifikasi (kolom seperti web) ──
    detail_rows: list[tuple[str, list]] = []
    for section_title, key in sections:
        bagian = total_data[key]
        if key == "belum_diklasifikasikan" and not bagian["items"] \
                and not total_data["jumlah_jurnal_belum_diklasifikasi"]:
            continue
        detail_rows.append(("section", [section_title]))
        for item in bagian["items"]:
            kode, nama = item.get("account_code"), item.get("account_name") or item.get("nama")
            lawan = f"{kode} — {nama}" if kode else (nama or "Multi-akun")
            keterangan = item.get("line_description") or item.get("transaction_description") \
                or item.get("nama") or ""
            detail_rows.append(("item", [
                _as_date(item["tanggal"]) if item.get("tanggal") else None,
                item.get("no_jurnal") or "",
                lawan,
                keterangan,
                item.get("source_no") or "",
                _num(item["jumlah"]),
            ]))
        detail_rows.append(("subtotal", [f"Total {section_title}", "", "", "", "",
                                         _num(bagian["total"])]))
    excel_service.report_sheet(
        headers=["Tanggal", "No. Jurnal", "Akun Lawan", "Keterangan", "No. Referensi", "Jumlah"],
        rows=detail_rows, sheet="Rincian Transaksi", wb=wb,
        company=company, title="Laporan Arus Kas — Rincian Transaksi", period=period,
        decimal_columns={5}, date_columns={0}, wide_columns={2, 3},
    )
    return _xlsx_response(wb, f"arus-kas_{dari}_sd_{sampai}.xlsx")


# ==========================================
# UMUR PIUTANG / HUTANG
# ==========================================

@router.get("/umur-piutang", response_model=UmurPiutangResponse)
def get_umur_piutang(
    as_of: str = Query(..., description="Tanggal posisi (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Umur Piutang (Aging Receivables)."""
    try:
        dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_umur_piutang(db, dt)


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/umur-piutang/export")
def export_umur_piutang(
    as_of: str = Query(..., description="Tanggal posisi (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Umur Piutang (aging per pelanggan) ke file .xlsx."""
    try:
        dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD") from None
    data = laporan_service.get_umur_piutang(db, dt)

    rows: list[tuple[str, list]] = []
    for item in data["items"]:
        rows.append(("item", [item["nama"], _num(item["total"]), _num(item["belum_jatuh_tempo"]),
                              _num(item["umur_1_30"]), _num(item["umur_31_60"]),
                              _num(item["umur_61_90"]), _num(item["umur_91_plus"])]))
    rows.append(("total", ["Total", _num(data["total"]), _num(data["total_belum_jatuh_tempo"]),
                           _num(data["total_umur_1_30"]), _num(data["total_umur_31_60"]),
                           _num(data["total_umur_61_90"]), _num(data["total_umur_91_plus"])]))
    wb = excel_service.report_sheet(
        headers=["Pelanggan", "Total", "Belum JT", "1–30", "31–60", "61–90", ">90"],
        rows=rows, sheet="Umur Piutang",
        company=laporan_service.get_company_name(db),
        title="Umur Piutang",
        period=f"Posisi: {_fmt_date(as_of)}",
        decimal_columns={1, 2, 3, 4, 5, 6}, wide_columns={0},
    )
    return _xlsx_response(wb, f"umur-piutang_{as_of}.xlsx")


@router.get("/umur-hutang", response_model=UmurHutangResponse)
def get_umur_hutang(
    as_of: str = Query(..., description="Tanggal posisi (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Umur Hutang (Aging Payables)."""
    try:
        dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_umur_hutang(db, dt)


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/umur-hutang/export")
def export_umur_hutang(
    as_of: str = Query(..., description="Tanggal posisi (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Umur Hutang (aging per supplier) ke file .xlsx."""
    try:
        dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD") from None
    data = laporan_service.get_umur_hutang(db, dt)

    rows: list[tuple[str, list]] = []
    for item in data["items"]:
        rows.append(("item", [item["nama"], _num(item["total"]), _num(item["belum_jatuh_tempo"]),
                              _num(item["umur_1_30"]), _num(item["umur_31_60"]),
                              _num(item["umur_61_90"]), _num(item["umur_91_plus"])]))
    rows.append(("total", ["Total", _num(data["total"]), _num(data["total_belum_jatuh_tempo"]),
                           _num(data["total_umur_1_30"]), _num(data["total_umur_31_60"]),
                           _num(data["total_umur_61_90"]), _num(data["total_umur_91_plus"])]))
    wb = excel_service.report_sheet(
        headers=["Supplier", "Total", "Belum JT", "1–30", "31–60", "61–90", ">90"],
        rows=rows, sheet="Umur Hutang",
        company=laporan_service.get_company_name(db),
        title="Umur Hutang",
        period=f"Posisi: {_fmt_date(as_of)}",
        decimal_columns={1, 2, 3, 4, 5, 6}, wide_columns={0},
    )
    return _xlsx_response(wb, f"umur-hutang_{as_of}.xlsx")


# ==========================================
# BUKU BESAR
# ==========================================

@router.get("/buku-besar", response_model=BukuBesarResponse)
def get_buku_besar(
    akun_id: UUID = Query(..., description="ID Akun Perkiraan"),
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rincian Buku Besar per akun."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    try:
        return laporan_service.get_buku_besar(db, akun_id, date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ===== EXPORT EXCEL (Update #023; sheet Rekap Bulanan — Update #025) =====
@router.get("/buku-besar/export")
def export_buku_besar(
    akun_id: UUID = Query(..., description="ID Akun Perkiraan"),
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rincian Buku Besar per akun ke file .xlsx (saldo awal +
    transaksi berikut running balance + total debit/kredit + saldo akhir).

    Update #025 — sheet kedua "Rekap Bulanan": mutasi akun per bulan
    (Saldo Awal / Debet / Credit / Saldo Akhir — pola PU Master MAS),
    saldo berjalan konsisten dengan kolom Saldo sheet detail.
    """
    date_from, date_to = _parse_range(dari, sampai)
    try:
        data = laporan_service.get_buku_besar(db, akun_id, date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    akun = data["akun"]
    company = laporan_service.get_company_name(db)
    period = f"Akun: {akun['kode']} — {akun['nama']} · Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}"

    rows: list[tuple[str, list]] = [
        ("subtotal", ["Saldo Awal", "", "", None, None, _num(data["saldo_awal"])]),
    ]
    for t in data["transaksi"]:
        rows.append(("item", [_as_date(t["tanggal"]), t["no_jurnal"], t["deskripsi"],
                              _num(t["debit"]), _num(t["kredit"]), _num(t["saldo"])]))
    rows.append(("total", ["Total Debit", "", "", _num(data["total_debit"]), None, None]))
    rows.append(("total", ["Total Kredit", "", "", None, _num(data["total_kredit"]), None]))
    rows.append(("total", ["Saldo Akhir", "", "", None, None, _num(data["saldo_akhir"])]))
    wb = excel_service.report_sheet(
        headers=["Tanggal", "No. Jurnal", "Deskripsi", "Debit", "Kredit", "Saldo"],
        rows=rows, sheet="Buku Besar",
        company=company, title="Rincian Buku Besar", period=period,
        decimal_columns={3, 4, 5}, date_columns={0}, wide_columns={2},
    )

    # ── Sheet 2: rekap bulanan akun (gaya PU Master MAS — Update #025) ──
    # Agregasi per bulan dari daftar transaksi (urut tanggal ASC dari service);
    # saldo akhir bulan = saldo running transaksi TERAKHIR bulan itu.
    month_agg: dict[str, dict] = {}
    order: list[str] = []
    for t in data["transaksi"]:
        mkey = (t["tanggal"] or "")[:7]
        if not mkey:
            continue
        if mkey not in month_agg:
            month_agg[mkey] = {"debit": Decimal(0), "kredit": Decimal(0), "akhir": None}
            order.append(mkey)
        agg = month_agg[mkey]
        agg["debit"] += t["debit"] or Decimal(0)
        agg["kredit"] += t["kredit"] or Decimal(0)
        agg["akhir"] = t["saldo"]

    rec_rows: list[tuple[str, list]] = []
    saldo_jalan = data["saldo_awal"]
    for mkey in order:
        agg = month_agg[mkey]
        rec_rows.append(("item", [_month_key_label(mkey), _num(saldo_jalan),
                                   _num(agg["debit"]), _num(agg["kredit"]), _num(agg["akhir"])]))
        saldo_jalan = agg["akhir"]
    rec_rows.append(("total", ["TOTAL", _num(data["saldo_awal"]), _num(data["total_debit"]),
                               _num(data["total_kredit"]), _num(data["saldo_akhir"])]))
    excel_service.report_sheet(
        headers=["Bulan", "Saldo Awal", "Debet", "Credit", "Saldo Akhir"],
        rows=rec_rows, sheet="Rekap Bulanan", wb=wb,
        company=company, title="Rincian Buku Besar — Rekap Bulanan", period=period,
        decimal_columns={1, 2, 3, 4},
    )
    return _xlsx_response(wb, f"buku-besar_{_slug(akun['kode'])}_{dari}_sd_{sampai}.xlsx")


# ==========================================
# KAS & BANK
# ==========================================

@router.get("/mutasi-kas", response_model=MutasiKasBankResponse)
def get_mutasi_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Mutasi Kas."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="KAS")


# ===== EXPORT EXCEL (Update #025 — gaya PU/HU Master MAS) =====
def _mutasi_rows(data: dict) -> list[tuple[str, list]]:
    """Baris export mutasi kas/bank: seksi per akun kas + baris transaksi
    + subtotal saldo akhir tiap akun (saldo running per akun dari service)."""
    by_akun: dict[str, list] = {}
    for t in data["transaksi"]:
        by_akun.setdefault(t["akun"], []).append(t)
    rows: list[tuple[str, list]] = []
    for nama_akun, trans in by_akun.items():
        rows.append(("section", [nama_akun]))
        for t in trans:
            rows.append(("item", [_as_date(t["tanggal"]), t["no_jurnal"], t["akun"], t["deskripsi"],
                                  _num(t["debit"]), _num(t["kredit"]), _num(t["saldo"])]))
        rows.append(("subtotal", ["Saldo Akhir", "", "", "", "", None, _num(trans[-1]["saldo"])]))
    return rows


def _mutasi_monthly_sheet(wb, db, months, jenis: str, title: str, period: str, company: str):
    """Sheet "Rekap Bulanan" mutasi kas/bank — blok bulanan per akun
    (Saldo Awal / Debet / Credit / Saldo Akhir), pola sheet PU/HU Master MAS
    (Update #025). Baris TOTAL menjumlahkan semua akun per bulan."""
    accounts, monthly = _kas_bank_monthly(db, months, jenis=jenis)

    rows: list[tuple[str, list]] = []
    for kode in sorted(accounts):
        info = accounts[kode]
        vals = [kode, info["nama"]]
        for rows_m in monthly:
            a = rows_m.get(kode)
            if a is None:
                vals += [None] * 4
                continue
            vals += [_num(a["saldo_awal"]), _num(a["total_masuk"]),
                     _num(a["total_keluar"]), _num(a["saldo_akhir"])]
        rows.append(("item", vals))

    vals = ["", "TOTAL"]
    for rows_m in monthly:
        vals += [
            _num(sum((a["saldo_awal"] for a in rows_m.values()), Decimal(0))),
            _num(sum((a["total_masuk"] for a in rows_m.values()), Decimal(0))),
            _num(sum((a["total_keluar"] for a in rows_m.values()), Decimal(0))),
            _num(sum((a["saldo_akhir"] for a in rows_m.values()), Decimal(0))),
        ]
    rows.append(("total", vals))

    blocks = [(_month_label(m_end, short=False),
               ["Saldo Awal", "Debet", "Credit", "Saldo Akhir"])
              for _, m_end in months]
    excel_service.block_report_sheet(
        left_headers=["Kode Akun", "Nama Akun"],
        blocks=blocks, rows=rows, sheet="Rekap Bulanan", wb=wb,
        company=company, title=title, period=period,
        decimal_columns=set(range(2, 2 + 4 * len(months))),
        wide_columns={1},
    )


@router.get("/mutasi-kas/export")
def export_mutasi_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Mutasi Kas ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #025 — 2 sheet: "Mutasi Kas" (detail transaksi per akun, kolom
    seperti web) + "Rekap Bulanan" (blok bulanan per akun: Saldo Awal /
    Debet / Credit / Saldo Akhir — pola sheet PU/HU Master MAS, maks. 14
    bulan per export).
    """
    date_from, date_to = _parse_range(dari, sampai)
    months = _month_ranges(date_from, date_to)
    data = laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="KAS")

    company = laporan_service.get_company_name(db)
    period = f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}"
    wb = excel_service.report_sheet(
        headers=["Tanggal", "No. Jurnal", "Akun", "Deskripsi", "Debit", "Kredit", "Saldo"],
        rows=_mutasi_rows(data), sheet="Mutasi Kas",
        company=company, title="Mutasi Kas", period=period,
        decimal_columns={4, 5, 6}, date_columns={0}, wide_columns={3},
    )
    _mutasi_monthly_sheet(wb, db, months, "KAS", "Mutasi Kas — Rekap Bulanan", period, company)
    return _xlsx_response(wb, f"mutasi-kas_{dari}_sd_{sampai}.xlsx")


@router.get("/mutasi-bank", response_model=MutasiKasBankResponse)
def get_mutasi_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Mutasi Bank."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="BANK")


# ===== EXPORT EXCEL (Update #025 — gaya PU/HU Master MAS) =====
@router.get("/mutasi-bank/export")
def export_mutasi_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Mutasi Bank ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #025 — 2 sheet: "Mutasi Bank" (detail transaksi per akun, kolom
    seperti web) + "Rekap Bulanan" (blok bulanan per akun: Saldo Awal /
    Debet / Credit / Saldo Akhir — pola sheet PU/HU Master MAS, maks. 14
    bulan per export).
    """
    date_from, date_to = _parse_range(dari, sampai)
    months = _month_ranges(date_from, date_to)
    data = laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="BANK")

    company = laporan_service.get_company_name(db)
    period = f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}"
    wb = excel_service.report_sheet(
        headers=["Tanggal", "No. Jurnal", "Akun", "Deskripsi", "Debit", "Kredit", "Saldo"],
        rows=_mutasi_rows(data), sheet="Mutasi Bank",
        company=company, title="Mutasi Bank", period=period,
        decimal_columns={4, 5, 6}, date_columns={0}, wide_columns={3},
    )
    _mutasi_monthly_sheet(wb, db, months, "BANK", "Mutasi Bank — Rekap Bulanan", period, company)
    return _xlsx_response(wb, f"mutasi-bank_{dari}_sd_{sampai}.xlsx")


@router.get("/rekap-kas-bank", response_model=RekapKasBankResponse)
def get_rekap_kas_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekap Kas & Bank (saldo awal, masuk, keluar, akhir per akun)."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_rekap_kas_bank(db, date_from, date_to)


# ===== EXPORT EXCEL (Update #025 — gaya PU/HU Master MAS) =====
@router.get("/rekap-kas-bank/export")
def export_rekap_kas_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekap Kas & Bank ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #025 — layout blok bulanan pola PU/HU Master MAS: kolom
    Kode/Nama/Jenis + BLOK BULANAN (maks. 14) masing-masing 4 sub-kolom
    (Saldo Awal / Masuk / Keluar / Saldo Akhir) + blok TOTAL (Periode) —
    angka rentang penuh sama seperti web — + baris TOTAL per bulan.
    """
    date_from, date_to = _parse_range(dari, sampai)
    months = _month_ranges(date_from, date_to)
    accounts, monthly = _kas_bank_monthly(db, months, jenis=None)
    total_data = laporan_service.get_rekap_kas_bank(db, date_from, date_to)
    total_by_kode = {a["kode"]: a for a in total_data["akun"]}

    rows: list[tuple[str, list]] = []
    for kode in sorted(accounts):
        info = accounts[kode]
        vals = [kode, info["nama"], info["jenis"]]
        for rows_m in monthly:
            a = rows_m.get(kode)
            if a is None:
                vals += [None] * 4
                continue
            vals += [_num(a["saldo_awal"]), _num(a["total_masuk"]),
                     _num(a["total_keluar"]), _num(a["saldo_akhir"])]
        t = total_by_kode.get(kode)
        vals += ([_num(t["saldo_awal"]), _num(t["total_masuk"]),
                  _num(t["total_keluar"]), _num(t["saldo_akhir"])] if t else [None] * 4)
        rows.append(("item", vals))

    # Baris TOTAL: jumlah semua akun per bulan + blok TOTAL (rentang penuh).
    vals = ["", "TOTAL", ""]
    for rows_m in monthly:
        vals += [
            _num(sum((a["saldo_awal"] for a in rows_m.values()), Decimal(0))),
            _num(sum((a["total_masuk"] for a in rows_m.values()), Decimal(0))),
            _num(sum((a["total_keluar"] for a in rows_m.values()), Decimal(0))),
            _num(sum((a["saldo_akhir"] for a in rows_m.values()), Decimal(0))),
        ]
    akun_list = total_data["akun"]
    vals += [
        _num(sum((a["saldo_awal"] for a in akun_list), Decimal(0))),
        _num(sum((a["total_masuk"] for a in akun_list), Decimal(0))),
        _num(sum((a["total_keluar"] for a in akun_list), Decimal(0))),
        _num(sum((a["saldo_akhir"] for a in akun_list), Decimal(0))),
    ]
    rows.append(("total", vals))

    sub = ["Saldo Awal", "Masuk", "Keluar", "Saldo Akhir"]
    blocks = [(_month_label(m_end, short=False), sub) for _, m_end in months]
    blocks.append(("TOTAL (Periode)", sub))
    n_num = 4 * (len(months) + 1)
    wb = excel_service.block_report_sheet(
        left_headers=["Kode Akun", "Nama Akun", "Jenis"],
        blocks=blocks, rows=rows, sheet="Rekap Kas & Bank",
        company=laporan_service.get_company_name(db),
        title="Rekap Kas & Bank",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns=set(range(3, 3 + n_num)),
        wide_columns={1},
    )
    return _xlsx_response(wb, f"rekap-kas-bank_{dari}_sd_{sampai}.xlsx")


@router.get('/validasi', response_model=ReportValidationResponse)
def validate_financial_reports(dari: date, sampai: date, db=Depends(get_current_db)):
    if dari > sampai:
        raise HTTPException(400, 'Tanggal dari tidak boleh setelah sampai')
    return laporan_service.validate_reports(db, day_start(dari), day_end(sampai))


# ==========================================
# Tahap 3 — Rekonsiliasi Persediaan vs Buku Besar
# ==========================================

@router.get("/rekonsiliasi-persediaan", response_model=RekonsiliasiPersediaanResponse)
def get_rekonsiliasi_persediaan(
    as_of: Optional[str] = Query(None, description="Tanggal hari ini (YYYY-MM-DD); tanggal historis belum tersedia. Saldo terkini mencakup semua jurnal POSTED."),
    only_mismatch: bool = Query(False, description="Hanya tampilkan akun/barang yang selisihnya != 0."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekonsiliasi saldo stok terkini (seluruh gudang) dan seluruh jurnal POSTED.

    Termasuk jurnal bertanggal masa depan yang telah diposting. Tanggal historis
    ditolak karena stok belum memiliki snapshot tersebut.
    Mapping barang diprioritaskan; fallback kategori mengikuti resolver posting.
    """
    from datetime import datetime
    as_of_dt = None
    if as_of:
        try:
            as_of_dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")
    return rekonsiliasi_persediaan_service.get_rekonsiliasi_persediaan(
        db, as_of=as_of_dt, only_mismatch=only_mismatch
    )


@router.get("/rekonsiliasi-persediaan/ringkasan", response_model=RekonsiliasiPersediaanRingkasanResponse)
def get_ringkasan_rekonsiliasi_persediaan(
    as_of: Optional[str] = Query(None, description="Tanggal hari ini (YYYY-MM-DD); tanggal historis belum tersedia. Saldo terkini mencakup semua jurnal POSTED."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ringkasan cepat rekonsiliasi persediaan (untuk dashboard / alerting)."""
    from datetime import datetime
    as_of_dt = None
    if as_of:
        try:
            as_of_dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")
    return rekonsiliasi_persediaan_service.get_ringkasan_rekonsiliasi(db, as_of=as_of_dt)


# ===== Update #027 — export Excel Laporan Lainnya (rekonsiliasi & health) =====
@router.get("/rekonsiliasi-persediaan/export")
def export_rekonsiliasi_persediaan(
    as_of: Optional[str] = Query(None, description="Tanggal posisi (YYYY-MM-DD); default: hari ini. Saldo terkini mencakup semua jurnal POSTED."),
    only_mismatch: bool = Query(False, description="Hanya baris yang selisihnya != 0."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekonsiliasi Persediaan (stok vs buku besar) ke .xlsx — Update #027.

    Parameter IDENTIK dengan GET /laporan/rekonsiliasi-persediaan. Payload
    service (dict sembarang) dirender generic_report_sheet: seksi RINGKASAN
    (skalar + subtotal) + satu tabel per list (akun/barang persediaan).
    """
    as_of_dt = None
    if as_of:
        try:
            as_of_dt = day_end(datetime.strptime(as_of, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")
    payload = rekonsiliasi_persediaan_service.get_rekonsiliasi_persediaan(
        db, as_of=as_of_dt, only_mismatch=only_mismatch
    )
    period = f"Posisi: {_fmt_date(as_of)}" if as_of else "Posisi: hari ini (saldo terkini)"
    if only_mismatch:
        period += " · hanya selisih ≠ 0"
    wb = excel_service.generic_report_sheet(
        payload, sheet="Rekonsiliasi Persediaan",
        company=laporan_service.get_company_name(db),
        title="Rekonsiliasi Persediaan (Stok vs Buku Besar)", period=period,
    )
    now = datetime.now()
    return _xlsx_response(wb, f"rekonsiliasi-persediaan_{now:%Y%m%d}_{now:%H%M}.xlsx")


@router.get("/audit-persediaan", response_model=AuditTransaksiPersediaanResponse)
def get_audit_persediaan(
    dari: Optional[str] = Query(None, description="Tanggal awal (YYYY-MM-DD). Default: 2000-01-01."),
    sampai: Optional[str] = Query(None, description="Tanggal akhir (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Audit trail transaksi persediaan: penerimaan, pengiriman, retur, penyesuaian.

    Verifikasi bahwa setiap transaksi yang mengubah stok juga menghasilkan
    jurnal Persediaan yang sesuai. Anomali = transaksi dengan nilai > 0
    tapi tidak ada jurnal (cek auto_post_jurnal atau error posting).
    """
    from datetime import datetime
    date_from = None
    date_to = None
    if dari:
        try:
            date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format dari harus YYYY-MM-DD")
    if sampai:
        try:
            date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format sampai harus YYYY-MM-DD")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    return rekonsiliasi_persediaan_service.audit_transaksi_persediaan(
        db, date_from=date_from, date_to=date_to
    )


@router.get("/audit-persediaan/export")
def export_audit_persediaan(
    dari: Optional[str] = Query(None, description="Tanggal awal (YYYY-MM-DD). Default: 2000-01-01."),
    sampai: Optional[str] = Query(None, description="Tanggal akhir (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Audit Transaksi Persediaan ke .xlsx — Update #027.

    Parameter IDENTIK dengan GET /laporan/audit-persediaan (rentang bebas,
    boleh beberapa bulan/tahun — TANPA cap bulanan karena bukan blok bulanan).
    """
    date_from = None
    date_to = None
    if dari:
        try:
            date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format dari harus YYYY-MM-DD")
    if sampai:
        try:
            date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        except ValueError:
            raise HTTPException(400, "Format sampai harus YYYY-MM-DD")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    payload = rekonsiliasi_persediaan_service.audit_transaksi_persediaan(
        db, date_from=date_from, date_to=date_to
    )
    if dari and sampai:
        period = f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}"
    elif dari:
        period = f"Periode: mulai {_fmt_date(dari)}"
    elif sampai:
        period = f"Periode: s.d. {_fmt_date(sampai)}"
    else:
        period = "Semua Periode"
    wb = excel_service.generic_report_sheet(
        payload, sheet="Audit Persediaan",
        company=laporan_service.get_company_name(db),
        title="Audit Transaksi Persediaan", period=period,
    )
    now = datetime.now()
    return _xlsx_response(wb, f"audit-persediaan_{now:%Y%m%d}_{now:%H%M}.xlsx")


# ==========================================
# PHASE 9 — FINANCIAL RECONCILIATION & ACCOUNTING HEALTH
# ==========================================

@router.get("/rekonsiliasi/grni")
def get_grni_reconciliation_endpoint(
    as_of: Optional[str] = Query(None, description="Tanggal as-of (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekonsiliasi Open GRNI vs GRNI GL (Master Roadmap §26).

    Membandingkan:
    - Open GRNI = sum penerimaan barang SELESAI yang belum di-invoice
    - GRNI GL = saldo akun PENERIMAAN_DALAM_PROSES dari POSTED journals

    Selisih harus 0 (tolerance 1 cent).
    """
    from app.services.financial_reconciliation_service import get_grni_reconciliation
    from datetime import datetime

    as_of_dt = None
    if as_of:
        try:
            as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")

    return get_grni_reconciliation(db, as_of_dt)


@router.get("/rekonsiliasi/grni/export")
def export_grni_reconciliation(
    as_of: Optional[str] = Query(None, description="Tanggal as-of (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekonsiliasi Open GRNI vs GRNI GL ke .xlsx — Update #027."""
    from app.services.financial_reconciliation_service import get_grni_reconciliation
    from datetime import datetime as _dt

    as_of_dt = None
    if as_of:
        try:
            as_of_dt = _dt.strptime(as_of, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")

    payload = get_grni_reconciliation(db, as_of_dt)
    period = f"Posisi: {_fmt_date(as_of)}" if as_of else "Posisi: hari ini"
    wb = excel_service.generic_report_sheet(
        payload, sheet="GRNI vs GL",
        company=laporan_service.get_company_name(db),
        title="Rekonsiliasi Open GRNI vs GRNI GL", period=period,
    )
    now = datetime.now()
    return _xlsx_response(wb, f"rekonsiliasi-grni_{now:%Y%m%d}_{now:%H%M}.xlsx")


@router.get("/rekonsiliasi/cashflow-vs-bs")
def get_cashflow_vs_bs_reconciliation_endpoint(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekonsiliasi Cash Flow Ending Cash vs Balance Sheet Cash (Master Roadmap §26).

    Membandingkan:
    - Cash Flow Ending = saldo akhir kas/bank dari laporan arus kas
    - Balance Sheet Cash = total saldo akun kas/bank di neraca per tanggal akhir

    Selisih harus 0.
    """
    from app.services.financial_reconciliation_service import (
        get_cash_flow_vs_balance_sheet_reconciliation,
    )
    from datetime import datetime

    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(400, "Format tanggal harus YYYY-MM-DD")

    return get_cash_flow_vs_balance_sheet_reconciliation(db, date_from, date_to)


@router.get("/rekonsiliasi/cashflow-vs-bs/export")
def export_cashflow_vs_bs_reconciliation(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekonsiliasi Cash Flow vs Neraca ke .xlsx — Update #027."""
    from app.services.financial_reconciliation_service import (
        get_cash_flow_vs_balance_sheet_reconciliation,
    )

    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(400, "Format tanggal harus YYYY-MM-DD")

    payload = get_cash_flow_vs_balance_sheet_reconciliation(db, date_from, date_to)
    wb = excel_service.generic_report_sheet(
        payload, sheet="Cash Flow vs Neraca",
        company=laporan_service.get_company_name(db),
        title="Rekonsiliasi Cash Flow vs Neraca (Kas)",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
    )
    now = datetime.now()
    return _xlsx_response(wb, f"rekonsiliasi-cashflow-vs-bs_{now:%Y%m%d}_{now:%H%M}.xlsx")


@router.get("/rekonsiliasi/equity-vs-bs")
def get_equity_vs_bs_reconciliation_endpoint(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekonsiliasi Changes in Equity Closing vs Balance Sheet Equity (Master Roadmap §26).

    Membandingkan:
    - Equity Closing = total modal akhir dari laporan perubahan modal
    - Balance Sheet Equity = total ekuitas di neraca per tanggal akhir

    Selisih harus 0.
    """
    from app.services.financial_reconciliation_service import (
        get_equity_vs_balance_sheet_reconciliation,
    )
    from datetime import datetime

    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(400, "Format tanggal harus YYYY-MM-DD")

    return get_equity_vs_balance_sheet_reconciliation(db, date_from, date_to)


@router.get("/rekonsiliasi/equity-vs-bs/export")
def export_equity_vs_bs_reconciliation(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekonsiliasi Ekuitas vs Neraca ke .xlsx — Update #027."""
    from app.services.financial_reconciliation_service import (
        get_equity_vs_balance_sheet_reconciliation,
    )

    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(400, "Format tanggal harus YYYY-MM-DD")

    payload = get_equity_vs_balance_sheet_reconciliation(db, date_from, date_to)
    wb = excel_service.generic_report_sheet(
        payload, sheet="Ekuitas vs Neraca",
        company=laporan_service.get_company_name(db),
        title="Rekonsiliasi Ekuitas vs Neraca",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
    )
    now = datetime.now()
    return _xlsx_response(wb, f"rekonsiliasi-equity-vs-bs_{now:%Y%m%d}_{now:%H%M}.xlsx")


@router.get("/accounting-health")
def get_accounting_health_endpoint(
    as_of: Optional[str] = Query(None, description="Tanggal as-of (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Accounting Health Dashboard — aggregate semua reconciliation status (Master Roadmap §27).

    Menampilkan status MATCH/MISMATCH untuk 11 reconciliation checks:
    1. Trial Balance (Debit = Credit)
    2. Balance Sheet (Assets = Liabilities + Equity)
    3. AR Aging = AR GL
    4. AP Aging = AP GL
    5. Inventory Valuation = Inventory GL
    6. Fixed Asset Register = FA GL
    7. Cash/Bank Ledger = Cash/Bank GL
    8. Open GRNI = GRNI GL
    9. Cash Flow Ending = Balance Sheet Cash
    10. Changes in Equity Closing = Balance Sheet Equity
    11. Cash Flow Movement (Opening + Change - Ending = 0)

    Response (camelCase — kontrak frontend reports/accounting-health.tsx):
    {
        "asOf": "2026-09-15T...",
        "overallStatus": "HEALTHY" | "ISSUES_FOUND",
        "reconciliations": [
            {"name": "Trial Balance", "status": "MATCH", "selisih": "0.00", "detail": "..."},
            ...
        ],
        "summary": {
            "totalChecks": 11,
            "matchCount": 10,
            "mismatchCount": 1,
            "notConfiguredCount": 0
        }
    }
    """
    from app.services.accounting_health_service import get_accounting_health
    from datetime import datetime

    as_of_dt = None
    if as_of:
        try:
            as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")

    return get_accounting_health(db, as_of_dt)


@router.get("/accounting-health/export")
def export_accounting_health(
    as_of: Optional[str] = Query(None, description="Tanggal as-of (YYYY-MM-DD). Default: hari ini."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Accounting Health Dashboard (11 reconciliation checks) ke .xlsx — Update #027.

    Payload camelCase (kontrak frontend) dirender generik: RINGKASAN
    (asOf/overallStatus/summary.*) + tabel per pemeriksaan (name/status/
    selisih/detail).
    """
    from app.services.accounting_health_service import get_accounting_health
    from datetime import datetime as _dt

    as_of_dt = None
    if as_of:
        try:
            as_of_dt = _dt.strptime(as_of, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Format as_of harus YYYY-MM-DD")

    payload = get_accounting_health(db, as_of_dt)
    period = f"Posisi: {_fmt_date(as_of)}" if as_of else "Posisi: hari ini"
    wb = excel_service.generic_report_sheet(
        payload, sheet="Accounting Health",
        company=laporan_service.get_company_name(db),
        title="Accounting Health — Status Rekonsiliasi", period=period,
    )
    now = datetime.now()
    return _xlsx_response(wb, f"accounting-health_{now:%Y%m%d}_{now:%H%M}.xlsx")


# ==========================================
# Update AUD-17 — Rekap Pajak PPN & PPh 23
# ==========================================

@router.get("/rekap-ppn", response_model=RekapPpnResponse)
def get_rekap_ppn(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekap PPN per masa pajak: DPP & PPN Keluaran (SI), Masukan (PI),
    koreksi retur — dokumen dibatalkan otomatis dikecualikan (reversal-aware)."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")
    return laporan_service.get_rekap_ppn(db, date_from, date_to)


# ===== EXPORT EXCEL (Update #025 — gaya pivot TrxPPN Master MAS) =====
@router.get("/rekap-ppn/export")
def export_rekap_ppn(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekap PPN ke .xlsx bergaya workbook pembukuan ASAHI.

    Update #025 — sheet "Rekap PPN" meniru pivot TrxPPN Master MAS: baris
    uraian (Faktur Keluaran / Retur Keluaran / Faktur Masukan / Retur
    Masukan + jumlah neto per sisi + PPN Neto) × BLOK MASA PAJAK sub-kolom
    [DPP, PPN] + blok TOTAL. Baris PPN Neto melipat koreksi retur
    (Keluaran Neto - Masukan Neto). Sheet "Rincian" tetap per dokumen
    (kolom sama seperti web).
    """
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_rekap_ppn(db, date_from, date_to)

    company = laporan_service.get_company_name(db)
    period = f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}"

    masa_list = [m["masa"] for m in data["ringkasan_masa"]]
    lookups = {m["masa"]: m for m in data["ringkasan_masa"]}
    t = data["total"]

    def _row(label: str, dpp_key: str, ppn_key: str, kind: str = "item"):
        vals = [label]
        for masa in masa_list:
            m = lookups[masa]
            vals += [_num(m[dpp_key]), _num(m[ppn_key])]
        vals += [_num(t[dpp_key]), _num(t[ppn_key])]
        return (kind, vals)

    def _neto_row(label: str, ppn_key: str, retur_key: str, kind: str = "subtotal"):
        vals = [label]
        for masa in masa_list:
            m = lookups[masa]
            vals += [None, _num(m[ppn_key] + m[retur_key])]
        vals += [None, _num(t[ppn_key] + t[retur_key])]
        return (kind, vals)

    def _ppn_neto_row():
        vals = ["PPN Neto (Kurang/(Lebih) Bayar)"]
        for masa in masa_list:
            m = lookups[masa]
            neto = (m["ppn_keluaran"] + m["ppn_retur_keluaran"]) \
                - (m["ppn_masukan"] + m["ppn_retur_masukan"])
            vals += [None, _num(neto)]
        neto = (t["ppn_keluaran"] + t["ppn_retur_keluaran"]) \
            - (t["ppn_masukan"] + t["ppn_retur_masukan"])
        vals += [None, _num(neto)]
        return ("total", vals)

    rows: list[tuple[str, list]] = [
        ("section", ["Faktur Keluaran (Penjualan)"]),
        _row("Faktur Keluaran", "dpp_keluaran", "ppn_keluaran"),
        _row("Retur Keluaran", "dpp_retur_keluaran", "ppn_retur_keluaran"),
        _neto_row("Jumlah PPN Keluaran (Neto)", "ppn_keluaran", "ppn_retur_keluaran"),
        ("section", ["Faktur Masukan (Pembelian)"]),
        _row("Faktur Masukan", "dpp_masukan", "ppn_masukan"),
        _row("Retur Masukan", "dpp_retur_masukan", "ppn_retur_masukan"),
        _neto_row("Jumlah PPN Masukan (Neto)", "ppn_masukan", "ppn_retur_masukan"),
        _ppn_neto_row(),
    ]
    blocks = [(_month_key_label(masa), ["DPP", "PPN"]) for masa in masa_list]
    blocks.append(("TOTAL", ["DPP", "PPN"]))
    n_num = 2 * (len(masa_list) + 1)
    wb = excel_service.block_report_sheet(
        left_headers=["Uraian"],
        blocks=blocks, rows=rows, sheet="Rekap PPN",
        company=company, title="Rekap PPN (Masa Pajak)", period=period,
        decimal_columns=set(range(1, 1 + n_num)),
        wide_columns={0},
    )

    jenis_label = {"KELUARAN": "Keluaran", "MASUKAN": "Masukan",
                   "RETUR_KELUARAN": "Retur Keluaran", "RETUR_MASUKAN": "Retur Masukan"}
    rincian_rows: list[tuple[str, list]] = []
    for r in data["rincian"]:
        rincian_rows.append(("item", [r["tanggal"], r["no_dokumen"], r["pihak"] or "",
                                    jenis_label.get(r["jenis"], r["jenis"]),
                                    _num(r["dpp"]), _num(r["ppn"])]))
    excel_service.report_sheet(
        headers=["Tanggal", "No Dokumen", "Pihak", "Jenis", "DPP", "PPN"],
        rows=rincian_rows, sheet="Rincian", wb=wb,
        company=company, title="Rekap PPN (Masa Pajak) — Rincian", period=period,
        decimal_columns={4, 5}, date_columns={0}, wide_columns={2},
    )
    return _xlsx_response(wb, f"rekap-ppn_{dari}_sd_{sampai}.xlsx")


@router.get("/rekap-pph23", response_model=RekapPph23Response)
def get_rekap_pph23(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekap PPh 23 per pihak: dipotong pelanggan dari kita (Dibayar di Muka)
    vs dipotong kita dari supplier (Hutang PPh 23) — sumber pelunasan,
    reversal-aware."""
    try:
        date_from = day_start(datetime.strptime(dari, "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(sampai, "%Y-%m-%d"))
        if date_from > date_to:
            raise HTTPException(400, "Tanggal dari tidak boleh setelah sampai")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")
    return laporan_service.get_rekap_pph23(db, date_from, date_to)


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/rekap-pph23/export")
def export_rekap_pph23(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekap PPh 23 ke file .xlsx — 2 sheet: rekap per pihak (+ dua
    baris total per sisi AR/AP) dan rincian pemotongan (kolom sama seperti web)."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_rekap_pph23(db, date_from, date_to)

    company = laporan_service.get_company_name(db)
    period = f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}"

    sisi_label = {"KITA_POTONG": "Kita Potong (AP)", "DIPOTONG_DARI_KITA": "Dipotong dari Kita (AR)"}
    pihak_rows: list[tuple[str, list]] = []
    for p in data["per_pihak"]:
        pihak_rows.append(("item", [p["pihak"] or "(tanpa nama)", p["npwp"] or "",
                                     sisi_label.get(p["sisi"], p["sisi"]),
                                     _num(p["total_dasar"]), _num(p["total_pph23"])]))
    pihak_rows.append(("total", ["Total PPh 23 Dipotong dari Kita (AR)", "", "", None,
                                  _num(data["total_dipotong_dari_kita"])]))
    pihak_rows.append(("total", ["Total PPh 23 Kita Potong (AP)", "", "", None,
                                  _num(data["total_kita_potong"])]))
    wb = excel_service.report_sheet(
        headers=["Pihak", "NPWP", "Sisi", "Total Dasar", "Total PPh 23"],
        rows=pihak_rows, sheet="Rekap PPh 23",
        company=company, title="Rekap PPh 23", period=period,
        decimal_columns={3, 4}, wide_columns={0},
    )

    rincian_rows: list[tuple[str, list]] = []
    for r in data["rincian"]:
        rincian_rows.append(("item", [r["tanggal"], r["pihak"] or "", r["no_bukti"],
                                    r["no_invoice"] or "", r["sumber"],
                                    _num(r["dasar"]), _num(r["pph23"])]))
    excel_service.report_sheet(
        headers=["Tanggal", "Pihak", "No Bukti", "No Invoice", "Sumber", "Dasar", "PPh 23"],
        rows=rincian_rows, sheet="Rincian", wb=wb,
        company=company, title="Rekap PPh 23 — Rincian", period=period,
        decimal_columns={5, 6}, date_columns={0}, wide_columns={1},
    )
    return _xlsx_response(wb, f"rekap-pph23_{dari}_sd_{sampai}.xlsx")
