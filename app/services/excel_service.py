"""Helper generik untuk membuat workbook Excel (openpyxl) siap di-stream.

Update #5 — fitur Import & Export Excel.
Dipakai endpoint export master (barang/pelanggan/supplier), export tagihan
piutang/hutang, export stok, dan template import master.

Update #023 — export laporan keuangan: report_sheet() + append_report_title()
(builder sheet laporan bergaya korporat — kop perusahaan, seksi, baris total).

Update #024 — gaya mengikuti workbook pembukuan ASAHI (Master MAS):
- ACCOUNTING_FORMAT: angka tampil "-" untuk nol (format akuntansi ala
  template mereka '_(* #,##0.00_);...') — dipakai seluruh kolom desimal
  report_sheet.
- percent_columns pada report_sheet (kolom "%" ala sheet LR).
- block_report_sheet(): header DUA TINGKAT untuk laporan blok-bulanan ala
  sheet NS (kolom info + blok bulanan × sub-kolom, judul blok merge).

Style minimal-profesional:
- Header: bold + fill abu terang + border tipis, freeze panes A2.
- Auto column width (perkiraan panjang isi, min 10 max 45).
- Kolom angka: number_format '#,##0' (integer) / '#,##0.00' (desimal);
  kolom tanggal: 'DD/MM/YYYY'.
"""
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="F2F2F2")
HEADER_FONT = Font(bold=True)
_THIN = Side(style="thin", color="D9D9D9")
THIN_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

INTEGER_FORMAT = "#,##0"
DECIMAL_FORMAT = "#,##0.00"
DATE_FORMAT = "DD/MM/YYYY"

# Update #024 — format akuntansi ala workbook pembukuan ASAHI (Master MAS):
# nol tampil "-", negatif dalam kurung, rata dengan spasi pengaman._(_).
ACCOUNTING_FORMAT = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
PERCENT_FORMAT = "0.0%"

MIN_WIDTH = 10
MAX_WIDTH = 45

# ==========================================
# Update #023 — Sheet laporan keuangan korporat
# ==========================================

# Kop laporan: baris 1 nama perusahaan (bold 14), baris 2 judul (bold 12),
# baris 3 periode (normal 10), baris 4 "Dicetak: ..." (italic 9 abu-abu).
REPORT_COMPANY_FONT = Font(bold=True, size=14)
REPORT_TITLE_FONT = Font(bold=True, size=12)
REPORT_PERIOD_FONT = Font(size=10)
REPORT_PRINTED_FONT = Font(italic=True, size=9, color="808080")

# Baris seksi: bold + fill abu sangat terang + teks UPPERCASE.
SECTION_FILL = PatternFill("solid", fgColor="F7F7F7")
REPORT_BOLD_FONT = Font(bold=True)

# Baris total/subtotal: bold + border ATAS medium (subtotal) / double (total).
_MEDIUM = Side(style="medium", color="D9D9D9")
_DOUBLE = Side(style="double", color="D9D9D9")
SUBTOTAL_BORDER = Border(left=_THIN, right=_THIN, top=_MEDIUM, bottom=_THIN)
TOTAL_BORDER = Border(left=_THIN, right=_THIN, top=_DOUBLE, bottom=_THIN)

# Kolom nama (akun/pihak/keterangan) boleh lebih lebar dari MAX_WIDTH.
NAME_MAX_WIDTH = 50


def _cell_text(value) -> str:
    """Representasi teks sebuah nilai untuk estimasi lebar kolom."""
    if value is None:
        return ""
    return str(value)


