from app.services.accounting_control import atomic_accounting_write
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.transaksi.jurnal import JurnalUmum, RefModule, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail
from app.schemas.base import PaginatedResponse
from app.schemas.jurnal import JurnalUmumListResponse, JurnalUmumDetailResponse, JurnalDetailItemCreate, JurnalManualCreate
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.services.accounting_control import require_unposted
from app.services.posting_service import validate_entries
from app.services import workflow_service
from app.services import excel_service
from app.services.laporan_service import get_company_name
from app.services.reporting_ledger import local_datetime
from app.utils.ref_module_label import get_ref_module_label

router = APIRouter()


# ===== EXPORT EXCEL (Update #023) =====
def _xlsx_response(wb, filename: str):
    """Bungkus workbook menjadi StreamingResponse .xlsx (pola export Update #5)."""
    from fastapi.responses import StreamingResponse
    res = excel_service.xlsx_response(wb, filename)
    return StreamingResponse(res["stream"], media_type=res["media_type"], headers=res["headers"])


# ==========================================
# FRONTEND WIRE: RefModule enum list
# ==========================================
@router.get("/ref-modules", tags=["Jurnal Umum"])
def list_ref_modules(
    include_legacy: bool = Query(
        True,
        description="Include legacy enum values (untuk filter jurnal historis). "
                    "Set False untuk dropdown 'transaksi baru' yang hanya boleh pakai enum canonical.",
    ),
):
    """List semua RefModule enum beserta label & group untuk frontend dropdown.

    Dipakai oleh frontend untuk:
    - Dropdown filter di list jurnal (include_legacy=True, supaya bisa filter
      data historis yang masih pakai enum legacy)
    - Display label ramah pengguna di kolom "Sumber Modul" jurnal list
    - Dropdown "pilih sumber modul" untuk form pembuatan jurnal (kalau perlu)

    Response format:
        {
            "data": [
                {
                    "value": "SALES_DELIVERY",
                    "label": "Pengiriman Barang",
                    "group": "Penjualan",
                    "is_legacy": false
                },
                ...
            ]
        }
    """
    from app.utils.ref_module_label import list_ref_modules
    return {"data": list_ref_modules(include_legacy=include_legacy)}


@atomic_accounting_write
def _update_manual(db, db_obj, data_in, actor):
    require_unposted(db_obj)
    from app.services.workflow_service import role, APPROVERS, find_workflow, append_event
    from app.services.penutupan_periode_service import validate_periode_not_closed
    if actor.id != db_obj.created_by and role(actor) not in APPROVERS:
        raise HTTPException(403, 'Hanya pembuat atau manajer/admin yang dapat mengedit draft')
    validate_periode_not_closed(db, data_in.tanggal)
    entries = [JurnalEntryItem(d.akun_perkiraan_id, d.debit, d.kredit, d.keterangan) for d in data_in.details]
    debit, kredit = validate_entries(db, entries, is_manual=True)
    db_obj.details.clear()
    db.flush()
    for e in entries:
        db_obj.details.append(JurnalDetail(akun_perkiraan_id=e.akun_perkiraan_id, debit=e.debit, kredit=e.kredit, keterangan=e.keterangan))
    from app.services.reporting_ledger import local_datetime
    db_obj.tanggal = local_datetime(data_in.tanggal)
    db_obj.keterangan = data_in.keterangan
    db_obj.total_debit, db_obj.total_kredit = debit, kredit
    wf = find_workflow(db, db_obj)
    if wf:
        append_event(db, wf, 'edit', 'DRAFT', actor.id)
    return db_obj


