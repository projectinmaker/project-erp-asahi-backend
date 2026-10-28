from datetime import datetime, date
from decimal import Decimal
from typing import Optional

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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/neraca-saldo/export")
def export_neraca_saldo(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Neraca Saldo ke file .xlsx (kolom sama seperti tampilan web)."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_neraca_saldo(db, date_from, date_to)

    rows: list[tuple[str, list]] = []
    for idx, akun in enumerate(data["akun"], start=1):
        rows.append(("item", [idx, akun["kode_akun"], akun["nama_akun"], akun["saldo_normal"],
                              _num(akun["total_debit"]), _num(akun["total_kredit"]), _num(akun["saldo"])]))
    rows.append(("total", ["Grand Total", "", "", None,
                           _num(data["total_debit"]), _num(data["total_kredit"]),
                           _num(data["total_debit"] - data["total_kredit"])]))
    wb = excel_service.report_sheet(
        headers=["No", "Kode Akun", "Nama Akun", "Normal", "Debit", "Kredit", "Saldo"],
        rows=rows, sheet="Neraca Saldo",
        company=laporan_service.get_company_name(db),
        title="Neraca Saldo",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns={4, 5, 6}, wide_columns={2},
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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/perubahan-modal/export")
def export_perubahan_modal(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Laporan Perubahan Modal ke file .xlsx (kolom sama seperti web)."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_perubahan_modal(db, date_from, date_to)

    rows: list[tuple[str, list]] = []
    for idx, akun in enumerate(data["akun_modal"], start=1):
        rows.append(("item", [idx, akun["kode_akun"], akun["nama_akun"], _num(akun["saldo_awal"]),
                              _num(akun["mutasi_debit"]), _num(akun["mutasi_kredit"]),
                              _num(akun["perubahan"]), _num(akun["saldo_akhir"])]))
    # Baris laba/rugi berjalan: label di kolom Nama Akun, nilai di kolom Perubahan.
    rows.append(("subtotal", ["", "", "Laba/(Rugi) Berjalan", None, None, None,
                              _num(data["laba_rugi_berjalan"]), None]))
    rows.append(("total", ["", "", "Total Modal Akhir", None, None, None, None,
                           _num(data["total_modal_akhir"])]))
    wb = excel_service.report_sheet(
        headers=["No", "Kode Akun", "Nama Akun", "Saldo Awal", "Mutasi D", "Mutasi K", "Perubahan", "Saldo Akhir"],
        rows=rows, sheet="Perubahan Modal",
        company=laporan_service.get_company_name(db),
        title="Perubahan Modal",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns={3, 4, 5, 6, 7}, wide_columns={2},
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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/laba-rugi/export")
def export_laba_rugi(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Laporan Laba Rugi ke file .xlsx (seksi + total sama seperti web)."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_laba_rugi(db, date_from, date_to)

    def _items(items):
        return [("item", [idx, it["kode_akun"], it["nama_akun"], _num(it["total"])])
                for idx, it in enumerate(items, start=1)]

    rows: list[tuple[str, list]] = [("section", ["Pendapatan"])]
    rows += _items(data["pendapatan"])
    rows.append(("total", ["", "", "Total Pendapatan", _num(data["total_pendapatan"])]))
    rows.append(("section", ["Harga Pokok Penjualan"]))
    rows += _items(data["hpp"])
    rows.append(("total", ["", "", "Total HPP", _num(data["total_hpp"])]))
    rows.append(("total", ["", "", "Laba Kotor", _num(data["laba_kotor"])]))
    rows.append(("section", ["Beban Operasional"]))
    rows += _items(data["beban"])
    rows.append(("total", ["", "", "Total Beban", _num(data["total_beban"])]))
    rows.append(("total", ["", "", "Laba Usaha", _num(data["laba_usaha"])]))
    if data["pendapatan_lain"]:
        rows.append(("section", ["Pendapatan Lain-lain"]))
        rows += _items(data["pendapatan_lain"])
        rows.append(("total", ["", "", "Total Pendapatan Lain-lain", _num(data["total_pendapatan_lain"])]))
    rows.append(("total", ["", "", "Laba (Rugi) Bersih", _num(data["laba_bersih"])]))
    wb = excel_service.report_sheet(
        headers=["No", "Kode Akun", "Nama Akun", "Jumlah"],
        rows=rows, sheet="Laba Rugi",
        company=laporan_service.get_company_name(db),
        title="Laporan Laba / Rugi (Rupiah)",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns={3}, wide_columns={2},
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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/neraca/export")
def export_neraca(
    tanggal: str = Query(..., description="Tanggal posisi neraca (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Neraca (posisi keuangan) ke file .xlsx (kolom sama seperti web)."""
    try:
        dt = day_end(datetime.strptime(tanggal, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD") from None
    data = laporan_service.get_neraca(db, dt)

    def _items(items):
        return [("item", [idx, it["kode_akun"], it["nama_akun"], _num(it["total"])])
                for idx, it in enumerate(items, start=1)]

    rows: list[tuple[str, list]] = [("section", ["Aset"])]
    rows += _items(data["aset"])
    rows.append(("total", ["", "", "Total Aset", _num(data["total_aset"])]))
    rows.append(("section", ["Kewajiban"]))
    rows += _items(data["kewajiban"])
    rows.append(("total", ["", "", "Total Kewajiban", _num(data["total_kewajiban"])]))
    rows.append(("section", ["Ekuitas"]))
    rows += _items(data["ekuitas"])
    rows.append(("total", ["", "", "Total Ekuitas", _num(data["total_ekuitas"])]))
    rows.append(("total", ["", "", "Total Kewajiban + Ekuitas",
                           _num(data["total_kewajiban"] + data["total_ekuitas"])]))
    wb = excel_service.report_sheet(
        headers=["No", "Kode Akun", "Nama Akun", "Jumlah"],
        rows=rows, sheet="Neraca",
        company=laporan_service.get_company_name(db),
        title="Neraca",
        period=f"Per Tanggal: {_fmt_date(tanggal)}",
        decimal_columns={3}, wide_columns={2},
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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/arus-kas/export")
def export_arus_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Laporan Arus Kas ke file .xlsx — seksi operasional/investasi/
    pembiayaan (+belum terklasifikasi bila ada) + subtotal tiap seksi + saldo
    awal/perubahan bersih/saldo akhir (view detail penuh)."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_arus_kas(db, date_from, date_to)

    sections = (
        ("Arus Kas dari Aktivitas Operasional", "operasional"),
        ("Arus Kas dari Aktivitas Investasi", "investasi"),
        ("Arus Kas dari Aktivitas Pembiayaan", "pembiayaan"),
        ("Belum Diklasifikasi", "belum_diklasifikasikan"),
    )
    rows: list[tuple[str, list]] = []
    for section_title, key in sections:
        bagian = data[key]
        if key == "belum_diklasifikasikan" and not bagian["items"] \
                and not data["jumlah_jurnal_belum_diklasifikasi"]:
            continue
        rows.append(("section", [section_title]))
        for item in bagian["items"]:
            kode, nama = item.get("account_code"), item.get("account_name") or item.get("nama")
            lawan = f"{kode} — {nama}" if kode else (nama or "Multi-akun")
            keterangan = item.get("line_description") or item.get("transaction_description") \
                or item.get("nama") or ""
            rows.append(("item", [
                _as_date(item["tanggal"]) if item.get("tanggal") else None,
                item.get("no_jurnal") or "",
                lawan,
                keterangan,
                item.get("source_no") or "",
                _num(item["jumlah"]),
            ]))
        rows.append(("subtotal", [f"Total {section_title}", "", "", "", "", _num(bagian["total"])]))
    rows.append(("total", ["Saldo Awal", "", "", "", "", _num(data["saldo_awal"])]))
    rows.append(("total", ["Perubahan Bersih", "", "", "", "", _num(data["net_change"])]))
    rows.append(("total", ["Saldo Akhir", "", "", "", "", _num(data["saldo_akhir"])]))
    rows.append(("subtotal", ["Selisih Rekonsiliasi", "", "", "", "", _num(data["selisih_rekonsiliasi"])]))
    wb = excel_service.report_sheet(
        headers=["Tanggal", "No. Jurnal", "Akun Lawan", "Keterangan", "No. Referensi", "Jumlah"],
        rows=rows, sheet="Arus Kas",
        company=laporan_service.get_company_name(db),
        title="Laporan Arus Kas",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/buku-besar/export")
def export_buku_besar(
    akun_id: UUID = Query(..., description="ID Akun Perkiraan"),
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rincian Buku Besar per akun ke file .xlsx (saldo awal +
    transaksi berikut running balance + total debit/kredit + saldo akhir)."""
    date_from, date_to = _parse_range(dari, sampai)
    try:
        data = laporan_service.get_buku_besar(db, akun_id, date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    akun = data["akun"]

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
        company=laporan_service.get_company_name(db),
        title="Rincian Buku Besar",
        period=f"Akun: {akun['kode']} — {akun['nama']} · Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns={3, 4, 5}, date_columns={0}, wide_columns={2},
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


# ===== EXPORT EXCEL (Update #023) =====
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


@router.get("/mutasi-kas/export")
def export_mutasi_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Mutasi Kas ke file .xlsx — seksi per akun kas, kolom transaksi
    (tanggal/no jurnal/akun/deskripsi/debit/kredit) + saldo akhir tiap akun."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="KAS")

    wb = excel_service.report_sheet(
        headers=["Tanggal", "No. Jurnal", "Akun", "Deskripsi", "Debit", "Kredit", "Saldo"],
        rows=_mutasi_rows(data), sheet="Mutasi Kas",
        company=laporan_service.get_company_name(db),
        title="Mutasi Kas",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns={4, 5, 6}, date_columns={0}, wide_columns={3},
    )
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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/mutasi-bank/export")
def export_mutasi_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Mutasi Bank ke file .xlsx — seksi per akun bank, kolom transaksi
    (tanggal/no jurnal/akun/deskripsi/debit/kredit) + saldo akhir tiap akun."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="BANK")

    wb = excel_service.report_sheet(
        headers=["Tanggal", "No. Jurnal", "Akun", "Deskripsi", "Debit", "Kredit", "Saldo"],
        rows=_mutasi_rows(data), sheet="Mutasi Bank",
        company=laporan_service.get_company_name(db),
        title="Mutasi Bank",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns={4, 5, 6}, date_columns={0}, wide_columns={3},
    )
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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/rekap-kas-bank/export")
def export_rekap_kas_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekap Kas & Bank (per akun: saldo awal, masuk, keluar, akhir)
    ke file .xlsx + baris total."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_rekap_kas_bank(db, date_from, date_to)

    rows: list[tuple[str, list]] = []
    for idx, akun in enumerate(data["akun"], start=1):
        rows.append(("item", [idx, akun["kode"], akun["nama"], akun["jenis"],
                              _num(akun["saldo_awal"]), _num(akun["total_masuk"]),
                              _num(akun["total_keluar"]), _num(akun["saldo_akhir"])]))
    akun_list = data["akun"]
    total_awal = sum((a["saldo_awal"] for a in akun_list), Decimal(0))
    total_masuk = sum((a["total_masuk"] for a in akun_list), Decimal(0))
    total_keluar = sum((a["total_keluar"] for a in akun_list), Decimal(0))
    total_akhir = sum((a["saldo_akhir"] for a in akun_list), Decimal(0))
    rows.append(("total", ["Total", "", "", None, _num(total_awal), _num(total_masuk),
                           _num(total_keluar), _num(total_akhir)]))
    wb = excel_service.report_sheet(
        headers=["No", "Kode", "Nama Akun", "Jenis", "Saldo Awal", "Total Masuk", "Total Keluar", "Saldo Akhir"],
        rows=rows, sheet="Rekap Kas & Bank",
        company=laporan_service.get_company_name(db),
        title="Rekap Kas & Bank",
        period=f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}",
        decimal_columns={4, 5, 6, 7}, wide_columns={2},
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


# ===== EXPORT EXCEL (Update #023) =====
@router.get("/rekap-ppn/export")
def export_rekap_ppn(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Rekap PPN ke file .xlsx — 2 sheet: ringkasan per masa pajak
    (+ baris TOTAL) dan rincian dokumen (kolom sama seperti tampilan web)."""
    date_from, date_to = _parse_range(dari, sampai)
    data = laporan_service.get_rekap_ppn(db, date_from, date_to)

    company = laporan_service.get_company_name(db)
    period = f"Periode: {_fmt_date(dari)} - {_fmt_date(sampai)}"

    masa_rows: list[tuple[str, list]] = []
    for m in data["ringkasan_masa"]:
        masa_rows.append(("item", [m["masa"], _num(m["dpp_keluaran"]), _num(m["ppn_keluaran"]),
                                    _num(m["dpp_masukan"]), _num(m["ppn_masukan"]), _num(m["ppn_neto"])]))
    if data["total"] is not None:
        t = data["total"]
        masa_rows.append(("total", ["Total", _num(t["dpp_keluaran"]), _num(t["ppn_keluaran"]),
                                    _num(t["dpp_masukan"]), _num(t["ppn_masukan"]), _num(t["ppn_neto"])]))
    wb = excel_service.report_sheet(
        headers=["Masa", "DPP Keluaran", "PPN Keluaran", "DPP Masukan", "PPN Masukan", "PPN Neto"],
        rows=masa_rows, sheet="Rekap PPN",
        company=company, title="Rekap PPN (Masa Pajak)", period=period,
        decimal_columns={1, 2, 3, 4, 5},
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