def workbook_from_rows(
    headers: list[str],
    rows: list[list],
    sheet: str = "Data",
    number_columns: set[int] | None = None,
    decimal_columns: set[int] | None = None,
    date_columns: set[int] | None = None,
) -> Workbook:
    """Bangun Workbook dari header + baris data.

    number_columns  : indeks kolom (0-based) dengan format integer '#,##0'.
    decimal_columns : indeks kolom (0-based) dengan format desimal '#,##0.00'.
    date_columns    : indeks kolom (0-based) berisi date/datetime → 'DD/MM/YYYY'.
    """
    number_columns = number_columns or set()
    decimal_columns = decimal_columns or set()
    date_columns = date_columns or set()

    wb = Workbook()
    ws = wb.active
    ws.title = sheet

    ws.append(list(headers))
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(vertical="center")

    for row in rows:
        ws.append(list(row))

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = THIN_BORDER

    # Number/date format per kolom (0-based index → 1-based kolom excel).
    for idx in sorted(number_columns | decimal_columns | date_columns):
        fmt = DECIMAL_FORMAT if idx in decimal_columns else INTEGER_FORMAT if idx in number_columns else DATE_FORMAT
        for row in ws.iter_rows(min_row=2, min_col=idx + 1, max_col=idx + 1):
            row[0].number_format = fmt

    # Auto column width: perkiraan panjang isi (header + data), min 10 max 45.
    for idx, header in enumerate(headers, start=1):
        longest = len(_cell_text(header))
        for row in rows:
            if idx - 1 < len(row):
                longest = max(longest, len(_cell_text(row[idx - 1])))
        ws.column_dimensions[get_column_letter(idx)].width = max(MIN_WIDTH, min(MAX_WIDTH, longest + 2))

    ws.freeze_panes = "A2"
    return wb


def add_instructions_sheet(wb: Workbook, rows: list[list], sheet: str = "Petunjuk") -> None:
    """Tambah sheet petunjuk (kolom, wajib, tipe data, keterangan + catatan umum).

    rows: list baris — baris pertama dianggap header sheet Petunjuk.
    """
    ws = wb.create_sheet(title=sheet)
    for row in rows:
        ws.append(list(row))
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    if rows:
        n_cols = len(rows[0])
        for idx in range(1, n_cols + 1):
            longest = max(len(_cell_text(r[idx - 1])) for r in rows)
            # Kolom keterangan terakhir diberi lebar penuh agar catatan terbaca.
            width = MAX_WIDTH if idx == n_cols else max(MIN_WIDTH, min(MAX_WIDTH, longest + 2))
            ws.column_dimensions[get_column_letter(idx)].width = width


def append_report_title(
    ws,
    title_lines: list[tuple[str, int | None]],
    n_columns: int = 1,
) -> int:
    """Tulis blok kop judul laporan korporat di atas sheet (Update #023).

    title_lines: daftar (teks, ukuran_font). Ukuran dipetakan ke gaya baku:
    14 → nama perusahaan (bold), 12 → judul laporan (bold), 10 → info periode
    (normal), 9/None → catatan "Dicetak: ..." (italic, abu-abu). Setiap baris
    di-merge selebar n_columns dan rata tengah.

    Blok ditutup SATU baris kosong. Return nomor baris untuk header kolom.
    """
    styles = {14: REPORT_COMPANY_FONT, 12: REPORT_TITLE_FONT, 10: REPORT_PERIOD_FONT}
    row = 1
    for text, size in title_lines:
        cell = ws.cell(row=row, column=1, value=text)
        cell.font = styles.get(size, REPORT_PRINTED_FONT)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        if n_columns > 1:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=n_columns)
        row += 1
    return row + 1  # sisakan satu baris kosong setelah kop