@router.put('/manual/{jurnal_id}', response_model=JurnalUmumDetailResponse)
def update_manual(jurnal_id: UUID, data_in: JurnalManualCreate, db: Session = Depends(get_current_db),
                  current_user: Pengguna = Depends(get_current_user)):
    obj = db.get(JurnalUmum, jurnal_id)
    if not obj or obj.ref_module != RefModule.MANUAL or obj.reversal_of_id:
        raise HTTPException(404, 'Jurnal manual tidak ditemukan')
    try:
        return _update_manual(db, obj, data_in, current_user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("", response_model=PaginatedResponse[JurnalUmumListResponse])
def get_jurnal_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="Cari berdasarkan no jurnal, keterangan, atau tipe transaksi"),
    ref_module: Optional[RefModule] = Query(None, description="Filter berdasarkan modul referensi"),
    status_filter: Optional[StatusJurnal] = Query(None, alias="status", description="Filter status jurnal"),
    tanggal_mulai: Optional[datetime] = Query(None, alias="tanggalMulai", description="Filter tanggal mulai"),
    tanggal_akhir: Optional[datetime] = Query(None, alias="tanggalAkhir", description="Filter tanggal akhir"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """List semua jurnal umum dengan filter dan pagination."""
    query = db.query(JurnalUmum).options(joinedload(JurnalUmum.creator))

    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            JurnalUmum.no_jurnal.ilike(search_term)
            | JurnalUmum.keterangan.ilike(search_term)
            | JurnalUmum.tipe_transaksi.ilike(search_term)
        )

    # Module filter
    if ref_module:
        query = query.filter(JurnalUmum.ref_module == ref_module)

    # Status filter
    if status_filter:
        query = query.filter(JurnalUmum.status == status_filter)

    # Date range filter
    if tanggal_mulai:
        query = query.filter(JurnalUmum.tanggal >= tanggal_mulai)
    if tanggal_akhir:
        query = query.filter(JurnalUmum.tanggal <= tanggal_akhir)

    total = query.count()
    data = query.order_by(JurnalUmum.tanggal.desc(), JurnalUmum.no_jurnal.desc()) \
        .offset(skip).limit(limit).all()

    return {"data": data, "total": total, "skip": skip, "limit": limit}


