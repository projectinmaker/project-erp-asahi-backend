from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.master.barang import Barang
from app.models.master.gudang import Gudang
from app.models.master.syarat_bayar import SyaratBayar
from app.models.master.kategori_aset import KategoriAset
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank
from app.models.master.setting_akun import SettingAkun
from app.models.master.app_setting import AppSetting
from app.models.master.company_profile import CompanyProfile
from app.models.master.mata_uang import MataUang
from app.models.master.alamat_pengiriman import AlamatPengiriman
from app.models.master.rekening_bank import RekeningBank
from app.models.master.kategori_barang import KategoriBarang
from app.models.master.satuan import Satuan
from app.models.master.barang import ItemTypeBarang
from app.models.detail.barang_satuan import BarangSatuan
from app.models.transaksi.stock_balance import StockBalance
from app.models.transaksi.stok_mutasi import StokMutasi
from app.models.transaksi.persediaan.penyesuaian_stok import StatusPersediaan
from app.schemas.base import PaginatedResponse
from app.schemas.master import (
    PelangganCreate, PelangganUpdate, PelangganResponse,
    PelangganFromCoaCreate, PelangganCoaResponse,
    SupplierCreate, SupplierUpdate, SupplierResponse,
    SupplierFromCoaCreate, SupplierCoaResponse,
    BarangCreate, BarangUpdate, BarangResponse,
    BarangSatuanCreate, BarangSatuanUpdate, BarangSatuanResponse,
    KategoriBarangCreate, KategoriBarangUpdate, KategoriBarangResponse,
    SatuanCreate, SatuanUpdate, SatuanResponse,
    GudangCreate, GudangUpdate, GudangResponse,
    SyaratBayarCreate, SyaratBayarUpdate, SyaratBayarResponse,
    KategoriAsetCreate, KategoriAsetUpdate, KategoriAsetResponse,
    KasBankAkunCreate, KasBankAkunUpdate, KasBankAkunResponse,
    SettingAkunUpdate, SettingAkunResponse,
    AppSettingUpdate, AppSettingResponse,
    CompanyProfileResponse, CompanyProfileUpdate,
    MataUangCreate, MataUangUpdate, MataUangResponse,
    AlamatPengirimanCreate, AlamatPengirimanUpdate, AlamatPengirimanResponse,
    RekeningBankCreate, RekeningBankUpdate, RekeningBankResponse,
    COASimpleResponse,
    ImportResult, ImportRowError,
)

# Simple schemas for dropdown
from app.schemas.base import BaseSchema
from app.services import excel_service


class BarangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    harga_pokok: Decimal = Decimal("0")
    harga_jual: Decimal = Decimal("0")
    stok: int = 0
    # Update #4 — dipakai barang-dropdown agar FE tahu metode valuasi barang
    # (optional agar aman untuk pemakaian lain kelas ini)
    metode_valuasi: str | None = None


class PelangganSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    # === Update #4 — auto-fill master data di form transaksi ===
    # Dropdown pelanggan kini membawa data master (alamat, kontak, syarat bayar)
    # sehingga form SO/Invoice/Penawaran/Pengiriman bisa menarik otomatis tanpa
    # input manual. Field opsional — aman untuk pemakaian lama.
    alamat: str | None = None
    telepon: str | None = None
    kontak_person: str | None = None
    email: str | None = None
    syarat_bayar_id: UUID | None = None


class SupplierSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    # === Update #4 — auto-fill master data di form transaksi ===
    # Dropdown supplier membawa alamat/kontak/syarat bayar + mata uang default
    # supplier (dipakai form PO untuk memilih currency otomatis).
    alamat: str | None = None
    telepon: str | None = None
    kontak_person: str | None = None
    email: str | None = None
    syarat_bayar_id: UUID | None = None
    currency: str | None = None


from app.services import master_service
from app.services import persediaan_service
from app.services import setting_akun_service
from app.services import workflow_service
from app.services.coa_linkage_service import (
    auto_create_piutang_coa, auto_create_hutang_coa,
    find_piutang_root_coa, find_hutang_root_coa, get_coa_detail_ids_under,
)

router = APIRouter()


# ==========================================
# IMPORT & EXPORT EXCEL — HELPERS (Update #5)
# ==========================================
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_streaming_response(wb, filename: str):
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        excel_service.workbook_to_stream(wb),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _stamp() -> str:
    """Timestamp filename YYYYMMDD-HHMM (waktu lokal server)."""
    return datetime.now().strftime("%Y%m%d-%H%M")


def _akun_label(akun) -> str:
    """Label akun 'kode - nama' untuk kolom export; kosong bila tak ter-link."""
    return f"{akun.kode} - {akun.nama}" if akun else ""


def _norm_header(value) -> str:
    """Normalisasi nama kolom header: trim, buang tanda wajib '*', lowercase."""
    return str(value or "").strip().strip("*").strip().lower()


def _cell_str(value):
    """Nilai sel sebagai string trim; None/kosong → None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_decimal_text(text: str) -> Decimal:
    """Parse teks angka toleran (Rp, pemisah ribuan '.',/',' , desimal ',')."""
    cleaned = text.replace("Rp", "").replace(" ", "").strip()
    if "." in cleaned and "," in cleaned:
        # 1.234.567,89 → 1234567.89 ('.' ribuan, ',' desimal)
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        # 1.234,89 → 1.234.89 (desimal); 1,234 → 1234 (ribuan US)
        cleaned = f"{head}.{tail}" if len(tail) in (1, 2) else cleaned.replace(",", "")
    return Decimal(cleaned)


def _cell_decimal(value, default=None):
    """Sel → Decimal; None/kosong → default; format salah → ValueError (error baris)."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("nilai harus angka")
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return default
    try:
        return _parse_decimal_text(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"'{value}' bukan angka yang valid") from exc


def _cell_int(value, default=0):
    """Sel → bilangan bulat; None/kosong → default; pecahan/format salah → ValueError."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("nilai harus bilangan bulat")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not float(value).is_integer():
            raise ValueError("nilai harus bilangan bulat")
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    number = _cell_decimal(text)
    if number != number.to_integral_value():
        raise ValueError("nilai harus bilangan bulat")
    return int(number)


def _load_import_sheet(content: bytes):
    """Baca file xlsx upload → (header_map, data_rows).

    - Sheet PERTAMA; baris pertama = header (dicocokkan berdasar NAMA kolom,
      bukan posisi — toleran terhadap urutan; nama dinormalisasi: buang '*',
      lowercase).
    - File bukan xlsx / rusak → HTTPException 400.
    """
    from openpyxl import load_workbook
    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="File harus berupa Excel .xlsx yang valid") from exc
    try:
        if not wb.worksheets:
            raise HTTPException(status_code=400, detail="File Excel tidak memiliki sheet data")
        ws = wb.worksheets[0]
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    if not rows:
        return {}, []
    header_map: dict[str, int] = {}
    for idx, name in enumerate(rows[0]):
        key = _norm_header(name)
        if key and key not in header_map:
            header_map[key] = idx
    return header_map, rows[1:]


def _get_col(raw: tuple, header_map: dict, key: str):
    """Ambil nilai sel berdasar nama kolom normalisasi; None bila kolom tak ada."""
    idx = header_map.get(key)
    if idx is None or idx >= len(raw):
        return None
    return raw[idx]


def _import_error(exc) -> str:
    """Pesan ramah untuk error satu baris import."""
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc)


# ==========================================
# PELANGGAN ENDPOINTS
# ==========================================
@router.get("/pelanggan", response_model=PaginatedResponse[PelangganResponse])
def get_pelanggan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="Cari berdasarkan nama atau kode"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Pelanggan, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/pelanggan", response_model=PelangganResponse, status_code=status.HTTP_201_CREATED)
def create_pelanggan(
    data_in: PelangganCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    # Validasi akun induk piutang SEBELUM create supaya pelanggan tidak
    # tersimpan setengah jalan kalau parent-nya invalid.
    from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA
    if data_in.akun_piutang_parent_id and not data_in.akun_piutang_id:
        parent = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == data_in.akun_piutang_parent_id).first()
        if not parent or parent.status != "AKTIF":
            raise HTTPException(status_code=400, detail="Akun induk piutang tidak ditemukan atau tidak aktif")
        if parent.header != HeaderCOA.AKTIVA:
            raise HTTPException(
                status_code=400,
                detail=f"Akun induk piutang harus bertipe AKTIVA (akun {parent.kode} '{parent.nama}' bertipe {parent.header.value})",
            )
    pelanggan = master_service.create_master(db, Pelanggan, data_in)
    # Kalau akun_piutang_id sudah diisi manual (link ke COA existing), skip auto-create
    if not pelanggan.akun_piutang_id:
        piutang_coa_id = auto_create_piutang_coa(db, pelanggan, parent_id=data_in.akun_piutang_parent_id)
        if piutang_coa_id:
            pelanggan.akun_piutang_id = piutang_coa_id
            db.add(pelanggan)
            db.commit()
            db.refresh(pelanggan)
    return pelanggan


# ── Pelanggan: Export & Import Excel (Update #5) ──────────────────────────
@router.get("/pelanggan/export")
def export_pelanggan(
    search: str | None = Query(None, description="Cari berdasarkan nama atau kode"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export seluruh pelanggan (urut kode) ke file .xlsx."""
    query = db.query(Pelanggan)
    if search:
        query = query.filter(or_(Pelanggan.nama.ilike(f"%{search}%"), Pelanggan.kode.ilike(f"%{search}%")))
    rows = (
        query.options(joinedload(Pelanggan.akun_piutang), joinedload(Pelanggan.syarat_bayar))
        .order_by(Pelanggan.kode)
        .all()
    )
    data = [
        [
            p.kode, p.nama, p.alamat or "", p.telepon or "", p.email or "",
            p.kontak_person or "", p.npwp or "", p.nitku or "", p.tax_status or "",
            p.credit_limit, p.syarat_bayar.nama if p.syarat_bayar else "",
            p.status, _akun_label(p.akun_piutang),
        ]
        for p in rows
    ]
    wb = excel_service.workbook_from_rows(
        headers=["Kode", "Nama", "Alamat", "Telepon", "Email", "Kontak Person", "NPWP", "NITKU",
                 "Tax Status", "Credit Limit", "Syarat Bayar", "Status", "Akun Piutang"],
        rows=data, sheet="Data",
        decimal_columns={9},
    )
    return _xlsx_streaming_response(wb, f"pelanggan-{_stamp()}.xlsx")


@router.get("/pelanggan/import-template")
def pelanggan_import_template(
    current_user: Pengguna = Depends(get_current_user),
):
    """Template import pelanggan (.xlsx): sheet Data (header saja) + Petunjuk."""
    wb = excel_service.workbook_from_rows(
        headers=["Kode*", "Nama*", "Alamat", "Telepon", "Email", "Kontak Person",
                 "NPWP", "NITKU", "Tax Status", "Credit Limit"],
        rows=[], sheet="Data",
    )
    excel_service.add_instructions_sheet(wb, rows=[
        ["Kolom", "Wajib", "Tipe Data", "Keterangan"],
        ["Kode*", "Ya", "Teks (maks 20)", "Kode unik pelanggan. Contoh: PLG-001. Duplikat dengan pelanggan AKTIF existing ditolak per baris."],
        ["Nama*", "Ya", "Teks (maks 200)", "Nama pelanggan. Contoh: PT Maju Jaya."],
        ["Alamat", "Tidak", "Teks", "Alamat lengkap. Contoh: Jl. Sudirman No. 1, Jakarta."],
        ["Telepon", "Tidak", "Teks", "Contoh: 021-555123."],
        ["Email", "Tidak", "Teks", "Contoh: admin@majujaya.co.id."],
        ["Kontak Person", "Tidak", "Teks", "Nama PIC. Contoh: Budi Santoso."],
        ["NPWP", "Tidak", "Teks", "15 digit tanpa tanda baca. Contoh: 012345678901234."],
        ["NITKU", "Tidak", "Teks", "NITKU e-Faktur (16 digit). Opsional."],
        ["Tax Status", "Tidak", "Pilihan", "PKP / NON_PKP. Kosong diperbolehkan."],
        ["Credit Limit", "Tidak", "Angka desimal", "Batas kredit, angka tanpa pemisah ribuan. Contoh: 5000000. Kosong = tanpa limit."],
        ["Catatan", "", "", "Baris dengan Kode & Nama kosong dilewati. Urutan kolom bebas — pembacaan berdasar nama kolom. Error satu baris tidak menghentikan baris lain. Akun piutang 'Piutang - {Nama}' dibuat OTOMATIS untuk setiap pelanggan baru."],
    ])
    return _xlsx_streaming_response(wb, "template-import-pelanggan.xlsx")