def report_sheet(
    headers: list[str],
    rows: list[tuple[str, list]],
    sheet: str = "Laporan",
    wb: Workbook | None = None,
    company: str = "",
    title: str = "",
    period: str = "",
    number_columns: set[int] | None = None,
    decimal_columns: set[int] | None = None,
    date_columns: set[int] | None = None,
    percent_columns: set[int] | None = None,
    wide_columns: set[int] | None = None,
    printed_at: datetime | None = None,
) -> Workbook:
    """Bangun sheet laporan keuangan bergaya korporat (Update #023).

    Layout per sheet:
    - Baris 1-4: kop — (company, 14), (title, 12), (period, 10) bila diisi,
      lalu "Dicetak: dd/mm/yyyy HH:MM" (italic abu-abu) — merge selebar kolom,
      rata tengah; ditutup satu baris kosong.
    - Baris header: gaya `workbook_from_rows` (bold + fill F2F2F2 + border
      tipis) + freeze panes tepat di bawahnya.
    - Baris data `rows`: list tuple (kind, values), kind ∈
      {"section", "item", "subtotal", "total"}:
        "section"  : bold + fill F7F7F7 + teks UPPERCASE di kolom pertama
                      (kolom lain dikosongkan — colspan visual tidak wajib);
        "item"     : baris data biasa (border tipis);
        "subtotal" : bold + border ATAS medium;
        "total"    : bold + border ATAS double (nilai tetap di kolom angka).
      values sepanjang headers (0-based, selisih panjang dianggap kosong).

    Kolom (indeks 0-based): decimal_columns → ACCOUNTING_FORMAT (Update #024,
    nol tampil "-"), number_columns → '#,##0', date_columns → 'DD/MM/YYYY'
    (nilai berisi date/datetime), percent_columns → '0.0%' (nilai = rasio
    0..1, ala kolom "%" sheet LR Master MAS).
    wide_columns: kolom nama yang boleh lebar sampai 50 (lainnya max 45, min 10).

    wb None → Workbook baru (sheet aktif dipakai); wb diberikan → sheet baru
    dibuat lewat create_sheet (untuk laporan multi-sheet, mis. rekap pajak).
    """
    number_columns = number_columns or set()
    decimal_columns = decimal_columns or set()
    date_columns = date_columns or set()
    percent_columns = percent_columns or set()
    wide_columns = wide_columns or set()
    n_cols = len(headers)

    if wb is None:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet
    else:
        ws = wb.create_sheet(title=sheet)

    printed = (printed_at or datetime.now()).strftime("%d/%m/%Y %H:%M")
    title_lines: list[tuple[str, int | None]] = [(company or "-", 14), (title, 12)]
    if period:
        title_lines.append((period, 10))
    title_lines.append((f"Dicetak: {printed}", 9))

    header_row = append_report_title(ws, title_lines, n_columns=n_cols)

    for idx, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=idx, value=text)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(vertical="center")

    widths = [len(_cell_text(h)) for h in headers]
    fmt_by_col = {}
    for idx in sorted(number_columns | decimal_columns | date_columns | percent_columns):
        fmt_by_col[idx] = (
            ACCOUNTING_FORMAT if idx in decimal_columns
            else PERCENT_FORMAT if idx in percent_columns
            else INTEGER_FORMAT if idx in number_columns
            else DATE_FORMAT
        )

    r = header_row + 1
    for kind, values in rows:
        cells = []
        for idx in range(n_cols):
            value = values[idx] if idx < len(values) else None
            if kind == "section" and idx > 0:
                value = None  # seksi: cukup teks di kolom pertama
            if kind == "section" and idx == 0 and value is not None:
                value = str(value).upper()
            cell = ws.cell(row=r, column=idx + 1, value=value)
            cells.append(cell)
            if value is not None:
                widths[idx] = max(widths[idx], len(_cell_text(value)))
        if kind == "section":
            for cell in cells:
                cell.font = REPORT_BOLD_FONT
                cell.fill = SECTION_FILL
                cell.border = THIN_BORDER
        elif kind == "subtotal":
            for cell in cells:
                cell.font = REPORT_BOLD_FONT
                cell.border = SUBTOTAL_BORDER
        elif kind == "total":
            for cell in cells:
                cell.font = REPORT_BOLD_FONT
                cell.border = TOTAL_BORDER
        else:  # "item"
            for cell in cells:
                cell.border = THIN_BORDER
        for idx, fmt in fmt_by_col.items():
            cells[idx].number_format = fmt
        r += 1

    # Auto column width (kop diabaikan — sudah merge & center), min 10 max 45/50.
    for idx in range(n_cols):
        cap = NAME_MAX_WIDTH if idx in wide_columns else MAX_WIDTH
        ws.column_dimensions[get_column_letter(idx + 1)].width = max(MIN_WIDTH, min(cap, widths[idx] + 2))

    # Freeze tepat di bawah baris header (kop + header tetap terlihat saat scroll).
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    return wb