# ===== EXPORT EXCEL (Update #025 — line-level ala sheet "JURNAL" Master MAS) =====
# Route /export WAJIB dideklarasikan SEBELUM /{jurnal_id} — kalau tidak,
# GET /jurnal/export tertangkap path param {jurnal_id} → 422 (UUID parse error).
@router.get("/export")
def export_jurnal_list(
    search: str | None = Query(None, description="Cari berdasarkan no jurnal, keterangan, atau tipe transaksi"),
    ref_module: RefModule | None = Query(None, description="Filter berdasarkan modul referensi"),
    status_filter: StatusJurnal | None = Query(None, alias="status", description="Filter status jurnal"),
    tanggal_mulai: datetime | None = Query(None, alias="tanggalMulai", description="Filter tanggal mulai"),
    tanggal_akhir: datetime | None = Query(None, alias="tanggalAkhir", description="Filter tanggal akhir"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Export Jurnal Umum ke .xlsx LEVEL BARIS (line) — Update #025.

    Meniru sheet "JURNAL" workbook Master MAS: satu baris per DETAIL jurnal
    (akun), kolom Posting (tipe transaksi) / Tanggal Transaksi / No. Voucher /
    Modul Referensi / No. Referensi / Jurnal (kode - nama akun) / D/C /
    Debet / Credit / Keterangan / Status + baris TOTAL (jumlah Debet &
    Credit — idiom SUBTOTAL sheet JURNAL).

    Filter IDENTIK dengan GET /jurnal (search, ref_module, status,
    tanggalMulai/tanggalAkhir) TANPA pagination — semua jurnal yang cocok
    diekspor (cap aman 10.000 jurnal) dengan seluruh baris detailnya,
    urutan sama seperti list (tanggal DESC, no jurnal DESC).
    """
    query = db.query(JurnalUmum)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            JurnalUmum.no_jurnal.ilike(search_term)
            | JurnalUmum.keterangan.ilike(search_term)
            | JurnalUmum.tipe_transaksi.ilike(search_term)
        )
    if ref_module:
        query = query.filter(JurnalUmum.ref_module == ref_module)
    if status_filter:
        query = query.filter(JurnalUmum.status == status_filter)
    if tanggal_mulai:
        query = query.filter(JurnalUmum.tanggal >= tanggal_mulai)
    if tanggal_akhir:
        query = query.filter(JurnalUmum.tanggal <= tanggal_akhir)

    rows_jurnal = query.options(
        joinedload(JurnalUmum.details).joinedload(JurnalDetail.akun_perkiraan)
    ).order_by(
        JurnalUmum.tanggal.desc(), JurnalUmum.no_jurnal.desc()
    ).limit(10000).all()

    data: list[tuple[str, list]] = []
    total_debet = Decimal(0)
    total_kredit = Decimal(0)
    for j in rows_jurnal:
        tgl = local_datetime(j.tanggal).date()
        status = j.status.value if j.status else ""
        ket_umum = j.keterangan or ""
        modul_label = get_ref_module_label(j.ref_module)
        for d in (j.details or []):
            akun = getattr(d, "akun_perkiraan", None)
            jurnal_cell = f"{akun.kode} - {akun.nama}" if akun else ""
            debet = d.debit or Decimal(0)
            kredit = d.kredit or Decimal(0)
            keterangan = (getattr(d, "keterangan", None) or ket_umum)
            data.append(("item", [
                j.tipe_transaksi or "JURNAL",
                tgl,
                j.no_jurnal,
                modul_label,
                j.ref_no or "",
                jurnal_cell,
                "D" if debet > 0 else "C",
                float(debet) if debet > 0 else None,
                float(kredit) if kredit > 0 else None,
                keterangan,
                status,
            ]))
            total_debet += debet
            total_kredit += kredit
    data.append(("total", ["TOTAL", None, None, None, None, None, None,
                           float(total_debet), float(total_kredit), None, None]))

    if tanggal_mulai and tanggal_akhir:
        period = f"Periode: {tanggal_mulai:%d/%m/%Y} - {tanggal_akhir:%d/%m/%Y}"
    elif tanggal_mulai:
        period = f"Periode: mulai {tanggal_mulai:%d/%m/%Y}"
    elif tanggal_akhir:
        period = f"Periode: s.d. {tanggal_akhir:%d/%m/%Y}"
    else:
        period = "Semua Periode"

    wb = excel_service.report_sheet(
        headers=["Posting", "Tanggal Transaksi", "No. Voucher", "Modul Referensi",
                 "No. Referensi", "Jurnal", "D/C", "Debet", "Credit", "Keterangan", "Status"],
        rows=data, sheet="Jurnal Umum",
        company=get_company_name(db),
        title="Jurnal Umum",
        period=period,
        decimal_columns={7, 8}, date_columns={1}, wide_columns={5, 9},
    )
    now = datetime.now()
    return _xlsx_response(wb, f"jurnal-umum_{now:%Y%m%d}_{now:%H%M}.xlsx")


@router.get("/{jurnal_id}", response_model=JurnalUmumDetailResponse)
def get_jurnal_detail(
    jurnal_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Detail jurnal umum beserta semua line items."""
    item = (
        db.query(JurnalUmum)
        .options(
            joinedload(JurnalUmum.creator),
            joinedload(JurnalUmum.details).joinedload(JurnalDetail.akun_perkiraan),
        )
        .filter(JurnalUmum.id == jurnal_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Jurnal Umum tidak ditemukan")
    return item


# ==========================================
# JURNAL MANUAL
# ==========================================
@router.post("/manual", response_model=JurnalUmumDetailResponse, status_code=status.HTTP_201_CREATED)
@atomic_accounting_write
def create_jurnal_manual(
    data_in: JurnalManualCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Jurnal Umum secara manual.

    User menginput sendiri baris-baris debit dan kredit.
    Sistem akan:
    1. Bulatkan tiap nominal ke dua desimal (HALF_UP), lalu validasi balance
    2. Generate nomor jurnal otomatis
    3. Set tipe_transaksi = 'MANUAL' dan ref_module = MANUAL
    """
    # auto_posting_jurnal validates each line after HALF_UP normalization,
    # using the same validator as manual edit and workflow posting.
    # Convert ke JurnalEntryItem
    entries = [
        JurnalEntryItem(
            akun_perkiraan_id=d.akun_perkiraan_id,
            debit=d.debit,
            kredit=d.kredit,
            keterangan=d.keterangan,
        )
        for d in data_in.details
    ]

    try:
        jurnal = auto_posting_jurnal(
            db=db,
            ref_module=RefModule.MANUAL,
            ref_no="",  # Manual jurnal tidak punya ref_no dokumen
            entries=entries,
            keterangan=data_in.keterangan,
            tanggal=data_in.tanggal,
            created_by=current_user.id,
            tipe_transaksi="MANUAL",
            status=StatusJurnal.DRAFT,
            is_manual=True,  # enforce control account ban rule
        )
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        db.rollback()
        raise

    # Administrator (revisi tim akuntansi): langsung diposting tanpa langkah approval.
    # Gagal finalisasi → seluruh request dibatalkan (jurnal tidak tersimpan).
    try:
        workflow_service.direct_complete(db, current_user, 'jurnal_umum', jurnal.id)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Return detail response
    return (
        db.query(JurnalUmum)
        .options(
            joinedload(JurnalUmum.creator),
            joinedload(JurnalUmum.details).joinedload(JurnalDetail.akun_perkiraan),
        )
        .filter(JurnalUmum.id == jurnal.id)
        .first()
    )
