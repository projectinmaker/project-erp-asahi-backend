"""Helper generik untuk membuat workbook Excel (openpyxl) siap di-stream.

Update #5 — fitur Import & Export Excel.
Dipakai endpoint export master (barang/pelanggan/supplier), export tagihan
piutang/hutang, export stok, dan template import master.

Update #023 — export laporan keuangan: report_sheet() + append_report_title()
(builder sheet laporan bergaya korporat — kop perusahaan, seksi, baris total).

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

    Kolom (indeks 0-based): decimal_columns → '#,##0.00', number_columns →
    '#,##0', date_columns → 'DD/MM/YYYY' (nilai berisi date/datetime).
    wide_columns: kolom nama yang boleh lebar sampai 50 (lainnya max 45, min 10).

    wb None → Workbook baru (sheet aktif dipakai); wb diberikan → sheet baru
    dibuat lewat create_sheet (untuk laporan multi-sheet, mis. rekap pajak).
    """
    number_columns = number_columns or set()
    decimal_columns = decimal_columns or set()
    date_columns = date_columns or set()
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
    for idx in sorted(number_columns | decimal_columns | date_columns):
        fmt_by_col[idx] = (
            DECIMAL_FORMAT if idx in decimal_columns
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