@router.post("/pelanggan/import", response_model=ImportResult)
async def import_pelanggan(
    file: UploadFile = File(...),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Import pelanggan dari file .xlsx (jalur create sama dengan POST /pelanggan).

    Error per baris tidak menghentikan baris lain — ringkasan dikembalikan 200.
    File bukan .xlsx → 400.
    """
    content = await file.read()
    header_map, rows = _load_import_sheet(content)
    result = ImportResult(total_baris=0, sukses=0, gagal=0, errors=[])
    for offset, raw in enumerate(rows):
        excel_row = offset + 2  # baris 1 = header
        kode = _cell_str(_get_col(raw, header_map, "kode"))
        nama = _cell_str(_get_col(raw, header_map, "nama"))
        if not kode and not nama:
            continue  # baris kosong di-skip
        result.total_baris += 1
        try:
            if not kode:
                raise ValueError("Kode wajib diisi")
            if not nama:
                raise ValueError("Nama wajib diisi")
            credit_limit = _cell_decimal(_get_col(raw, header_map, "credit limit"), None)
            data_in = PelangganCreate(
                kode=kode, nama=nama,
                alamat=_cell_str(_get_col(raw, header_map, "alamat")),
                telepon=_cell_str(_get_col(raw, header_map, "telepon")),
                email=_cell_str(_get_col(raw, header_map, "email")),
                kontak_person=_cell_str(_get_col(raw, header_map, "kontak person")),
                npwp=_cell_str(_get_col(raw, header_map, "npwp")),
                nitku=_cell_str(_get_col(raw, header_map, "nitku")),
                tax_status=_cell_str(_get_col(raw, header_map, "tax status")),
                credit_limit=credit_limit,
            )
            # JALUR CREATE YANG SAMA dengan POST /pelanggan (auto COA piutang ikut jalan)
            pelanggan = master_service.create_master(db, Pelanggan, data_in)
            if not pelanggan.akun_piutang_id:
                piutang_coa_id = auto_create_piutang_coa(db, pelanggan)
                if piutang_coa_id:
                    pelanggan.akun_piutang_id = piutang_coa_id
                    db.add(pelanggan)
                    db.commit()
                    db.refresh(pelanggan)
            result.sukses += 1
        except (HTTPException, ValueError, IntegrityError) as exc:
            db.rollback()
            result.gagal += 1
            result.errors.append(ImportRowError(baris=excel_row, pesan=_import_error(exc)))
    return result


@router.get("/pelanggan/{pelanggan_id}", response_model=PelangganResponse)
def get_pelanggan_detail(
    pelanggan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    return item

@router.put("/pelanggan/{pelanggan_id}", response_model=PelangganResponse)
def update_pelanggan(
    pelanggan_id: UUID,
    data_in: PelangganUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    old_nama = item.nama
    item = master_service.update_master(db, item, data_in)
    # Sync nama COA piutang jika nama pelanggan berubah
    if data_in.nama and data_in.nama != old_nama and item.akun_piutang_id:
        from app.models.akun_perkiraan import AkunPerkiraan
        coa = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == item.akun_piutang_id).first()
        if coa:
            coa.nama = f"Piutang - {item.nama}"
            db.add(coa)
            db.commit()
            db.refresh(item)
    return item

@router.delete("/pelanggan/{pelanggan_id}", status_code=status.HTTP_200_OK)
def delete_pelanggan(
    pelanggan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Pelanggan sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Pelanggan berhasil dinonaktifkan"}

@router.get("/pelanggan-coa", response_model=list[PelangganCoaResponse])
def get_pelanggan_coa(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """
    Skenario B2: List COA DETAIL di bawah 'Piutang Usaha' (termasuk root
    itu sendiri kalau ber-level DETAIL — COA revisi v2 mis. 112000), LEFT JOIN
    ke Pelanggan (kalau sudah linked via akun_piutang_id).

    FIX: pelanggan yang ter-link langsung ke root DETAIL (mis. 112000) dan
    pelanggan yang belum punya akun piutang sama sekali tetap ditampilkan
    supaya tidak "hilang" dari master pelanggan (sebelumnya halaman bisa
    kosong padahal data pelanggan ada & dipakai modul penjualan).
    """
    from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun

    group = find_piutang_root_coa(db)

    rows: list = []
    if group:
        detail_ids = get_coa_detail_ids_under(db, group.id)
        # Root 'Piutang Usaha' yang ber-level DETAIL ikut dilist — konsisten
        # dengan POST /pelanggan-from-coa yang membolehkan link langsung ke
        # root (mis. pelanggan di-link ke 112000).
        if group.tingkat == TingkatAkun.DETAIL and group.id not in detail_ids:
            detail_ids = [group.id] + detail_ids
        if detail_ids:
            rows = (
                db.query(AkunPerkiraan, Pelanggan)
                .outerjoin(Pelanggan, Pelanggan.akun_piutang_id == AkunPerkiraan.id)
                .filter(AkunPerkiraan.id.in_(detail_ids))
                .order_by(AkunPerkiraan.kode)
                .all()
            )

    # Pelanggan tanpa akun piutang (belum di-link / auto-create gagal karena
    # root COA belum dikonfigurasi) tetap muncul sebagai baris tanpa akun.
    linked_pelanggan_ids = {pel.id for _, pel in rows if pel is not None}
    orphans = (
        db.query(Pelanggan)
        .filter(Pelanggan.akun_piutang_id.is_(None))
        .order_by(Pelanggan.kode)
        .all()
    )
    rows = rows + [(None, pel) for pel in orphans if pel.id not in linked_pelanggan_ids]

    return [
        {
            "coa_id": coa.id if coa else None,
            "kode": coa.kode if coa else None,
            "nama": coa.nama if coa else None,
            "pelanggan_id": pelanggan.id if pelanggan else None,
            "kode_pelanggan": pelanggan.kode if pelanggan else None,
            "nama_pelanggan": pelanggan.nama if pelanggan else None,
            "alamat": pelanggan.alamat if pelanggan else None,
            "telepon": pelanggan.telepon if pelanggan else None,
            "email": pelanggan.email if pelanggan else None,
            "kontak_person": pelanggan.kontak_person if pelanggan else None,
            "npwp": pelanggan.npwp if pelanggan else None,
            "syarat_bayar_default": pelanggan.syarat_bayar_default if pelanggan else None,
            "status": pelanggan.status if pelanggan else (coa.status if coa else "AKTIF"),
            "is_linked": pelanggan is not None,
        }
        for coa, pelanggan in rows
    ]

@router.post("/pelanggan-from-coa", response_model=PelangganResponse, status_code=status.HTTP_201_CREATED)
def create_pelanggan_from_coa(
    data_in: PelangganFromCoaCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """
    Skenario B2: Link COA Piutang existing (sudah di-import manual) ke pelanggan
    baru. TIDAK membuat COA baru — pakai coa_id yang dikirim frontend langsung.
    """
    from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun

    coa = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == data_in.coa_id).first()
    if not coa:
        raise HTTPException(status_code=404, detail="COA tidak ditemukan")
    if coa.tingkat != TingkatAkun.DETAIL:
        raise HTTPException(status_code=400, detail="COA yang dipilih harus level DETAIL")

    group = find_piutang_root_coa(db)
    # COA v2: root 'Piutang Usaha' bisa berlevel DETAIL (mis. 112000) — root itu
    # sendiri pun boleh di-link; selain itu harus descendant dari root.
    if not group or coa.id not in ([group.id] + get_coa_detail_ids_under(db, group.id)):
        raise HTTPException(status_code=400, detail="COA yang dipilih bukan bagian dari 'Piutang Usaha'")

    existing_link = db.query(Pelanggan).filter(Pelanggan.akun_piutang_id == coa.id).first()
    if existing_link:
        raise HTTPException(
            status_code=400,
            detail=f"COA ini sudah terhubung ke pelanggan '{existing_link.nama}' ({existing_link.kode})"
        )

    pelanggan = Pelanggan(
        kode=data_in.kode,
        nama=data_in.nama,
        alamat=data_in.alamat,
        telepon=data_in.telepon,
        email=data_in.email,
        kontak_person=data_in.kontak_person,
        npwp=data_in.npwp,
        syarat_bayar_default=data_in.syarat_bayar_default,
        akun_piutang_id=coa.id,
    )
    db.add(pelanggan)
    db.commit()
    db.refresh(pelanggan)
    return pelanggan


# ==========================================
# SUPPLIER ENDPOINTS
# ==========================================
@router.get("/supplier", response_model=PaginatedResponse[SupplierResponse])
def get_supplier_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Supplier, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/supplier", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
def create_supplier(
    data_in: SupplierCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    # Validasi akun induk hutang SEBELUM create (mirror create_pelanggan).
    from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA
    if data_in.akun_hutang_parent_id and not data_in.akun_hutang_id:
        parent = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == data_in.akun_hutang_parent_id).first()
        if not parent or parent.status != "AKTIF":
            raise HTTPException(status_code=400, detail="Akun induk hutang tidak ditemukan atau tidak aktif")
        if parent.header != HeaderCOA.KEWAJIBAN:
            raise HTTPException(
                status_code=400,
                detail=f"Akun induk hutang harus bertipe KEWAJIBAN (akun {parent.kode} '{parent.nama}' bertipe {parent.header.value})",
            )
    supplier = master_service.create_master(db, Supplier, data_in)
    # Kalau akun_hutang_id sudah diisi manual (link ke COA existing), skip auto-create
    if not supplier.akun_hutang_id:
        hutang_coa_id = auto_create_hutang_coa(db, supplier, parent_id=data_in.akun_hutang_parent_id)
        if hutang_coa_id:
            supplier.akun_hutang_id = hutang_coa_id
            db.add(supplier)
            db.commit()
            db.refresh(supplier)
    return supplier


# ── Supplier: Export & Import Excel (Update #5) ───────────────────────────
@router.get("/supplier/export")
def export_supplier(
    search: str | None = Query(None, description="Cari berdasarkan nama atau kode"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export seluruh supplier (urut kode) ke file .xlsx."""
    query = db.query(Supplier)
    if search:
        query = query.filter(or_(Supplier.nama.ilike(f"%{search}%"), Supplier.kode.ilike(f"%{search}%")))
    rows = (
        query.options(joinedload(Supplier.akun_hutang))
        .order_by(Supplier.kode)
        .all()
    )
    data = [
        [
            s.kode, s.nama, s.alamat or "", s.telepon or "", s.email or "",
            s.kontak_person or "", s.npwp or "", s.nitku or "", s.supplier_type or "",
            s.city or "", s.province or "", s.country or "", s.postal_code or "",
            s.currency or "", s.bank_name or "", s.bank_account_no or "",
            s.bank_account_name or "", s.status, _akun_label(s.akun_hutang),
        ]
        for s in rows
    ]
    wb = excel_service.workbook_from_rows(
        headers=["Kode", "Nama", "Alamat", "Telepon", "Email", "Kontak Person", "NPWP", "NITKU",
                 "Supplier Type", "City", "Province", "Country", "Postal Code", "Currency",
                 "Bank Name", "Bank Account No", "Bank Account Name", "Status", "Akun Hutang"],
        rows=data, sheet="Data",
    )
    return _xlsx_streaming_response(wb, f"supplier-{_stamp()}.xlsx")


@router.get("/supplier/import-template")
def supplier_import_template(
    current_user: Pengguna = Depends(get_current_user),
):
    """Template import supplier (.xlsx): sheet Data (header saja) + Petunjuk."""
    wb = excel_service.workbook_from_rows(
        headers=["Kode*", "Nama*", "Alamat", "Telepon", "Email", "Kontak Person",
                 "NPWP", "NITKU", "Supplier Type", "City", "Province", "Country",
                 "Postal Code", "Currency", "Bank Name", "Bank Account No", "Bank Account Name"],
        rows=[], sheet="Data",
    )
    excel_service.add_instructions_sheet(wb, rows=[
        ["Kolom", "Wajib", "Tipe Data", "Keterangan"],
        ["Kode*", "Ya", "Teks (maks 20)", "Kode unik supplier. Contoh: SUP-001. Duplikat dengan supplier AKTIF existing ditolak per baris."],
        ["Nama*", "Ya", "Teks (maks 200)", "Nama supplier. Contoh: CV Sumber Rejeki."],
        ["Alamat", "Tidak", "Teks", "Alamat lengkap. Contoh: Jl. Industri No. 10, Bandung."],
        ["Telepon", "Tidak", "Teks", "Contoh: 022-555987."],
        ["Email", "Tidak", "Teks", "Contoh: sales@sumberrejeki.co.id."],
        ["Kontak Person", "Tidak", "Teks", "Nama PIC. Contoh: Andi Wijaya."],
        ["NPWP", "Tidak", "Teks", "15 digit tanpa tanda baca. Contoh: 012345678901234."],
        ["NITKU", "Tidak", "Teks", "NITKU e-Faktur (16 digit). Opsional."],
        ["Supplier Type", "Ya", "Pilihan", "Wajib diisi: COMPANY / INDIVIDUAL. Selain itu (termasuk kosong) → baris ditolak."],
        ["City", "Tidak", "Teks", "Kota. Contoh: Bandung."],
        ["Province", "Tidak", "Teks", "Provinsi. Contoh: Jawa Barat."],
        ["Country", "Tidak", "Teks", "Negara. Contoh: Indonesia."],
        ["Postal Code", "Tidak", "Teks", "Kode pos. Contoh: 40123."],
        ["Currency", "Tidak", "Teks (3 huruf)", "Kode mata uang ISO 4217. Contoh: IDR (default)."],
        ["Bank Name", "Tidak", "Teks", "Nama bank. Contoh: Bank BCA."],
        ["Bank Account No", "Tidak", "Teks", "Nomor rekening. Contoh: 1234567890."],
        ["Bank Account Name", "Tidak", "Teks", "Nama pemilik rekening. Contoh: CV Sumber Rejeki."],
        ["Catatan", "", "", "Baris dengan Kode & Nama kosong dilewati. Urutan kolom bebas — pembacaan berdasar nama kolom. Error satu baris tidak menghentikan baris lain. Akun hutang 'Hutang - {Nama}' dibuat OTOMATIS untuk setiap supplier baru."],
    ])
    return _xlsx_streaming_response(wb, "template-import-supplier.xlsx")


@router.post("/supplier/import", response_model=ImportResult)
async def import_supplier(
    file: UploadFile = File(...),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Import supplier dari file .xlsx (jalur create sama dengan POST /supplier).

    Error per baris tidak menghentikan baris lain — ringkasan dikembalikan 200.
    File bukan .xlsx → 400.
    """
    content = await file.read()
    header_map, rows = _load_import_sheet(content)
    result = ImportResult(total_baris=0, sukses=0, gagal=0, errors=[])
    for offset, raw in enumerate(rows):
        excel_row = offset + 2  # baris 1 = header
        kode = _cell_str(_get_col(raw, header_map, "kode"))
        nama = _cell_str(_get_col(raw, header_map, "nama"))
        if not kode and not nama:
            continue  # baris kosong di-skip
        result.total_baris += 1
        try:
            if not kode:
                raise ValueError("Kode wajib diisi")
            if not nama:
                raise ValueError("Nama wajib diisi")
            supplier_type = _cell_str(_get_col(raw, header_map, "supplier type"))
            if not supplier_type or supplier_type.upper() not in ("COMPANY", "INDIVIDUAL"):
                raise ValueError("Supplier Type wajib diisi: COMPANY / INDIVIDUAL")
            currency = _cell_str(_get_col(raw, header_map, "currency"))
            data_in = SupplierCreate(
                kode=kode, nama=nama, supplier_type=supplier_type.upper(),
                alamat=_cell_str(_get_col(raw, header_map, "alamat")),
                telepon=_cell_str(_get_col(raw, header_map, "telepon")),
                email=_cell_str(_get_col(raw, header_map, "email")),
                kontak_person=_cell_str(_get_col(raw, header_map, "kontak person")),
                npwp=_cell_str(_get_col(raw, header_map, "npwp")),
                nitku=_cell_str(_get_col(raw, header_map, "nitku")),
                city=_cell_str(_get_col(raw, header_map, "city")),
                province=_cell_str(_get_col(raw, header_map, "province")),
                country=_cell_str(_get_col(raw, header_map, "country")),
                postal_code=_cell_str(_get_col(raw, header_map, "postal code")),
                currency=currency or "IDR",
                bank_name=_cell_str(_get_col(raw, header_map, "bank name")),
                bank_account_no=_cell_str(_get_col(raw, header_map, "bank account no")),
                bank_account_name=_cell_str(_get_col(raw, header_map, "bank account name")),
            )
            # JALUR CREATE YANG SAMA dengan POST /supplier (auto COA hutang ikut jalan)
            supplier = master_service.create_master(db, Supplier, data_in)
            if not supplier.akun_hutang_id:
                hutang_coa_id = auto_create_hutang_coa(db, supplier)
                if hutang_coa_id:
                    supplier.akun_hutang_id = hutang_coa_id
                    db.add(supplier)
                    db.commit()
                    db.refresh(supplier)
            result.sukses += 1
        except (HTTPException, ValueError, IntegrityError) as exc:
            db.rollback()
            result.gagal += 1
            result.errors.append(ImportRowError(baris=excel_row, pesan=_import_error(exc)))
    return result


@router.get("/supplier/{supplier_id}", response_model=SupplierResponse)
def get_supplier_detail(supplier_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    return item

@router.put("/supplier/{supplier_id}", response_model=SupplierResponse)
def update_supplier(supplier_id: UUID, data_in: SupplierUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    old_nama = item.nama
    item = master_service.update_master(db, item, data_in)
    # Sync nama COA hutang jika nama supplier berubah
    if data_in.nama and data_in.nama != old_nama and item.akun_hutang_id:
        from app.models.akun_perkiraan import AkunPerkiraan
        coa = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == item.akun_hutang_id).first()
        if coa:
            coa.nama = f"Hutang - {item.nama}"
            db.add(coa)
            db.commit()
            db.refresh(item)
    return item

@router.delete("/supplier/{supplier_id}", status_code=status.HTTP_200_OK)
def delete_supplier(supplier_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Supplier sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Supplier berhasil dinonaktifkan"}

@router.get("/supplier-coa", response_model=list[SupplierCoaResponse])
def get_supplier_coa(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """
    Skenario B2: List COA DETAIL di bawah 'Hutang Usaha' (termasuk root
    itu sendiri kalau ber-level DETAIL — COA revisi v2 mis. 211000), LEFT JOIN
    ke Supplier (kalau sudah linked via akun_hutang_id). Mirror dari
    /pelanggan-coa — termasuk fix baris supplier tanpa akun hutang.
    """
    from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun

    group = find_hutang_root_coa(db)

    rows: list = []
    if group:
        detail_ids = get_coa_detail_ids_under(db, group.id)
        if group.tingkat == TingkatAkun.DETAIL and group.id not in detail_ids:
            detail_ids = [group.id] + detail_ids
        if detail_ids:
            rows = (
                db.query(AkunPerkiraan, Supplier)
                .outerjoin(Supplier, Supplier.akun_hutang_id == AkunPerkiraan.id)
                .filter(AkunPerkiraan.id.in_(detail_ids))
                .order_by(AkunPerkiraan.kode)
                .all()
            )

    linked_supplier_ids = {spl.id for _, spl in rows if spl is not None}
    orphans = (
        db.query(Supplier)
        .filter(Supplier.akun_hutang_id.is_(None))
        .order_by(Supplier.kode)
        .all()
    )
    rows = rows + [(None, spl) for spl in orphans if spl.id not in linked_supplier_ids]

    return [
        {
            "coa_id": coa.id if coa else None,
            "kode": coa.kode if coa else None,
            "nama": coa.nama if coa else None,
            "supplier_id": supplier.id if supplier else None,
            "kode_supplier": supplier.kode if supplier else None,
            "nama_supplier": supplier.nama if supplier else None,
            "alamat": supplier.alamat if supplier else None,
            "telepon": supplier.telepon if supplier else None,
            "email": supplier.email if supplier else None,
            "kontak_person": supplier.kontak_person if supplier else None,
            "npwp": supplier.npwp if supplier else None,
            "syarat_bayar_default": supplier.syarat_bayar_default if supplier else None,
            "status": supplier.status if supplier else (coa.status if coa else "AKTIF"),
            "is_linked": supplier is not None,
        }
        for coa, supplier in rows
    ]

@router.post("/supplier-from-coa", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
def create_supplier_from_coa(
    data_in: SupplierFromCoaCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """
    Skenario B2: Link COA Hutang existing (sudah di-import manual) ke supplier
    baru. TIDAK membuat COA baru — pakai coa_id yang dikirim frontend langsung.
    """
    from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun

    coa = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == data_in.coa_id).first()
    if not coa:
        raise HTTPException(status_code=404, detail="COA tidak ditemukan")
    if coa.tingkat != TingkatAkun.DETAIL:
        raise HTTPException(status_code=400, detail="COA yang dipilih harus level DETAIL")

    group = find_hutang_root_coa(db)
    # COA v2: root 'Hutang Usaha' bisa berlevel DETAIL (mis. 211000) — root itu
    # sendiri pun boleh di-link; selain itu harus descendant dari root.
    if not group or coa.id not in ([group.id] + get_coa_detail_ids_under(db, group.id)):
        raise HTTPException(status_code=400, detail="COA yang dipilih bukan bagian dari 'Hutang Usaha'")

    existing_link = db.query(Supplier).filter(Supplier.akun_hutang_id == coa.id).first()
    if existing_link:
        raise HTTPException(
            status_code=400,
            detail=f"COA ini sudah terhubung ke supplier '{existing_link.nama}' ({existing_link.kode})"
        )

    supplier = Supplier(
        kode=data_in.kode,
        nama=data_in.nama,
        alamat=data_in.alamat,
        telepon=data_in.telepon,
        email=data_in.email,
        kontak_person=data_in.kontak_person,
        npwp=data_in.npwp,
        syarat_bayar_default=data_in.syarat_bayar_default,
        akun_hutang_id=coa.id,
    )
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


# ==========================================
# BARANG ENDPOINTS
# ==========================================
@router.get('/barang-akun-persediaan', response_model=PaginatedResponse[COASimpleResponse])
def get_barang_inventory_accounts(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
    search: str | None = None, item_type: str | None = Query(None,
        description="Filter per jenis item: BARANG_DAGANG/BARANG_JADI -> INVENTORY_FINISHED, "
                    "BARANG_BAKU -> INVENTORY_RAW, BARANG_BANTU -> INVENTORY_AUX, JASA -> kosong. "
                    "Tanpa param = semua akun persediaan."),
    db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    from app.models.akun_perkiraan import AkunPerkiraan
    from sqlalchemy import or_
    query = master_service.inventory_account_candidates(db, item_type=item_type)
    if search:
        query = query.filter(or_(AkunPerkiraan.kode.ilike(f'%{search}%'), AkunPerkiraan.nama.ilike(f'%{search}%')))
    total = query.count()
    return {'data': query.order_by(AkunPerkiraan.kode, AkunPerkiraan.id).offset(skip).limit(limit).all(),
            'total': total, 'skip': skip, 'limit': limit}


class AkunPilihanItemResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class BarangAkunPilihanResponse(BaseSchema):
    """Pilihan akun untuk picker COA di form Barang & Jasa (Update #3)."""
    hpp: list[AkunPilihanItemResponse]
    penjualan: list[AkunPilihanItemResponse]
    retur: list[AkunPilihanItemResponse]
    diskon: list[AkunPilihanItemResponse]


@router.get('/barang-akun-pilihan', response_model=BarangAkunPilihanResponse)
def get_barang_akun_pilihan(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar pilihan akun per kategori mapping barang (Update #3).

    - hpp      : akun COGS/COGS tingkat DETAIL AKTIF (mis. 531001).
    - penjualan: akun REVENUE/OPERATING_REVENUE system SALES, dikecualikan akun
                 yang di-map ke RETUR_PENJUALAN & POTONGAN_PENJUALAN (411001-411003).
    - retur    : akun setting_akun RETUR_PENJUALAN (mis. 411004).
    - diskon   : akun setting_akun POTONGAN_PENJUALAN (mis. 411005).
    """
    return master_service.barang_akun_pilihan(db)


@router.get("/barang", response_model=PaginatedResponse[BarangResponse])
def get_barang_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Barang, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/barang", response_model=BarangResponse, status_code=status.HTTP_201_CREATED)
def create_barang(
    data_in: BarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Barang, data_in)


# ── Barang: Export & Import Excel (Update #5) ─────────────────────────────
@router.get("/barang/export")
def export_barang(
    search: str | None = Query(None, description="Cari berdasarkan nama atau kode"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export seluruh barang (urut kode) ke file .xlsx."""
    query = db.query(Barang)
    if search:
        query = query.filter(or_(Barang.nama.ilike(f"%{search}%"), Barang.kode.ilike(f"%{search}%")))
    rows = (
        query.options(
            joinedload(Barang.kategori), joinedload(Barang.satuan),
            joinedload(Barang.akun_persediaan), joinedload(Barang.akun_hpp),
            joinedload(Barang.akun_penjualan), joinedload(Barang.akun_retur_penjualan),
            joinedload(Barang.akun_diskon_penjualan),
        )
        .order_by(Barang.kode)
        .all()
    )
    data = [
        [
            b.kode, b.nama,
            b.kategori.nama if b.kategori else "",
            b.satuan.nama if b.satuan else "",
            b.item_type.value if b.item_type else "",
            b.metode_valuasi.value if b.metode_valuasi else "",
            "Ya" if b.stock_item else "Tidak",
            b.stok, b.stok_minimum, b.harga_pokok, b.harga_jual,
            b.status,
            _akun_label(b.akun_persediaan), _akun_label(b.akun_hpp),
            _akun_label(b.akun_penjualan), _akun_label(b.akun_retur_penjualan),
            _akun_label(b.akun_diskon_penjualan),
        ]
        for b in rows
    ]
    wb = excel_service.workbook_from_rows(
        headers=["Kode", "Nama", "Kategori", "Satuan", "Tipe Barang", "Metode Valuasi",
                 "Stok Item", "Stok", "Stok Minimum", "Harga Pokok", "Harga Jual", "Status",
                 "Akun Persediaan", "Akun HPP", "Akun Penjualan", "Akun Retur Penjualan",
                 "Akun Diskon Penjualan"],
        rows=data, sheet="Data",
        number_columns={7, 8},
        decimal_columns={9, 10},
    )
    return _xlsx_streaming_response(wb, f"barang-{_stamp()}.xlsx")


@router.get("/barang/import-template")
def barang_import_template(
    current_user: Pengguna = Depends(get_current_user),
):
    """Template import barang (.xlsx): sheet Data (header saja) + Petunjuk.

    Update #12: kolom "Gudang" + "Stok" — stok awal kini bisa di-set langsung
    lewat import (diterapkan lewat dokumen Penyesuaian Stok otomatis).
    """
    wb = excel_service.workbook_from_rows(
        headers=["Kode*", "Nama*", "Kategori*", "Satuan*", "Tipe Barang",
                 "Gudang", "Stok", "Stok Minimum", "Harga Pokok", "Harga Jual"],
        rows=[], sheet="Data",
    )
    excel_service.add_instructions_sheet(wb, rows=[
        ["Kolom", "Wajib", "Tipe Data", "Keterangan"],
        ["Kode*", "Ya", "Teks (maks 20)", "Kode unik barang. Contoh: BRG-0101. Baris dengan Kode duplikat barang AKTIF existing ditolak."],
        ["Nama*", "Ya", "Teks (maks 200)", "Nama barang. Contoh: Buku Tulis A5 38 Lembar."],
        ["Kategori*", "Ya", "Teks", "NAMA kategori PERSIS seperti master kategori (huruf besar/kecil diabaikan). Contoh: Alat Tulis. Tidak ditemukan → baris ditolak."],
        ["Satuan*", "Ya", "Teks", "NAMA satuan PERSIS seperti master satuan (huruf besar/kecil diabaikan). Contoh: PCS. Tidak ditemukan → baris ditolak."],
        ["Tipe Barang", "Tidak", "Pilihan", "BARANG_DAGANG / BARANG_JADI / BARANG_BAKU / BARANG_BANTU / JASA. Kosong = BARANG_DAGANG. Nilai lain → baris ditolak."],
        ["Gudang", "Wajib bila Stok > 0", "Teks", "NAMA atau KODE gudang PERSIS seperti master gudang (huruf besar/kecil diabaikan). Contoh: Gudang Utama. Bila Stok > 0 dan kolom kosong: otomatis dipakai bila hanya ada 1 gudang aktif; bila lebih dari 1 → baris ditolak."],
        ["Stok", "Tidak", "Bilangan bulat ≥ 0", "Stok awal barang di gudang. Contoh: 100. Kosong / 0 = tidak dibuat penyesuaian. Stok diterapkan lewat dokumen Penyesuaian Stok (TAMBAH) otomatis: import oleh Administrator langsung disetujui (stok + jurnal tercatat); oleh role lain menjadi DIAJUKAN dan menunggu persetujuan. Tipe JASA tidak boleh mengisi Stok. Nilai stok memakai Harga Pokok sebagai biaya satuan."],
        ["Stok Minimum", "Tidak", "Bilangan bulat", "Batas stok minimum. Contoh: 10. Kosong = 0."],
        ["Harga Pokok", "Tidak", "Angka desimal", "Harga pokok per satuan, angka TANPA pemisah ribuan. Contoh: 25000. Kosong = 0. Dipakai juga sebagai biaya satuan stok awal (jurnal otomatis bila stok > 0 dan nilai > 0)."],
        ["Harga Jual", "Tidak", "Angka desimal", "Harga jual per satuan, angka TANPA pemisah ribuan. Contoh: 35000. Kosong = 0."],
        ["Catatan", "", "", "Baris dengan Kode & Nama kosong dilewati. Urutan kolom bebas — pembacaan berdasar nama kolom (bukan posisi). Error satu baris tidak menghentikan baris lain. Metode valuasi FEFO: stok awal TIDAK bisa diimport (butuh tanggal kedaluwarsa) — kosongkan Stok lalu tambahkan lewat menu Penyesuaian Stok. Barang NONAKTIF yang diaktifkan kembali tetap mempertahankan stok lamanya — kosongkan Stok untuk baris seperti ini."],
    ])
    return _xlsx_streaming_response(wb, "template-import-barang.xlsx")


@router.post("/barang/import", response_model=ImportResult)
async def import_barang(
    file: UploadFile = File(...),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Import barang dari file .xlsx (jalur create sama dengan POST /barang).

    Mapping Kategori/Satuan berdasar NAMA (case-insensitive) terhadap tabel
    master. Error per baris tidak menghentikan baris lain — ringkasan
    dikembalikan 200. File bukan .xlsx → 400.

    Update #12 — kolom "Stok" (opsional): stok awal diterapkan lewat dokumen
    Penyesuaian Stok TAMBAH + finalisasi workflow (direct_complete) — jalur
    yang SAMA dengan menu Penyesuaian Stok manual, sehingga saldo gudang,
    kartu stok/mutasi, dan jurnal (D Persediaan, K Selisih Persediaan bila
    nilai > 0) tetap tercatat. Administrator: dokumen langsung DISETUJUI
    (stok + jurnal diterapkan); role lain: tetap DIAJUKAN (menunggu
    persetujuan) dan baris dilaporkan gagal dengan pesan penjelas.
    Kolom "Gudang" (nama/kode) menentukan lokasi stok awal; bila kosong
    dipakai satu-satunya gudang aktif (bila ada).
    """
    content = await file.read()
    header_map, rows = _load_import_sheet(content)
    kategori_map = {
        k.nama.strip().lower(): k
        for k in db.query(KategoriBarang).filter(KategoriBarang.status == "AKTIF").all()
    }
    satuan_map = {
        s.nama.strip().lower(): s
        for s in db.query(Satuan).filter(Satuan.status == "AKTIF").all()
    }
    # Gudang aktif: dipetakan berdasar NAMA dan KODE (case-insensitive).
    gudang_aktif = (
        db.query(Gudang).filter(Gudang.status == "AKTIF").order_by(Gudang.kode).all()
    )
    gudang_map: dict = {}
    for g in gudang_aktif:
        gudang_map.setdefault(g.nama.strip().lower(), g)
        gudang_map.setdefault(g.kode.strip().lower(), g)
    satu_gudang = gudang_aktif[0] if len(gudang_aktif) == 1 else None
    from app.services import app_setting_service
    metode_valuasi_global = app_setting_service.get_metode_valuasi(db)

    result = ImportResult(total_baris=0, sukses=0, gagal=0, errors=[])
    for offset, raw in enumerate(rows):
        excel_row = offset + 2  # baris 1 = header
        kode = _cell_str(_get_col(raw, header_map, "kode"))
        nama = _cell_str(_get_col(raw, header_map, "nama"))
        if not kode and not nama:
            continue  # baris kosong di-skip
        result.total_baris += 1
        try:
            if not kode:
                raise ValueError("Kode wajib diisi")
            if not nama:
                raise ValueError("Nama wajib diisi")
            kategori_nama = _cell_str(_get_col(raw, header_map, "kategori"))
            if not kategori_nama:
                raise ValueError("Kategori wajib diisi")
            kategori = kategori_map.get(kategori_nama.strip().lower())
            if kategori is None:
                raise ValueError(f"Kategori '{kategori_nama}' tidak ditemukan di master kategori")
            satuan_nama = _cell_str(_get_col(raw, header_map, "satuan"))
            if not satuan_nama:
                raise ValueError("Satuan wajib diisi")
            satuan = satuan_map.get(satuan_nama.strip().lower())
            if satuan is None:
                raise ValueError(f"Satuan '{satuan_nama}' tidak ditemukan di master satuan")
            tipe_text = _cell_str(_get_col(raw, header_map, "tipe barang"))
            if tipe_text:
                try:
                    item_type = ItemTypeBarang(tipe_text.strip().upper())
                except ValueError as exc:
                    raise ValueError(
                        f"Tipe Barang '{tipe_text}' tidak valid "
                        "(pilih BARANG_DAGANG / BARANG_JADI / BARANG_BAKU / BARANG_BANTU / JASA)"
                    ) from exc
            else:
                item_type = ItemTypeBarang.BARANG_DAGANG  # default bila kosong

            # ── Update #12: stok awal + gudang (divalidasi SEBELUM create
            #    supaya barang tidak tertinggal setengah jalan bila stok
            #    ditolak) ─────────────────────────────────────────────────
            stok_awal = _cell_int(_get_col(raw, header_map, "stok"), 0)
            if stok_awal < 0:
                raise ValueError("Stok tidak boleh negatif")
            if item_type == ItemTypeBarang.JASA and stok_awal > 0:
                raise ValueError("Tipe JASA tidak menyimpan stok — kosongkan kolom Stok")
            gudang_tujuan = None
            if stok_awal > 0:
                if metode_valuasi_global == "FEFO":
                    raise ValueError(
                        "Metode valuasi FEFO aktif — stok awal tidak bisa diimport karena "
                        "butuh tanggal kedaluwarsa. Kosongkan Stok, lalu tambahkan lewat "
                        "menu Penyesuaian Stok."
                    )
                gudang_text = _cell_str(_get_col(raw, header_map, "gudang"))
                if gudang_text:
                    gudang_tujuan = gudang_map.get(gudang_text.strip().lower())
                    if gudang_tujuan is None:
                        raise ValueError(
                            f"Gudang '{gudang_text}' tidak ditemukan di master gudang (aktif)"
                        )
                elif satu_gudang is not None:
                    gudang_tujuan = satu_gudang
                elif not gudang_aktif:
                    raise ValueError(
                        "Belum ada gudang aktif — buat master Gudang dulu sebelum import stok"
                    )
                else:
                    raise ValueError(
                        "Ada lebih dari satu gudang aktif — isi kolom Gudang "
                        "(nama/kode) untuk menentukan lokasi stok awal"
                    )
                # Reaktivasi NONAKTIF dengan riwayat stok: create_master
                # mempertahankan stok lama — menimpa dengan penyesuaian baru
                # akan menggandakan stok, jadi tolak sejak awal.
                lama = (
                    db.query(Barang)
                    .filter(Barang.kode == kode, Barang.status == "NONAKTIF")
                    .order_by(Barang.created_at.desc(), Barang.id.desc())
                    .first()
                )
                if lama is not None and (
                    db.query(StockBalance).filter_by(barang_id=lama.id).first() is not None
                    or db.query(StokMutasi).filter_by(barang_id=lama.id).first() is not None
                ):
                    raise ValueError(
                        f"Kode '{kode}' adalah barang NONAKTIF dengan riwayat stok "
                        "(stok lama dipertahankan sistem) — kosongkan kolom Stok, "
                        "atur stok lewat menu Penyesuaian Stok"
                    )

            harga_pokok = _cell_decimal(_get_col(raw, header_map, "harga pokok"), Decimal("0"))
            data_in = BarangCreate(
                kode=kode, nama=nama,
                kategori_id=kategori.id, satuan_id=satuan.id,
                item_type=item_type,
                stok_minimum=_cell_int(_get_col(raw, header_map, "stok minimum"), 0),
                harga_pokok=harga_pokok,
                harga_jual=_cell_decimal(_get_col(raw, header_map, "harga jual"), Decimal("0")),
            )
            # JALUR CREATE YANG SAMA dengan POST /barang — policy item_type/akun
            # divalidasi identik di master_service.create_master.
            item = master_service.create_master(db, Barang, data_in)

            # ── Update #12: terapkan stok awal lewat jalur Penyesuaian Stok
            #    (sama dengan endpoint POST /persediaan/penyesuaian-stok:
            #    create + direct_complete admin) ───────────────────────────
            if stok_awal > 0 and gudang_tujuan is not None:
                try:
                    adj = persediaan_service.create_penyesuaian(
                        db=db,
                        tanggal=datetime.now(timezone.utc),
                        barang_id=item.id,
                        tipe="TAMBAH",
                        qty=stok_awal,
                        biaya_satuan=harga_pokok,
                        alasan=f"Stok awal import barang {kode}",
                        auto_post_jurnal=True,
                        created_by=current_user.id,
                        gudang_id=gudang_tujuan.id,
                    )
                    workflow_service.direct_complete(
                        db, current_user, "penyesuaian_stok", adj.id
                    )
                    db.refresh(adj)
                    # Status sukses penyesuaian = DISETUJUI (stok + jurnal
                    # sudah diterapkan approve_penyesuaian). DIAJUKAN berarti
                    # import oleh non-admin → menunggu persetujuan manual.
                    if adj.status == StatusPersediaan.DIAJUKAN:
                        raise ValueError(
                            f"Barang tersimpan, tetapi penyesuaian stok awal {adj.no_adj} "
                            "menunggu persetujuan — setujui di menu Persediaan ▸ "
                            "Penyesuaian Stok agar stok masuk"
                        )
                    if adj.status != StatusPersediaan.DISETUJUI:
                        raise ValueError(
                            f"Penyesuaian stok awal {adj.no_adj} berakhir dengan status "
                            f"{adj.status.value} — periksa dokumen di menu Penyesuaian Stok"
                        )
                except HTTPException:
                    raise
                except (ValueError, IntegrityError) as exc:
                    raise ValueError(
                        f"Barang '{kode}' tersimpan, tetapi penyesuaian stok awal gagal: "
                        f"{_import_error(exc)}. Atur stok manual lewat menu Penyesuaian Stok."
                    ) from exc
            result.sukses += 1
        except (HTTPException, ValueError, IntegrityError) as exc:
            db.rollback()
            result.gagal += 1
            result.errors.append(ImportRowError(baris=excel_row, pesan=_import_error(exc)))
    return result


@router.get("/barang/types")
def get_barang_types(current_user: Pengguna = Depends(get_current_user)):
    """Daftar jenis item (UI type) + policy-nya untuk form dinamis barang.

    Spec §6 (items/types): tipe, capabilities, required fields, visible tabs,
    account fields. Route ini HARUS terdaftar sebelum /barang/{barang_id}
    agar "types" tidak tertelan path parameter UUID.
    """
    from app.services.item_type_policy import types_payload
    return types_payload()


@router.get("/barang/{barang_id}", response_model=BarangResponse)
def get_barang_detail(barang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    return item

@router.put("/barang/{barang_id}", response_model=BarangResponse)
def update_barang(barang_id: UUID, data_in: BarangUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/barang/{barang_id}", status_code=status.HTTP_200_OK)
def delete_barang(barang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Barang sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Barang berhasil dinonaktifkan"}


# ==========================================
# BARANG SATUAN ENDPOINTS (Multi-satuan)
# ==========================================
@router.get("/barang/{barang_id}/satuan", response_model=list[BarangSatuanResponse])
def get_barang_satuan_list(
    barang_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Get daftar satuan untuk suatu barang (termasuk satuan utama dari barang.satuan_id)."""
    barang = master_service.get_master_by_id(db, Barang, barang_id)
    if not barang:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    return db.query(BarangSatuan).filter(
        BarangSatuan.barang_id == barang_id
    ).order_by(BarangSatuan.is_utama.desc()).all()


@router.post("/barang/{barang_id}/satuan", response_model=BarangSatuanResponse, status_code=status.HTTP_201_CREATED)
def add_barang_satuan(
    barang_id: UUID,
    data_in: BarangSatuanCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Tambah satuan ke daftar satuan barang."""
    barang = master_service.get_master_by_id(db, Barang, barang_id)
    if not barang:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    if barang_id != data_in.barang_id:
        raise HTTPException(status_code=400, detail="barang_id di path dan body tidak cocok")
    # Cek duplikat satuan
    existing = db.query(BarangSatuan).filter(
        BarangSatuan.barang_id == barang_id,
        BarangSatuan.satuan_id == data_in.satuan_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Satuan ini sudah terdaftar untuk barang tersebut")
    return master_service.create_master(db, BarangSatuan, data_in)


@router.put("/barang-satuan/{barang_satuan_id}", response_model=BarangSatuanResponse)
def update_barang_satuan(
    barang_satuan_id: UUID,
    data_in: BarangSatuanUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Update satuan pada daftar satuan barang (mis. ubah isi_satuan / faktor konversi)."""
    item = db.query(BarangSatuan).filter(BarangSatuan.id == barang_satuan_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Barang Satuan tidak ditemukan")
    # Cegah duplikat kalau satuan_id diganti ke satuan yang sudah dipakai barang ini
    if data_in.satuan_id and data_in.satuan_id != item.satuan_id:
        existing = db.query(BarangSatuan).filter(
            BarangSatuan.barang_id == item.barang_id,
            BarangSatuan.satuan_id == data_in.satuan_id,
            BarangSatuan.id != barang_satuan_id,
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Satuan ini sudah terdaftar untuk barang tersebut")
    return master_service.update_master(db, item, data_in)


@router.delete("/barang-satuan/{barang_satuan_id}", status_code=status.HTTP_200_OK)
def delete_barang_satuan(
    barang_satuan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Hapus satuan dari daftar satuan barang."""
    item = db.query(BarangSatuan).filter(BarangSatuan.id == barang_satuan_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Barang Satuan tidak ditemukan")
    if item.is_utama:
        raise HTTPException(status_code=400, detail="Satuan utama tidak bisa dihapus")
    db.delete(item)
    db.commit()
    return {"message": "Satuan berhasil dihapus dari barang"}


# ==========================================
# GUDANG ENDPOINTS
# ==========================================
@router.get("/gudang", response_model=list[GudangResponse])
def get_gudang_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return db.query(Gudang).filter(Gudang.status == "AKTIF").order_by(Gudang.nama).all()

@router.post("/gudang", response_model=GudangResponse, status_code=status.HTTP_201_CREATED)
def create_gudang(
    data_in: GudangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Gudang, data_in)

# ── Gudang: Export & Import Excel (Update #9) ───────────────────────────
@router.get("/gudang/export")
def export_gudang(
    search: str | None = Query(None, description="Cari berdasarkan nama atau kode"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export seluruh gudang (urut kode) ke file .xlsx — kolom Status & Total Barang
    hanya informasi (diabaikan saat import; pembacaan kolom berdasar nama)."""
    query = db.query(Gudang)
    if search:
        query = query.filter(or_(Gudang.nama.ilike(f"%{search}%"), Gudang.kode.ilike(f"%{search}%")))
    rows = query.order_by(Gudang.kode).all()
    data = [
        [g.kode, g.nama, g.alamat or "", g.status, g.total_barang or 0]
        for g in rows
    ]
    wb = excel_service.workbook_from_rows(
        headers=["Kode", "Nama", "Alamat", "Status", "Total Barang"],
        rows=data, sheet="Data",
        number_columns={4},
    )
    return _xlsx_streaming_response(wb, f"gudang-{_stamp()}.xlsx")


@router.get("/gudang/import-template")
def gudang_import_template(
    current_user: Pengguna = Depends(get_current_user),
):
    """Template import gudang (.xlsx): sheet Data (header saja) + Petunjuk."""
    wb = excel_service.workbook_from_rows(
        headers=["Kode*", "Nama*", "Alamat"],
        rows=[], sheet="Data",
    )
    excel_service.add_instructions_sheet(wb, rows=[
        ["Kolom", "Wajib", "Tipe Data", "Keterangan"],
        ["Kode*", "Ya", "Teks (maks 20)", "Kode unik gudang. Contoh: GD-001. Duplikat dengan gudang AKTIF existing ditolak per baris; gudang NONAKTIF dengan kode sama otomatis diaktifkan kembali."],
        ["Nama*", "Ya", "Teks (maks 100)", "Nama gudang. Contoh: Gudang Pusat."],
        ["Alamat", "Tidak", "Teks", "Alamat lengkap gudang."],
        ["Catatan", "", "", "Baris dengan Kode & Nama kosong dilewati. Urutan kolom bebas — pembacaan berdasar nama kolom. Error satu baris tidak menghentikan baris lain."],
    ])
    return _xlsx_streaming_response(wb, "template-import-gudang.xlsx")


@router.post("/gudang/import", response_model=ImportResult)
async def import_gudang(
    file: UploadFile = File(...),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Import gudang dari file .xlsx (jalur create sama dengan POST /gudang).

    Error per baris tidak menghentikan baris lain — ringkasan dikembalikan 200.
    File bukan .xlsx → 400.
    """
    content = await file.read()
    header_map, rows = _load_import_sheet(content)
    result = ImportResult(total_baris=0, sukses=0, gagal=0, errors=[])
    for offset, raw in enumerate(rows):
        excel_row = offset + 2  # baris 1 = header
        kode = _cell_str(_get_col(raw, header_map, "kode"))
        nama = _cell_str(_get_col(raw, header_map, "nama"))
        if not kode and not nama:
            continue  # baris kosong di-skip
        result.total_baris += 1
        try:
            if not kode:
                raise ValueError("Kode wajib diisi")
            if not nama:
                raise ValueError("Nama wajib diisi")
            data_in = GudangCreate(
                kode=kode, nama=nama,
                alamat=_cell_str(_get_col(raw, header_map, "alamat")),
            )
            # JALUR CREATE YANG SAMA dengan POST /gudang
            # (advisory lock + tolak duplikat AKTIF + reaktivasi NONAKTIF)
            master_service.create_master(db, Gudang, data_in)
            result.sukses += 1
        except (HTTPException, ValueError, IntegrityError) as exc:
            db.rollback()
            result.gagal += 1
            result.errors.append(ImportRowError(baris=excel_row, pesan=_import_error(exc)))
    return result


@router.get("/gudang/{gudang_id}", response_model=GudangResponse)
def get_gudang_detail(gudang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Gudang, gudang_id)
    if not item: raise HTTPException(status_code=404, detail="Gudang tidak ditemukan")
    return item

@router.put("/gudang/{gudang_id}", response_model=GudangResponse)
def update_gudang(gudang_id: UUID, data_in: GudangUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Gudang, gudang_id)
    if not item: raise HTTPException(status_code=404, detail="Gudang tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/gudang/{gudang_id}", status_code=status.HTTP_200_OK)
def delete_gudang(gudang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Gudang, gudang_id)
    if not item: raise HTTPException(status_code=404, detail="Gudang tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Gudang sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Gudang berhasil dinonaktifkan"}


# ==========================================
# SYARAT BAYAR ENDPOINTS
# ==========================================
@router.get("/syarat-bayar", response_model=list[SyaratBayarResponse])
def get_syarat_bayar_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return db.query(SyaratBayar).order_by(SyaratBayar.nama).all()

@router.post("/syarat-bayar", response_model=SyaratBayarResponse, status_code=status.HTTP_201_CREATED)
def create_syarat_bayar(
    data_in: SyaratBayarCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, SyaratBayar, data_in)

@router.get("/syarat-bayar/{syarat_bayar_id}", response_model=SyaratBayarResponse)
def get_syarat_bayar_detail(syarat_bayar_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, SyaratBayar, syarat_bayar_id)
    if not item: raise HTTPException(status_code=404, detail="Syarat Bayar tidak ditemukan")
    return item

@router.put("/syarat-bayar/{syarat_bayar_id}", response_model=SyaratBayarResponse)
def update_syarat_bayar(syarat_bayar_id: UUID, data_in: SyaratBayarUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, SyaratBayar, syarat_bayar_id)
    if not item: raise HTTPException(status_code=404, detail="Syarat Bayar tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/syarat-bayar/{syarat_bayar_id}", status_code=status.HTTP_200_OK)
def delete_syarat_bayar(syarat_bayar_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, SyaratBayar, syarat_bayar_id)
    if not item: raise HTTPException(status_code=404, detail="Syarat Bayar tidak ditemukan")
    db.delete(item)
    db.commit()
    return {"message": "Syarat Bayar berhasil dihapus"}


# ==========================================
# KATEGORI ASET ENDPOINTS
# ==========================================
@router.get("/kategori-aset", response_model=list[KategoriAsetResponse])
def get_kategori_aset_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return db.query(KategoriAset).filter(KategoriAset.status == "AKTIF").order_by(KategoriAset.nama).all()

@router.post("/kategori-aset", response_model=KategoriAsetResponse, status_code=status.HTTP_201_CREATED)
def create_kategori_aset(
    data_in: KategoriAsetCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, KategoriAset, data_in)

@router.get("/kategori-aset/{kategori_aset_id}", response_model=KategoriAsetResponse)
def get_kategori_aset_detail(kategori_aset_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KategoriAset, kategori_aset_id)
    if not item: raise HTTPException(status_code=404, detail="Kategori Aset tidak ditemukan")
    return item

@router.put("/kategori-aset/{kategori_aset_id}", response_model=KategoriAsetResponse)
def update_kategori_aset(kategori_aset_id: UUID, data_in: KategoriAsetUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KategoriAset, kategori_aset_id)
    if not item: raise HTTPException(status_code=404, detail="Kategori Aset tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/kategori-aset/{kategori_aset_id}", status_code=status.HTTP_200_OK)
def delete_kategori_aset(kategori_aset_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KategoriAset, kategori_aset_id)
    if not item: raise HTTPException(status_code=404, detail="Kategori Aset tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Kategori Aset sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Kategori Aset berhasil dinonaktifkan"}


# ==========================================
# KAS BANK AKUN ENDPOINTS
# ==========================================
def _gl_saldo_by_akun(db: Session, akun_ids: list) -> dict:
    """Saldo GL (jurnal POSTED: debit - kredit) per akun_perkiraan_id.

    Doktrin model KasBankAkun (Master Roadmap §23): kolom master `saldo`
    hanyalah backward-compat — kebenaran akuntansi berasal dari GL. Endpoint
    daftar/dropdown kas-bank memakai helper ini agar UI tidak menampilkan
    saldo 0 yang menyesatkan saat master saldo belum tersinkron.
    """
    if not akun_ids:
        return {}
    from sqlalchemy import func
    from app.models.detail.jurnal_detail import JurnalDetail
    from app.models.transaksi.jurnal import JurnalUmum
    rows = (
        db.query(
            JurnalDetail.akun_perkiraan_id,
            func.coalesce(func.sum(JurnalDetail.debit), 0) - func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalDetail.jurnal_umum_id == JurnalUmum.id)
        .filter(
            JurnalDetail.akun_perkiraan_id.in_(akun_ids),
            JurnalUmum.status == "POSTED",
        )
        .group_by(JurnalDetail.akun_perkiraan_id)
        .all()
    )
    return {r[0]: r[1] for r in rows}


def _kas_bank_rows_with_gl_saldo(db: Session, rows) -> list:
    """Serialisasi KasBankAkun dengan saldo dari GL (bukan master saldo)."""
    gl = _gl_saldo_by_akun(db, [r.akun_perkiraan_id for r in rows])
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "kode": r.kode,
            "nama": r.nama,
            "jenis": r.jenis,
            "akun_perkiraan_id": r.akun_perkiraan_id,
            "currency": r.currency or "IDR",
            "saldo": gl.get(r.akun_perkiraan_id, Decimal("0")),
            "status": r.status,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "akun_perkiraan": r.akun_perkiraan,
        })
    return out


@router.get("/kas-bank-akun", response_model=list[KasBankAkunResponse])
def get_kas_bank_akun_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    rows = db.query(KasBankAkun).filter(KasBankAkun.status == "AKTIF").order_by(KasBankAkun.nama).all()
    # Saldo ditampilkan dari GL (jurnal POSTED) — master saldo legacy bisa 0/stale.
    return _kas_bank_rows_with_gl_saldo(db, rows)

@router.post("/kas-bank-akun", response_model=KasBankAkunResponse, status_code=status.HTTP_201_CREATED)
def create_kas_bank_akun(
    data_in: KasBankAkunCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, KasBankAkun, data_in)

@router.get("/kas-bank-akun/{kas_bank_akun_id}", response_model=KasBankAkunResponse)
def get_kas_bank_akun_detail(kas_bank_akun_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KasBankAkun, kas_bank_akun_id)
    if not item: raise HTTPException(status_code=404, detail="Kas Bank Akun tidak ditemukan")
    # Saldo dari GL agar edit-form/detail tidak menampilkan saldo stale master.
    return _kas_bank_rows_with_gl_saldo(db, [item])[0]

@router.put("/kas-bank-akun/{kas_bank_akun_id}", response_model=KasBankAkunResponse)
def update_kas_bank_akun(kas_bank_akun_id: UUID, data_in: KasBankAkunUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KasBankAkun, kas_bank_akun_id)
    if not item: raise HTTPException(status_code=404, detail="Kas Bank Akun tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/kas-bank-akun/{kas_bank_akun_id}", status_code=status.HTTP_200_OK)
def delete_kas_bank_akun(kas_bank_akun_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KasBankAkun, kas_bank_akun_id)
    if not item: raise HTTPException(status_code=404, detail="Kas Bank Akun tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Kas Bank Akun sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Kas Bank Akun berhasil dinonaktifkan"}


# ==========================================
# SETTING AKUN ENDPOINTS
# Mapping akun default (Pendapatan, Pembelian, PPN, dll) yang dipakai
# saat auto-posting jurnal di modul Penjualan & Pembelian.
# Data di-seed lewat app.seed.phase3_setting_akun_seed — endpoint ini
# hanya untuk MENGUBAH akun_perkiraan_id-nya (bukan create/delete key baru).
# ==========================================
@router.get("/setting-akun", response_model=list[SettingAkunResponse])
def get_setting_akun_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return db.query(SettingAkun).order_by(SettingAkun.label).all()

@router.get("/setting-akun/{key}", response_model=SettingAkunResponse)
def get_setting_akun_detail(key: str, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = db.query(SettingAkun).filter(SettingAkun.key == key).first()
    if not item: raise HTTPException(status_code=404, detail="Setting akun tidak ditemukan")
    return item

@router.put("/setting-akun/{key}", response_model=SettingAkunResponse)
def update_setting_akun(key: str, data_in: SettingAkunUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    from app.services.inventory_receipt_control import KEY, validate_grni_account
    from app.services import setting_akun_service
    item = db.query(SettingAkun).filter(SettingAkun.key == key).first()
    if key == KEY:
        try:
            validate_grni_account(db, data_in.akun_perkiraan_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if item is None:
            item = SettingAkun(key=KEY, label="Penerimaan Dalam Proses (GRNI)",
                               akun_perkiraan_id=data_in.akun_perkiraan_id)
            db.add(item)
            db.commit()
            db.refresh(item)
            setting_akun_service.clear_cache()
            return item
    if not item:
        # Key belum ada di DB (mis. di-skip seeder karena akun default legacy
        # tidak ditemukan di COA v2). Bila key dikenal sistem, buat barisan
        # setting baru — agar bisa dikonfigurasi dari halaman Setting Akun.
        label = setting_akun_service.KNOWN_SETTING_LABELS.get(key)
        if label:
            item = SettingAkun(key=key, label=label,
                               akun_perkiraan_id=data_in.akun_perkiraan_id)
            db.add(item)
            db.commit()
            db.refresh(item)
            setting_akun_service.clear_cache()
            return item
        raise HTTPException(status_code=404, detail="Setting akun tidak ditemukan")
    item = master_service.update_master(db, item, data_in)
    setting_akun_service.clear_cache()
    return item



# ==========================================
# APP SETTING ENDPOINTS (Setting global non-COA)
# Contoh: METODE_VALUASI — metode valuasi persediaan global
# yang dikonfigurasi dari halaman Setting Akun.
# ==========================================
@router.get("/app-setting/{key}", response_model=AppSettingResponse)
def get_app_setting(key: str, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    """Ambil nilai setting aplikasi global berdasarkan key."""
    from app.services import app_setting_service

    if key == app_setting_service.KEY_METODE_VALUASI:
        # Selalu ada jawaban valid (default AVERAGE bila belum dikonfigurasi)
        return {"key": key, "value": app_setting_service.get_metode_valuasi(db)}

    item = db.query(AppSetting).filter(AppSetting.key == key).first()
    if not item:
        raise HTTPException(status_code=404, detail="Setting tidak ditemukan")
    return item


@router.put("/app-setting/{key}", response_model=AppSettingResponse)
def update_app_setting(key: str, data_in: AppSettingUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    """Update nilai setting aplikasi global."""
    from app.services import app_setting_service

    if key != app_setting_service.KEY_METODE_VALUASI:
        raise HTTPException(status_code=404, detail="Setting tidak ditemukan")
    try:
        item = app_setting_service.set_metode_valuasi(db, data_in.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item



# ==========================================
# KATEGORI BARANG ENDPOINTS
# ==========================================
@router.get("/kategori-barang", response_model=list[KategoriBarangResponse])
def get_kategori_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Get semua kategori barang AKTIF"""
    return db.query(KategoriBarang).filter(KategoriBarang.status == "AKTIF").order_by(KategoriBarang.nama).all()

@router.post("/kategori-barang", response_model=KategoriBarangResponse, status_code=status.HTTP_201_CREATED)
def create_kategori_barang(
    data_in: KategoriBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, KategoriBarang, data_in)

@router.get("/kategori-barang/{kategori_id}", response_model=KategoriBarangResponse)
def get_kategori_barang_detail(
    kategori_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, KategoriBarang, kategori_id)
    if not item:
        raise HTTPException(status_code=404, detail="Kategori Barang tidak ditemukan")
    return item

@router.put("/kategori-barang/{kategori_id}", response_model=KategoriBarangResponse)
def update_kategori_barang(
    kategori_id: UUID,
    data_in: KategoriBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, KategoriBarang, kategori_id)
    if not item:
        raise HTTPException(status_code=404, detail="Kategori Barang tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/kategori-barang/{kategori_id}", status_code=status.HTTP_200_OK)
def delete_kategori_barang(
    kategori_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, KategoriBarang, kategori_id)
    if not item:
        raise HTTPException(status_code=404, detail="Kategori Barang tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Kategori Barang sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Kategori Barang berhasil dinonaktifkan"}


# ==========================================
# SATUAN ENDPOINTS
# ==========================================
@router.get("/satuan", response_model=list[SatuanResponse])
def get_satuan_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Get semua satuan AKTIF"""
    return db.query(Satuan).filter(Satuan.status == "AKTIF").order_by(Satuan.nama).all()

@router.post("/satuan", response_model=SatuanResponse, status_code=status.HTTP_201_CREATED)
def create_satuan(
    data_in: SatuanCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Satuan, data_in)

@router.get("/satuan/{satuan_id}", response_model=SatuanResponse)
def get_satuan_detail(
    satuan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Satuan, satuan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
    return item

@router.put("/satuan/{satuan_id}", response_model=SatuanResponse)
def update_satuan(
    satuan_id: UUID,
    data_in: SatuanUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Satuan, satuan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/satuan/{satuan_id}", status_code=status.HTTP_200_OK)
def delete_satuan(
    satuan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Satuan, satuan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Satuan sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Satuan berhasil dinonaktifkan"}


# ==========================================
# ENDPOINT DROPDOWN (Untuk Form Transaksi)
# ==========================================
@router.get("/coa-dropdown", response_model=list[COASimpleResponse])
def get_coa_dropdown(
    exclude_linked: bool = Query(False, description="Exclude COA subledger auto-created per pelanggan/supplier (Piutang Usaha & Hutang Usaha children)"),
    include_header_group: bool = Query(False, description="Include akun level HEADER & GROUP juga (bukan cuma DETAIL). Dipakai halaman Setting Akun untuk key seperti PIUTANG_USAHA/HUTANG_USAHA yang nunjuk ke akun induk/root, bukan detail."),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Dropdown COA ringan (id, kode, nama) — default hanya akun DETAIL yang AKTIF
    (dipakai form transaksi/jurnal, yang cuma boleh posting ke akun DETAIL).

    Parameter exclude_linked: jika True, exclude COA subledger (is_subledger=True)
    yang auto-created per pelanggan/supplier.
    Parameter include_header_group: jika True, ikut include akun level HEADER
    & GROUP (bukan cuma DETAIL) — dipakai halaman Setting Akun, karena beberapa
    key (PIUTANG_USAHA, HUTANG_USAHA) memang nunjuk ke akun induk/root, bukan detail.
    """
    from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun

    query = db.query(AkunPerkiraan).filter(AkunPerkiraan.status == "AKTIF")

    if not include_header_group:
        query = query.filter(AkunPerkiraan.tingkat == TingkatAkun.DETAIL)

    if exclude_linked:
        query = query.filter(AkunPerkiraan.is_subledger == False)  # noqa: E712

    return query.order_by(AkunPerkiraan.kode).all()


@router.get("/barang-dropdown", response_model=list[BarangSimpleResponse])
def get_barang_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Dropdown Barang ringan (id, kode, nama)"""
    return db.query(Barang).filter(
        Barang.status == "AKTIF"
    ).order_by(Barang.nama).all()


@router.get("/pelanggan-dropdown", response_model=list[PelangganSimpleResponse])
def get_pelanggan_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Dropdown Pelanggan ringan (id, kode, nama)"""
    return db.query(Pelanggan).filter(
        Pelanggan.status == "AKTIF"
    ).order_by(Pelanggan.nama).all()


@router.get("/supplier-dropdown", response_model=list[SupplierSimpleResponse])
def get_supplier_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Dropdown Supplier ringan (id, kode, nama)"""
    return db.query(Supplier).filter(
        Supplier.status == "AKTIF"
    ).order_by(Supplier.nama).all()


def _get_kas_detail_coa_ids(db: Session) -> list[UUID]:
    """Cari semua COA DETAIL yang merupakan anak/cucu dari 'Kas dan Setara Kas'.

    Utamakan dari setting_akun (KEY_KAS_DAN_SETARA_KAS) supaya tetap jalan
    walau user ganti nama COA. Fallback ke pencarian by-nama (ilike) kalau
    setting belum di-configure/stale. Dipakai bareng oleh /kas-bank-dropdown
    dan /kas-bank-akun/sync supaya logic-nya nggak duplikat.
    """
    from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun

    kas_root_id = setting_akun_service.get_akun_id(db, setting_akun_service.KEY_KAS_DAN_SETARA_KAS)

    if kas_root_id and not db.query(AkunPerkiraan).filter(AkunPerkiraan.id == kas_root_id).first():
        kas_root_id = None  # stale reference, fallback di bawah

    if not kas_root_id:
        kas_root_id = db.query(AkunPerkiraan.id).filter(
            AkunPerkiraan.nama.ilike("%KAS%DAN%SETARA%KAS%"),
            AkunPerkiraan.tingkat.in_([TingkatAkun.HEADER, TingkatAkun.GROUP]),
        ).scalar()

    if not kas_root_id:
        return []

    # Recursive CTE: cari semua descendant (anak, cucu, dst)
    base = db.query(AkunPerkiraan.id).filter(
        AkunPerkiraan.induk_id == kas_root_id
    ).cte(name="coa_children", recursive=True)

    recursive = db.query(AkunPerkiraan.id).join(
        base, AkunPerkiraan.induk_id == base.c.id
    )

    all_descendants = base.union(recursive)

    return [
        row[0] for row in db.query(AkunPerkiraan.id).filter(
            AkunPerkiraan.id.in_(db.query(all_descendants.c.id)),
            AkunPerkiraan.tingkat == TingkatAkun.DETAIL,
        ).all()
    ]


@router.get("/kas-bank-dropdown", response_model=list[KasBankAkunResponse])
def get_kas_bank_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """
    Dropdown Kas/Bank Akun yang terhubung ke COA di bawah 'Kas dan Setara Kas'.
    Mencari semua akun DETAIL yang merupakan anak/cucu dari COA tersebut.

    Catatan: hanya menampilkan COA yang SUDAH punya entry KasBankAkun. COA
    detail yang di-import manual tapi belum di-sync tidak akan muncul —
    panggil POST /master/kas-bank-akun/sync untuk auto-create entry-nya.
    """
    detail_ids = _get_kas_detail_coa_ids(db)
    if not detail_ids:
        return []

    rows = db.query(KasBankAkun).filter(
        KasBankAkun.akun_perkiraan_id.in_(detail_ids),
        KasBankAkun.status == "AKTIF",
    ).order_by(KasBankAkun.nama).all()
    # Saldo dari GL — SummaryCard "Total Saldo Kas/Bank" di modul Kas & Bank
    # membaca field ini; master saldo legacy bisa 0/stale.
    return _kas_bank_rows_with_gl_saldo(db, rows)


@router.post("/kas-bank-akun/sync", status_code=status.HTTP_200_OK)
def sync_kas_bank_akun(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Sync COA di bawah 'Kas dan Setara Kas' ke tabel kas_bank_akun.

    Auto-create entry KasBankAkun untuk COA DETAIL yang belum punya
    (misal hasil import manual/Excel yang belum ke-link). Aman dipanggil
    berkali-kali (idempotent) — COA yang sudah punya KasBankAkun di-skip.
    Panggil endpoint ini sekali setelah import COA untuk sync semua akun
    Kas/Bank baru.
    """
    from app.models.akun_perkiraan import AkunPerkiraan

    detail_ids = _get_kas_detail_coa_ids(db)
    if not detail_ids:
        return {"created": 0, "skipped": 0, "detail": []}

    already_linked_ids = {
        row[0] for row in db.query(KasBankAkun.akun_perkiraan_id)
        .filter(KasBankAkun.akun_perkiraan_id.in_(detail_ids)).all()
    }

    to_create_query = db.query(AkunPerkiraan).filter(
        AkunPerkiraan.id.in_(detail_ids),
        AkunPerkiraan.status == "AKTIF",
    )
    if already_linked_ids:
        to_create_query = to_create_query.filter(~AkunPerkiraan.id.in_(already_linked_ids))
    to_create = to_create_query.all()

    if not to_create:
        return {"created": 0, "skipped": len(already_linked_ids), "detail": []}

    # Kode kas_bank_akun berikutnya: lanjutkan penomoran "BK-XXX" yang sudah ada
    last_kode = (
        db.query(KasBankAkun.kode)
        .filter(KasBankAkun.kode.like("BK-%"))
        .order_by(KasBankAkun.kode.desc())
        .first()
    )
    try:
        next_seq = int(last_kode[0].split("-")[1]) + 1 if last_kode else 1
    except (IndexError, ValueError):
        next_seq = 1

    created_detail = []
    for coa in to_create:
        # Deteksi jenis dari nama: mengandung "bank" -> BANK, selain itu KAS (default)
        jenis = JenisKasBank.BANK if "bank" in coa.nama.lower() else JenisKasBank.KAS

        new_item = KasBankAkun(
            kode=f"BK-{next_seq:03d}",
            nama=coa.nama,
            jenis=jenis,
            akun_perkiraan_id=coa.id,
            saldo=coa.saldo or 0,
            status="AKTIF",
        )
        db.add(new_item)
        created_detail.append({"kode_coa": coa.kode, "nama": coa.nama, "jenis": jenis.value, "kode_kas_bank": new_item.kode})
        next_seq += 1

    db.commit()

    return {
        "created": len(created_detail),
        "skipped": len(already_linked_ids),
        "detail": created_detail,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Profil Perusahaan — identitas untuk header cetak/PDF (update ASAHI)
# Satu baris data; dipakai semua template cetak (logo + nama perusahaan
# menggantikan nama sistem "ASAHI Books" yang dulu hardcoded frontend).
# ═══════════════════════════════════════════════════════════════════════════

# Batas panjang data URL logo (±1.5 MB gambar setelah base64).
MAX_LOGO_DATA_URL = 2_000_000

# Update ASAHI #6: slogan default (dipakai saat profil belum di-setup /
# kolom masih kosong) — tampil khusus di header cetak Invoice Penjualan.
SLOGAN_DEFAULT = (
    "Machining, precision, part Jig & fixture Fabrication "
    "Mechanical & electrical Industrial supplies"
)


def _active_rekening_bank(db: Session) -> List[RekeningBank]:
    """Rekening bank AKTIF untuk di-embed ke response profil (cetak invoice)."""
    return (
        db.query(RekeningBank)
        .filter(RekeningBank.is_aktif.is_(True))
        .order_by(RekeningBank.created_at, RekeningBank.id)
        .all()
    )


@router.get("/company-profile", response_model=CompanyProfileResponse)
def get_company_profile(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil profil perusahaan untuk header cetak/PDF.

    Update ASAHI #6: menyertakan slogan + daftar rekening bank AKTIF
    (dipakai template cetak Invoice Penjualan).
    """
    row = db.query(CompanyProfile).order_by(CompanyProfile.created_at, CompanyProfile.id).first()
    if row is None:
        # Fallback defensif bila migrasi/seed belum jalan — kembalikan nilai
        # default lama (identik dengan COMPANY_INFO yang dulu hardcoded FE).
        # Update ASAHI #3: alamat dipecah 2 baris setelah "Jatireja" (kop cetak).
        from uuid import uuid4
        return CompanyProfileResponse(
            id=uuid4(),
            nama_perusahaan="ASAHI Books",
            alamat=(
                "Jalan Simpangan No.18, RT.03/RW.06, Jatireja,\n"
                "Kec. Cikarang Tim., Kabupaten Bekasi, Jawa Barat 17530"
            ),
            slogan=SLOGAN_DEFAULT,
            rekening_bank=[],
        )
    return CompanyProfileResponse(
        id=row.id,
        nama_perusahaan=row.nama_perusahaan,
        alamat=row.alamat,
        telepon=row.telepon,
        email=row.email,
        logo=row.logo,
        # Update ASAHI #6: slogan apa adanya — nilai default di-seed lewat
        # migrasi; kalau user sengaja mengosongkan, invoice tampil tanpa
        # slogan (tidak dipaksa kembali ke default).
        slogan=row.slogan,
        rekening_bank=_active_rekening_bank(db),
        updated_at=row.updated_at,
    )


@router.put("/company-profile", response_model=CompanyProfileResponse)
def update_company_profile(
    data_in: CompanyProfileUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Simpan profil perusahaan (nama, alamat, kontak, logo, slogan) untuk cetak/PDF."""
    payload = data_in.model_dump()

    logo = payload.get("logo")
    if logo is not None:
        if not logo.startswith("data:image/"):
            raise HTTPException(400, "Logo harus berupa data URL gambar (data:image/...)")
        if len(logo) > MAX_LOGO_DATA_URL:
            raise HTTPException(400, "Ukuran logo terlalu besar (maksimal ±1.5 MB). Kompres/kecilkan gambar lalu unggah ulang.")

    # Update ASAHI #6: slogan kosong/disengaja dikosongkan → NULL (invoice
    # tampil tanpa slogan), bukan string kosong.
    if "slogan" in payload and payload["slogan"] is not None and not payload["slogan"].strip():
        payload["slogan"] = None

    row = db.query(CompanyProfile).order_by(CompanyProfile.created_at, CompanyProfile.id).first()
    if row is None:
        row = CompanyProfile(**payload)
        db.add(row)
    else:
        for key, value in payload.items():
            setattr(row, key, value)

    db.commit()
    db.refresh(row)
    # Update ASAHI #6: response build manual — embed rekening bank aktif agar
    # store frontend cukup satu fetch (sama seperti GET).
    return CompanyProfileResponse(
        id=row.id,
        nama_perusahaan=row.nama_perusahaan,
        alamat=row.alamat,
        telepon=row.telepon,
        email=row.email,
        logo=row.logo,
        slogan=row.slogan,
        rekening_bank=_active_rekening_bank(db),
        updated_at=row.updated_at,
    )


# ═════════════════════════════════════════════════════════════════════════
# Mata Uang — dropdown Currency form SO/PO (update ASAHI #3)
# Dikelola di Pengaturan → Profil Perusahaan.
# ═════════════════════════════════════════════════════════════════════════

@router.get("/mata-uang", response_model=List[MataUangResponse])
def list_mata_uang(
    aktif_only: bool = Query(False, description="True = hanya yang aktif (untuk dropdown)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar mata uang. Dropdown form pesanan memakai aktif_only=true."""
    query = db.query(MataUang)
    if aktif_only:
        query = query.filter(MataUang.is_aktif.is_(True))
    return query.order_by(MataUang.kode).all()


@router.post("/mata-uang", response_model=MataUangResponse, status_code=status.HTTP_201_CREATED)
def create_mata_uang(
    data_in: MataUangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Tambah mata uang baru (contoh: EUR, CNY, SGD)."""
    kode = data_in.kode.strip().upper()
    if db.query(MataUang).filter(MataUang.kode == kode).first():
        raise HTTPException(400, f"Mata uang {kode} sudah ada")
    row = MataUang(kode=kode, nama=data_in.nama.strip(), is_aktif=data_in.is_aktif)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/mata-uang/{mata_uang_id}", response_model=MataUangResponse)
def update_mata_uang(
    mata_uang_id: UUID,
    data_in: MataUangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Edit mata uang (nama / status aktif / kode)."""
    row = db.query(MataUang).filter(MataUang.id == mata_uang_id).first()
    if not row:
        raise HTTPException(404, "Mata uang tidak ditemukan")

    payload = data_in.model_dump(exclude_unset=True)
    if "kode" in payload and payload["kode"] is not None:
        kode_baru = payload["kode"].strip().upper()
        if kode_baru != row.kode and db.query(MataUang).filter(MataUang.kode == kode_baru).first():
            raise HTTPException(400, f"Mata uang {kode_baru} sudah ada")
        payload["kode"] = kode_baru
    if "nama" in payload and payload["nama"] is not None:
        payload["nama"] = payload["nama"].strip()

    # IDR adalah mata uang dasar pembukuan — tidak boleh dinonaktifkan.
    if row.kode == "IDR" and payload.get("is_aktif") is False:
        raise HTTPException(400, "IDR (Rupiah) adalah mata uang dasar dan tidak dapat dinonaktifkan")

    for key, value in payload.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/mata-uang/{mata_uang_id}", status_code=status.HTTP_200_OK)
def delete_mata_uang(
    mata_uang_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus mata uang. IDR tidak boleh dihapus; mata uang yang masih dipakai
    dokumen (SO/PO) juga ditolak agar histori tetap konsisten."""
    row = db.query(MataUang).filter(MataUang.id == mata_uang_id).first()
    if not row:
        raise HTTPException(404, "Mata uang tidak ditemukan")
    if row.kode == "IDR":
        raise HTTPException(400, "IDR (Rupiah) adalah mata uang dasar dan tidak dapat dihapus")

    from app.models.transaksi.penjualan.sales_order import SalesOrder
    from app.models.transaksi.pembelian.purchase_order import PurchaseOrder
    if db.query(PurchaseOrder).filter(PurchaseOrder.currency == row.kode).first() or \
       db.query(SalesOrder).filter(SalesOrder.currency == row.kode).first():
        raise HTTPException(400, f"Mata uang {row.kode} masih dipakai dokumen pesanan — nonaktifkan saja agar tidak muncul di dropdown")

    db.delete(row)
    db.commit()
    return {"ok": True, "message": f"Mata uang {row.kode} dihapus"}


# ═════════════════════════════════════════════════════════════════════════
# Alamat Pengiriman — gudang tujuan PO (update ASAHI #3)
# Dikelola di Pengaturan → Profil Perusahaan; saat input PO wajib pilih satu.
# ═════════════════════════════════════════════════════════════════════════

@router.get("/alamat-pengiriman", response_model=List[AlamatPengirimanResponse])
def list_alamat_pengiriman(
    aktif_only: bool = Query(False, description="True = hanya yang aktif (untuk form PO)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar alamat pengiriman (gudang tujuan PO)."""
    query = db.query(AlamatPengiriman)
    if aktif_only:
        query = query.filter(AlamatPengiriman.is_aktif.is_(True))
    return query.order_by(AlamatPengiriman.created_at).all()


@router.post("/alamat-pengiriman", response_model=AlamatPengirimanResponse, status_code=status.HTTP_201_CREATED)
def create_alamat_pengiriman(
    data_in: AlamatPengirimanCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Tambah alamat pengiriman (gudang) baru."""
    row = AlamatPengiriman(
        prefix=data_in.prefix.strip(),
        nama=data_in.nama.strip(),
        is_aktif=data_in.is_aktif,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/alamat-pengiriman/{alamat_id}", response_model=AlamatPengirimanResponse)
def update_alamat_pengiriman(
    alamat_id: UUID,
    data_in: AlamatPengirimanUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Edit alamat pengiriman. PO lama TIDAK ikut berubah (cetak memakai snapshot)."""
    row = db.query(AlamatPengiriman).filter(AlamatPengiriman.id == alamat_id).first()
    if not row:
        raise HTTPException(404, "Alamat pengiriman tidak ditemukan")

    payload = data_in.model_dump(exclude_unset=True)
    if "prefix" in payload and payload["prefix"] is not None:
        payload["prefix"] = payload["prefix"].strip()
    if "nama" in payload and payload["nama"] is not None:
        payload["nama"] = payload["nama"].strip()

    for key, value in payload.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/alamat-pengiriman/{alamat_id}", status_code=status.HTTP_200_OK)
def delete_alamat_pengiriman(
    alamat_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus alamat pengiriman. PO yang merujuk alamat ini tetap aman —
    referensinya di-NULL-kan dan cetakan memakai snapshot teks."""
    row = db.query(AlamatPengiriman).filter(AlamatPengiriman.id == alamat_id).first()
    if not row:
        raise HTTPException(404, "Alamat pengiriman tidak ditemukan")
    db.delete(row)
    db.commit()
    return {"ok": True, "message": "Alamat pengiriman dihapus"}


# ═════════════════════════════════════════════════════════════════════════
# Rekening Bank — tampil di bawah Keterangan pada cetak Invoice Penjualan
# (update ASAHI #6). Dikelola di Pengaturan → Profil Perusahaan.
# ═════════════════════════════════════════════════════════════════════════

def _validate_rekening_mata_uang(db: Session, kode: str) -> str:
    """Validasi kode mata uang rekening ke master mata uang (bila ada isinya)."""
    kode = (kode or "").strip().upper() or "IDR"
    if db.query(MataUang).count() > 0 and not db.query(MataUang).filter(MataUang.kode == kode).first():
        raise HTTPException(400, f"Mata uang {kode} tidak ada di daftar mata uang — tambahkan dulu di bagian Mata Uang")
    return kode


@router.get("/rekening-bank", response_model=List[RekeningBankResponse])
def list_rekening_bank(
    aktif_only: bool = Query(False, description="True = hanya yang aktif (untuk cetak invoice)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar rekening bank perusahaan (untuk cetak Invoice Penjualan)."""
    query = db.query(RekeningBank)
    if aktif_only:
        query = query.filter(RekeningBank.is_aktif.is_(True))
    return query.order_by(RekeningBank.created_at, RekeningBank.id).all()


@router.post("/rekening-bank", response_model=RekeningBankResponse, status_code=status.HTTP_201_CREATED)
def create_rekening_bank(
    data_in: RekeningBankCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Tambah rekening bank (contoh: Bank BNI KCP Jababeka — 12345678910 — IDR)."""
    row = RekeningBank(
        nama_bank=data_in.nama_bank.strip(),
        no_rekening=data_in.no_rekening.strip(),
        mata_uang=_validate_rekening_mata_uang(db, data_in.mata_uang),
        is_aktif=data_in.is_aktif,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/rekening-bank/{rekening_id}", response_model=RekeningBankResponse)
def update_rekening_bank(
    rekening_id: UUID,
    data_in: RekeningBankUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Edit rekening bank (nama bank / nomor / mata uang / status aktif)."""
    row = db.query(RekeningBank).filter(RekeningBank.id == rekening_id).first()
    if not row:
        raise HTTPException(404, "Rekening bank tidak ditemukan")

    payload = data_in.model_dump(exclude_unset=True)
    if "nama_bank" in payload and payload["nama_bank"] is not None:
        payload["nama_bank"] = payload["nama_bank"].strip()
    if "no_rekening" in payload and payload["no_rekening"] is not None:
        payload["no_rekening"] = payload["no_rekening"].strip()
    if "mata_uang" in payload and payload["mata_uang"] is not None:
        payload["mata_uang"] = _validate_rekening_mata_uang(db, payload["mata_uang"])

    for key, value in payload.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/rekening-bank/{rekening_id}", status_code=status.HTTP_200_OK)
def delete_rekening_bank(
    rekening_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus rekening bank. Nonaktifkan (jangan hapus) bila hanya ingin
    menahan dari cetakan sambil menyimpan datanya."""
    row = db.query(RekeningBank).filter(RekeningBank.id == rekening_id).first()
    if not row:
        raise HTTPException(404, "Rekening bank tidak ditemukan")
    db.delete(row)
    db.commit()
    return {"ok": True, "message": f"Rekening {row.nama_bank} dihapus"}