def block_report_sheet(
    left_headers: list[str],
    blocks: list[tuple[str, list[str]]],
    rows: list[tuple[str, list]],
    sheet: str = "Laporan",
    wb: Workbook | None = None,
    company: str = "",
    title: str = "",
    period: str = "",
    decimal_columns: set[int] | None = None,
    wide_columns: set[int] | None = None,
    printed_at: datetime | None = None,
) -> Workbook:
    """Bangun sheet laporan BLOK BULANAN ala sheet NS Master MAS (Update #024).

    Layout header DUA TINGKAT (pola "Neraca Saldo" workbook ASAHI):
    - Kolom info kiri (mis. Kode/Nama/Normal): label merge vertikal 2 baris.
    - Setiap blok (mis. satu bulan): judul blok merge horizontal di baris-1
      selebar sub-kolomnya, sub-label di baris-2 (mis. "Saldo Awal Db." dst).
    - Kop 4 baris + freeze tepat di bawah baris-2 header.

    left_headers   : label kolom info (kiri).
    blocks         : list (judul_blok, [sub_label, ...]).
    rows           : list (kind, values) — values FLAT sepanjang SEMUA kolom
                     (kolom info + semua sub-kolom tiap blok, 0-based global).
    decimal_columns: indeks global kolom angka → ACCOUNTING_FORMAT.
    wide_columns   : kolom nama yang boleh lebar sampai 50.
    """
    decimal_columns = decimal_columns or set()
    wide_columns = wide_columns or set()
    n_left = len(left_headers)
    sub_widths = [len(sub) for _, sub in blocks]
    n_cols = n_left + sum(sub_widths)

    if wb is None:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet
    else:
        ws = wb.create_sheet(title=sheet)

    printed = (printed_at or datetime.now()).strftime("%d/%m/%Y %H:%M")
    title_lines: list[tuple[str, int | None]] = [(company or "-", 14), (title, 12)]
    if period:
        title_lines.append((period, 10))
    title_lines.append((f"Dicetak: {printed}", 9))
    header_row = append_report_title(ws, title_lines, n_columns=n_cols)

    h1, h2 = header_row, header_row + 1

    def _style_header(cell):
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)

    # Baris-1: kolom info (merge vertikal H1:H2) + judul blok (merge horizontal).
    for idx, label in enumerate(left_headers, start=1):
        cell = ws.cell(row=h1, column=idx, value=label)
        _style_header(cell)
        if n_left > 0:
            ws.merge_cells(start_row=h1, start_column=idx, end_row=h2, end_column=idx)
    col = n_left + 1
    for (label, sub) in blocks:
        cell = ws.cell(row=h1, column=col, value=label)
        _style_header(cell)
        if len(sub) > 1:
            ws.merge_cells(start_row=h1, start_column=col, end_row=h1, end_column=col + len(sub) - 1)
        for sub_label in sub:
            c2 = ws.cell(row=h2, column=col, value=sub_label)
            _style_header(c2)
            col += 1

    # Baris data — kind sama seperti report_sheet.
    widths = [0] * n_cols
    for idx, label in enumerate(left_headers):
        widths[idx] = len(_cell_text(label))
    col = n_left
    for (_, sub) in blocks:
        for sub_label in sub:
            widths[col] = len(_cell_text(sub_label))
            col += 1

    r = h2 + 1
    for kind, values in rows:
        cells = []
        for idx in range(n_cols):
            value = values[idx] if idx < len(values) else None
            if kind == "section" and idx > 0:
                value = None
            if kind == "section" and idx == 0 and value is not None:
                value = str(value).upper()
            cell = ws.cell(row=r, column=idx + 1, value=value)
            cells.append(cell)
            if value is not None:
                widths[idx] = max(widths[idx], len(_cell_text(value)))
        if kind == "section":
            for cell in cells:
                cell.font = REPORT_BOLD_FONT
                cell.fill = SECTION_FILL
                cell.border = THIN_BORDER
        elif kind == "subtotal":
            for cell in cells:
                cell.font = REPORT_BOLD_FONT
                cell.border = SUBTOTAL_BORDER
        elif kind == "total":
            for cell in cells:
                cell.font = REPORT_BOLD_FONT
                cell.border = TOTAL_BORDER
        else:  # "item"
            for cell in cells:
                cell.border = THIN_BORDER
        for idx in decimal_columns:
            if idx < n_cols:
                cells[idx].number_format = ACCOUNTING_FORMAT
        r += 1

    for idx in range(n_cols):
        cap = NAME_MAX_WIDTH if idx in wide_columns else MAX_WIDTH
        ws.column_dimensions[get_column_letter(idx + 1)].width = max(MIN_WIDTH, min(cap, widths[idx] + 2))

    ws.freeze_panes = ws.cell(row=h2 + 1, column=1)
    return wb


def workbook_to_stream(wb: Workbook) -> BytesIO:
    """Simpan workbook ke BytesIO siap dikirim via StreamingResponse."""
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def xlsx_response(wb: Workbook, filename: str) -> dict:
    """Metadata response standar untuk StreamingResponse file .xlsx."""
    return {
        "stream": workbook_to_stream(wb),
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "headers": {"Content-Disposition": f'attachment; filename="{filename}"'},
    }


# ==========================================
# Update #027 — Sheet generik Laporan Lainnya
# ==========================================
# Rekonsiliasi persediaan/GRNI/CF-vs-BS/Ekuitas-vs-BS, audit persediaan, dan
# accounting health punya bentuk respons yang bervariasi antar laporan
# (campuran skalar + tabel). generic_report_sheet() merender payload dict
# SEMBARANG menjadi satu sheet bergaya korporat yang sama dengan laporan
# inti (#023-#025): kop perusahaan, seksi, header kolom, format akuntansi.

import json as _json
import re as _re
from datetime import date as _date_cls
from decimal import Decimal as _Decimal_cls
from enum import Enum as _Enum_cls
from uuid import UUID as _UUID_cls

# Kunci kolom → akronim/istilah yang harus tetap tampil utuh (bukan "Ppn").
_ACRONYMS = {
    "ppn": "PPN", "pph": "PPh", "dpp": "DPP", "grni": "GRNI", "gl": "GL",
    "bs": "BS", "cf": "CF", "ar": "AR", "ap": "AP", "sa": "SA", "sak": "SAK",
    "qty": "Qty", "npwp": "NPWP", "no": "No.", "id": "ID", "vs": "vs",
    "coa": "COA", "wip": "WIP", "lppb": "LPPB", "lpb": "LPB", "rop": "ROP", "sku": "SKU",
    "hpp": "HPP", "pph23": "PPh 23",
}

DATETIME_FORMAT = "DD/MM/YYYY HH:MM"


def pretty_header(key) -> str:
    """Kunci payload → judul kolom ramah dibaca.

    'total_selisih' → 'Total Selisih'; 'asOf' → 'As Of'; 'ppnKeluaran' →
    'PPN Keluaran'; 'pph23' → 'PPh 23'. Dipakai generic_report_sheet untuk
    header tabel & label ringkasan.
    """
    s = _re.sub(r"[_\s]+", " ", str(key)).strip()
    s = _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)     # camelCase → "camel Case"
    s = _re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)   # "PPNKeluaran" → "PPN Keluaran"
    words = []
    for w in s.split():
        lw = w.lower()
        if lw in _ACRONYMS:
            words.append(_ACRONYMS[lw])
            continue
        m = _re.fullmatch(r"([a-z]+)(\d+)", lw)
        if m:  # "pph23" → "PPh 23"
            words.append(f"{m.group(1).capitalize()} {m.group(2)}")
        else:
            words.append(w if w.isupper() else w.capitalize())
    return " ".join(words) or str(key)


def cell_value(v):
    """Nilai payload apa pun → nilai sel Excel yang aman.

    Decimal → float; bool → 'Ya'/'Tidak'; UUID/Enum → str; dict/list → JSON
    ringkas; date/datetime tetap objek (diformat per kolom sel).
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return "Ya" if v else "Tidak"
    if isinstance(v, int):          # bool sudah dicek lebih dulu
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, _Decimal_cls):
        return float(v)
    if isinstance(v, (datetime, _date_cls)):
        return v
    if isinstance(v, str):
        return v
    if isinstance(v, _UUID_cls):
        return str(v)
    if isinstance(v, _Enum_cls):
        return v.value
    if isinstance(v, (dict, list, tuple)):
        return _json.dumps(v, default=str, ensure_ascii=False)
    return str(v)


def _is_number(v) -> bool:
    return isinstance(v, (int, float, _Decimal_cls)) and not isinstance(v, bool)


def generic_report_sheet(
    payload,
    sheet: str = "Laporan",
    wb: Workbook | None = None,
    company: str = "",
    title: str = "",
    period: str = "",
    printed_at: datetime | None = None,
    max_rows: int = 20000,
) -> Workbook:
    """Sheet laporan SERBA-GENERIK dari payload dict sembarang (Update #027).

    Payload dibedah menjadi seksi (urutan kunci dipertahankan):
    - RINGKASAN: pasangan Uraian/Nilai dari nilai skalar; dict bersarang
      diratakan satu tingkat dengan kunci bertitik ('summary.matchCount').
    - Satu TABEL per nilai list-of-dicts — header = union kunci baris
      (urut kemunculan pertama), judul seksi = pretty_header(kunci payload).
    - List skalar → tabel satu kolom; list kosong → dilewati.

    Gaya konsisten report_sheet(): kop korporat (merge selebar kolom),
    baris seksi bold + fill, header kolom bold + fill + border, format
    akuntansi (nol '-') untuk kolom angka, 'DD/MM/YYYY' tanggal, freeze
    tepat di bawah kop. Baris tabel dipotong aman di max_rows dengan baris
    catatan.
    """
    if not isinstance(payload, dict):
        payload = {"Data": payload if isinstance(payload, list) else [payload]}

    # ── Bedah payload: skalar (diratakan) + tabel ─────────────────────────
    scalars: list[tuple[str, object]] = []
    tables: list[tuple[str, list, list]] = []   # (judul_seksi, kunci_kolom, baris_raw)

    for key, val in payload.items():
        if isinstance(val, dict):
            for k2, v2 in val.items():
                if isinstance(v2, dict):
                    for k3, v3 in v2.items():
                        scalars.append((f"{pretty_header(k2)}.{pretty_header(k3)}", v3))
                else:
                    scalars.append((pretty_header(k2), v2))
        elif isinstance(val, list):
            dicts = [x for x in val if isinstance(x, dict)]
            if val and len(dicts) == len(val):
                headers: list = []
                for row in dicts:
                    for k in row:
                        if k not in headers:
                            headers.append(k)
                tables.append((pretty_header(key), headers, dicts))
            elif val:
                tables.append((pretty_header(key), ["Nilai"], [[x] for x in val]))
            # list kosong → dilewati
        else:
            scalars.append((pretty_header(key), val))

    n_cols = max([2] + [len(h) for _, h, _ in tables])

    # ── Bangun sheet ──────────────────────────────────────────────────────
    if wb is None:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet
    else:
        ws = wb.create_sheet(title=sheet)

    printed = (printed_at or datetime.now()).strftime("%d/%m/%Y %H:%M")
    title_lines: list[tuple[str, int | None]] = [(company or "-", 14), (title, 12)]
    if period:
        title_lines.append((period, 10))
    title_lines.append((f"Dicetak: {printed}", 9))
    r = append_report_title(ws, title_lines, n_columns=n_cols)
    header_row = r

    widths = [0] * n_cols

    def _section_row(label: str):
        nonlocal r
        ws.cell(row=r, column=1, value=str(label).upper())
        for idx in range(n_cols):
            c = ws.cell(row=r, column=idx + 1)
            c.font = REPORT_BOLD_FONT
            c.fill = SECTION_FILL
            c.border = THIN_BORDER
        widths[0] = max(widths[0], len(str(label)))
        r += 1

    def _write_cell(row: int, col: int, raw):
        v = cell_value(raw)
        cell = ws.cell(row=row, column=col + 1, value=v)
        cell.border = THIN_BORDER
        if isinstance(v, int):
            cell.number_format = INTEGER_FORMAT
        elif isinstance(v, float):
            cell.number_format = ACCOUNTING_FORMAT
        elif isinstance(v, datetime):
            cell.number_format = DATETIME_FORMAT
        elif isinstance(v, _date_cls):
            cell.number_format = DATE_FORMAT
        elif v is not None:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        widths[col] = max(widths[col], len(_cell_text(v)))
        return cell

    # ── Seksi RINGKASAN (bila ada skalar) ────────────────────────────────
    if scalars:
        _section_row("Ringkasan")
        for label, raw in scalars:
            c1 = ws.cell(row=r, column=1, value=label)
            c1.border = THIN_BORDER
            widths[0] = max(widths[0], len(label))
            _write_cell(r, 1, raw)
            r += 1
        r += 1  # baris kosong antar seksi

    # ── Tabel per seksi ──────────────────────────────────────────────────
    for judul, headers, rows in tables:
        _section_row(judul)
        for idx, k in enumerate(headers):
            cell = ws.cell(row=r, column=idx + 1, value=pretty_header(k))
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical="center")
            widths[idx] = max(widths[idx], len(pretty_header(k)))
        r += 1
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        # Deteksi kolom angka/tanggal seluruhnya → format kolom konsisten.
        col_kind: dict[int, str] = {}
        for idx in range(len(headers)):
            kinds = set()
            for row in rows:
                v = row.get(headers[idx]) if isinstance(row, dict) else row[idx]
                if v is None:
                    continue
                if isinstance(v, bool):
                    kinds.add("text")
                elif _is_number(v):
                    kinds.add("number")
                elif isinstance(v, (datetime, _date_cls)):
                    kinds.add("date")
                else:
                    kinds.add("text")
            if kinds == {"number"}:
                col_kind[idx] = "number"
            elif kinds == {"date"}:
                col_kind[idx] = "date"
        for row in rows:
            for idx in range(len(headers)):
                raw = row.get(headers[idx]) if isinstance(row, dict) else row[idx]
                cell = _write_cell(r, idx, raw)
                if col_kind.get(idx) == "number" and isinstance(cell.value, (int, float)):
                    cell.number_format = INTEGER_FORMAT if isinstance(cell.value, int) else ACCOUNTING_FORMAT
                elif col_kind.get(idx) == "date" and isinstance(cell.value, (datetime, _date_cls)):
                    cell.number_format = DATETIME_FORMAT if isinstance(cell.value, datetime) else DATE_FORMAT
            r += 1
        if truncated:
            note = ws.cell(row=r, column=1, value=f"… (dipotong: baris melebihi {max_rows} tidak diekspor)")
            note.font = REPORT_PRINTED_FONT
            r += 1
        r += 1  # baris kosong antar seksi

    for idx in range(n_cols):
        ws.column_dimensions[get_column_letter(idx + 1)].width = max(MIN_WIDTH, min(NAME_MAX_WIDTH, widths[idx] + 2))

    # Freeze tepat di bawah kop — kop tetap terlihat saat scroll tabel panjang.
    ws.freeze_panes = ws.cell(row=header_row, column=1)
    return wb
