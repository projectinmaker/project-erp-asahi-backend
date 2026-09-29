from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.akun_perkiraan import HeaderCOA, TingkatAkun, HEADER_TO_ACCOUNT_CLASS
from app.models.master.pengguna import Pengguna
from app.schemas.coa import (
    COACreate, COAUpdate, COAResponse,
    SaldoAwalRequest, SaldoAwalResponse, NextKodeResponse,
    MigrationApplyRequest, MigrationApplyResponse,
    AccountTypeTemplateResponse, COAParentOptionResponse,
    COAPreviewRequest, COAPreviewResponse,
)
from app.services import coa_service
from app.services import coa_migration_service
from app.services import coa_type_registry
from app.schemas.base import PaginatedResponse

router = APIRouter()


def _coa_to_dict(coa, jenis_kas_bank=None) -> dict:
    """Convert AkunPerkiraan ORM object to dict for COAResponse.

    Includes derived helper flags (is_postable_for_manual, is_legacy_locked,
    is_system_account) supaya frontend bisa langsung tampilkan status tanpa
    perlu re-derive.
    """
    return {
        "id": coa.id,
        "kode": coa.kode,
        "nama": coa.nama,
        "header": coa.header,
        "tingkat": coa.tingkat,
        "induk_id": coa.induk_id,
        "induk_kode": coa.induk_kode,
        "saldo_normal": coa.saldo_normal,
        "status": coa.status,
        "saldo": coa.saldo,
        "tanggal": coa.tanggal,
        "created_at": coa.created_at,
        "updated_at": coa.updated_at,
        "jenis_kas_bank": jenis_kas_bank,
        "is_subledger": coa.is_subledger,
        # === ASAHI COA Revisi v2 — new fields ===
        "account_class": coa.account_class,
        "account_subclass": coa.account_subclass,
        "allow_system_posting": coa.allow_system_posting,
        "allow_manual_posting": coa.allow_manual_posting,
        "is_control_account": coa.is_control_account,
        "subledger_type": coa.subledger_type,
        "financial_statement": coa.financial_statement,
        "report_group": coa.report_group,
        "system_account_type": coa.system_account_type,
        "reconciliation_required": coa.reconciliation_required,
        "active": coa.active,
        # === Derived helper flags ===
        "is_postable_for_manual": coa.is_postable_for_manual,
        "is_postable_for_system": coa.is_postable_for_system,
        "is_legacy_locked": coa.is_legacy_locked,
        "is_system_account": coa.is_system_account,
    }


@router.get("/", response_model=PaginatedResponse[COAResponse])
def read_coa_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    header: Optional[HeaderCOA] = Query(None),
    tingkat: Optional[TingkatAkun] = Query(None),
    search: Optional[str] = Query(None, description="Cari berdasarkan kode atau nama"),
    include_subledger: bool = Query(False, description="Include COA subledger auto-created per pelanggan/supplier (Piutang/Hutang per-customer). Default False (hidden dari list utama)."),
    # === ASAHI COA Revisi v2 — filter baru ===
    is_control_account: Optional[bool] = Query(None, description="Filter akun control account (AR/AP/INVENTORY)"),
    subledger_type: Optional[str] = Query(None, description="Filter by subledger_type: AR / AP / INVENTORY / BANK_TRANSFER"),
    active_only: Optional[bool] = Query(None, description="True=hanya active, False=hanya inactive, None=semua"),
    account_class: Optional[str] = Query(None, description="Filter by account_class: ASSET/LIABILITY/EQUITY/REVENUE/COGS/EXPENSE"),
    allow_manual_posting: Optional[bool] = Query(None, description="True=hanya akun yang bisa dipakai jurnal manual"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Ambil daftar Chart of Accounts (COA) dengan info pagination.

    Response include jenisKasBank ('KAS'/'BANK') jika COA terhubung ke KasBankAkun.
    Secara default, COA subledger (auto-created per pelanggan/supplier, misal
    "Piutang - Budi") disembunyikan dari list ini — set include_subledger=true
    untuk menampilkannya juga.

    Filter baru (ASAHI COA Revisi v2):
    - isControlAccount: filter akun control account
    - subledgerType: filter AR/AP/INVENTORY/BANK_TRANSFER
    - activeOnly: filter akun active/inactive
    - accountClass: filter ASSET/LIABILITY/EQUITY/REVENUE/COGS/EXPENSE
    - allowManualPosting: filter akun yang bisa dipakai jurnal manual
      (berguna untuk dropdown akun di form jurnal manual — frontend bisa
      langsung query ?allowManualPosting=true untuk hide akun yang locked)
    """
    data, total = coa_service.get_coa_list(
        db, skip=skip, limit=limit, header=header, tingkat=tingkat, search=search,
        include_subledger=include_subledger,
        is_control_account=is_control_account,
        subledger_type=subledger_type,
        active_only=active_only,
        account_class=account_class,
        allow_manual_posting=allow_manual_posting,
    )

    result_data = [
        _coa_to_dict(coa, coa_service._get_jenis_kas_bank_for_coa(db, coa.id))
        for coa in data
    ]

    return {"data": result_data, "total": total, "skip": skip, "limit": limit}


@router.post("/", response_model=COAResponse, status_code=status.HTTP_201_CREATED)
def create_coa(
    coa_in: COACreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Tambah Akun Perkiraan (COA) baru.

    MODE BARU (revisi form): kirim `typeCode` (dari GET /coa/account-types)
    + input minimal. Server menurunkan klasifikasi dari template registry:
    header, tingkat, saldoNormal, accountClass, financialStatement,
    reportGroup, aturan posting. Validasi parent compatibility & uniqueness
    kode dilakukan server-side; kode kosong akan di-generate otomatis dari
    parent (untuk sub-akun).

    MODE LEGACY: tanpa typeCode — header/tingkat/saldoNormal wajib manual
    (backward compatibility importer/frontend lama).

    Jika field jenisKasBank diisi ('KAS'/'BANK'), akan auto-membuat
    KasBankAkun yang mengaitkan COA ini ke modul Kas & Bank.
    Opsi ini hanya relevan untuk COA DETAIL di bawah akun Kas dan Setara Kas.

    Flag isControlAccount / systemAccountType / subledgerType hanya boleh
    diatur Admin Finance / Administrator (COA-03) — server menolak payload
    user biasa.
    """
    # === Resolusi template tipe akun (mode baru) ===
    if coa_in.type_code:
        try:
            resolved = coa_type_registry.build_create_payload(
                db, coa_in.model_dump(exclude={"jenis_kas_bank"}), current_user)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        resolved["jenis_kas_bank"] = coa_in.jenis_kas_bank
        resolved["saldo"] = coa_in.saldo
        coa_in = COACreate(**resolved)
    else:
        # === Mode legacy: field wajib harus lengkap ===
        missing = [f for f in ("kode", "header", "tingkat", "saldo_normal") if getattr(coa_in, f) is None]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Field wajib ({', '.join(missing)}) harus diisi bila typeCode tidak dipakai.",
            )
        # Validasi flag control untuk user biasa tetap dijaga di mode legacy
        try:
            coa_type_registry.validate_control_flags(coa_in.model_dump(), current_user)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    existing = coa_service.get_coa_by_kode(db, kode=coa_in.kode)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kode akun '{coa_in.kode}' sudah terdaftar.",
        )

    if coa_in.jenis_kas_bank and coa_in.jenis_kas_bank not in ("KAS", "BANK"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="jenisKasBank harus 'KAS' atau 'BANK'.",
        )

    if coa_in.jenis_kas_bank and coa_in.tingkat != TingkatAkun.DETAIL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="jenisKasBank hanya boleh diisi untuk akun tingkat DETAIL.",
        )

    # === Validasi parent compatibility (mode legacy; mode baru sudah
    # tervalidasi oleh build_create_payload) ===
    if coa_in.induk_id and not coa_in.type_code:
        parent = coa_service.get_coa_by_id(db, coa_in.induk_id)
        if parent is None:
            raise HTTPException(status_code=400, detail="Akun induk tidak ditemukan.")
        if parent.tingkat not in (TingkatAkun.HEADER, TingkatAkun.GROUP):
            raise HTTPException(status_code=400, detail="Akun induk harus level HEADER atau GROUP.")
        if not parent.active:
            raise HTTPException(status_code=400, detail="Akun induk harus berstatus aktif.")
        parent_class = parent.account_class or HEADER_TO_ACCOUNT_CLASS.get(parent.header)
        own_class = coa_in.account_class or HEADER_TO_ACCOUNT_CLASS.get(coa_in.header)
        if parent_class and own_class and parent_class != own_class:
            raise HTTPException(
                status_code=400,
                detail=f"Kelas akun induk ({parent_class}) tidak kompatibel dengan kelas akun baru ({own_class}).",
            )

    coa = coa_service.create_coa(db, coa_in=coa_in)
    return _coa_to_dict(coa, coa_service._get_jenis_kas_bank_for_coa(db, coa.id))


# ==========================================
# SALDO AWAL (sebelum /{coa_id} supaya routing benar)
# ==========================================

@router.get("/saldo-awal", response_model=SaldoAwalResponse)
def get_saldo_awal(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Cek dan ambil saldo awal yang sudah diset."""
    return coa_service.get_saldo_awal(db)


@router.post("/saldo-awal", response_model=SaldoAwalResponse)
def save_saldo_awal(
    req: SaldoAwalRequest,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Set/overwrite saldo awal perusahaan.

    Membuat jurnal SALDO_AWAL (POSTED). Jika sudah pernah diset,
    jurnal lama dibalik, kemudian jurnal baru dibuat dalam satu transaksi.
    """
    from datetime import datetime as dt
    try:
        dt.strptime(req.tanggal, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    items = [
        {
            "akun_perkiraan_id": i.akun_perkiraan_id,
            "kode_akun": i.kode_akun,
            "nama_akun": i.nama_akun,
            "saldo_normal": i.saldo_normal,
            "debit": i.debit,
            "kredit": i.kredit,
        }
        for i in req.items
    ]

    try:
        return coa_service.save_saldo_awal(db, items, req.tanggal, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/next-kode", response_model=NextKodeResponse)
def get_next_kode(
    induk_id: UUID = Query(..., description="ID akun induk (HEADER/GROUP) tempat sub-akun baru akan dibuat"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Generate kode akun berikutnya secara otomatis di bawah akun induk.

    Contoh: induk 111.000.000 -> kode berikutnya 111.000.001, lalu 111.000.002, dst.
    """
    try:
        kode = coa_service.get_next_kode(db, induk_id=induk_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"kode": kode}


# ==========================================
# MIGRATION RUNNER (ASAHI COA Revisi v2)
# ==========================================

@router.get("/migration/preview", response_model=MigrationApplyResponse)
def migration_preview(
    action_filter: Optional[List[str]] = Query(None, description="Filter action: INSERT / UPDATE_CONTROL_RULE / UPDATE_NAME_NOTE / UPDATE_LOCK_LEGACY"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Preview migration map tanpa write ke DB.

    Frontend bisa pakai ini untuk tunjukkin ke user apa yang akan terjadi
    sebelum klik tombol "Apply Migration".
    """
    try:
        return coa_migration_service.apply_migration(
            db, dry_run=True, action_filter=action_filter
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/migration/apply", response_model=MigrationApplyResponse)
def migration_apply(
    req: MigrationApplyRequest,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Apply migration map ke DB.

    Proses:
    1. INSERT akun baru (111200, 530000, 531001)
    2. UPDATE_CONTROL_RULE — set flag control account (112000, 114xxx,
       211000, 322000)
    3. UPDATE_NAME_NOTE — rename akun (330000 Dividen, 510000 legacy)
    4. UPDATE_LOCK_LEGACY — lock akun 511001-511005 (legacy COGS)

    Idempotent: bisa di-run ulang tanpa efek samping (INSERT skip kalau
    kode sudah ada, UPDATE bersifat set-and-forget).
    """
    try:
        return coa_migration_service.apply_migration(
            db, dry_run=req.dry_run, action_filter=req.action_filter
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# REGISTRY TIPE AKUN — revisi form COA
# (harus sebelum /{coa_id} supaya routing benar)
# ==========================================

@router.get("/account-types", response_model=list[AccountTypeTemplateResponse])
def get_account_types(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar template tipe akun (istilah bisnis) dari registry server.

    Form Tambah/Edit Akun memakai ini untuk dropdown Tipe Akun — frontend
    TIDAK menduplikasi klasifikasi di select statis. Setiap template membawa
    default klasifikasi laporan (account_class, financial_statement,
    report_group), saldo normal, dan aturan posting yang akan diturunkan
    server saat create.
    """
    return coa_type_registry.list_templates()


@router.get("/parents", response_model=list[COAParentOptionResponse])
def get_eligible_parents(
    type_code: str = Query(..., description="Kode tipe akun (dari GET /coa/account-types)"),
    exclude_id: Optional[UUID] = Query(None, description="Untuk edit: exclude akun sendiri & descendant-nya (anti siklik)"),
    search: Optional[str] = Query(None, description="Cari berdasarkan kode/nama"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Akun induk yang eligible untuk sebuah tipe akun.

    Syarat: aktif, HEADER/GROUP, kelas akun kompatibel dengan tipe.
    Parent dengan subclass yang cocok ditandai `recommended=true` dan
    muncul paling atas (mis. tipe Kas & Bank → grup Kas dan Setara Kas).
    """
    try:
        return coa_type_registry.eligible_parents(
            db, type_code, exclude_id=exclude_id, search=search)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/preview", response_model=COAPreviewResponse)
def preview_coa(
    req: COAPreviewRequest,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Preview akun baru TANPA menyimpan: kode usulan + klasifikasi turunan.

    Dipakai form saat user memilih tipe akun / parent / mengedit kode —
    server menghitung derived values (header, tingkat, saldoNormal,
    accountClass, reportGroup, aturan posting) + warnings/errors.
    `errors` bersifat blocking (frontend menonaktifkan tombol Simpan);
    `warnings` hanya informatif. Kode usulan di-generate dari parent bila
    kosong; collision dengan kode existing dilaporkan sebagai error.
    """
    try:
        return coa_type_registry.preview_account(
            db,
            type_code=req.type_code,
            is_sub=req.is_sub,
            parent_id=req.induk_id,
            structural_type=req.structural_type,
            kode=req.kode,
            nama=req.nama,
            jenis_kas_bank=req.jenis_kas_bank,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# DETAIL / UPDATE
# ==========================================

@router.get("/{coa_id}", response_model=COAResponse)
def read_coa_detail(
    coa_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Akun Perkiraan berdasarkan ID."""
    coa = coa_service.get_coa_by_id(db, coa_id=coa_id)
    if not coa:
        raise HTTPException(status_code=404, detail="Akun Perkiraan tidak ditemukan")

    return _coa_to_dict(coa, coa_service._get_jenis_kas_bank_for_coa(db, coa.id))


@router.put("/{coa_id}", response_model=COAResponse)
def update_coa(
    coa_id: UUID,
    coa_in: COAUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Akun Perkiraan.

    Field baru yang bisa di-update (ASAHI COA Revisi v2):
    - accountClass, accountSubclass, financialStatement, reportGroup
    - allowSystemPosting, allowManualPosting
    - isControlAccount, subledgerType, reconciliationRequired
    - systemAccountType
    - active (akan otomatis sync ke `status` AKTIF/NONAKTIF)

    Pembatasan (revisi form COA — dijaga di server, bukan hanya UI):
    - Akun yang sudah punya jurnal terposting: perubahan structural/mapping
      (induk, kelas, tipe kontrol, subledger) DITOLAK — tampilkan lock reason
      dari error message.
    - Flag control/system/subledger hanya boleh diatur Admin Finance
      (MANAJER_KEUANGAN) / Administrator.
    - Induk baru harus HEADER/GROUP, kelas kompatibel, dan tidak siklik.
    """
    coa = coa_service.get_coa_by_id(db, coa_id=coa_id)
    if not coa:
        raise HTTPException(status_code=404, detail="Akun Perkiraan tidak ditemukan")

    try:
        coa_type_registry.validate_coa_configuration(
            db, coa, coa_in.model_dump(exclude_unset=True), current_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    updated = coa_service.update_coa(db, db_obj=coa, obj_in=coa_in)
    return _coa_to_dict(updated, coa_service._get_jenis_kas_bank_for_coa(db, updated.id))


@router.delete("/{coa_id}", status_code=status.HTTP_200_OK)
def delete_coa(
    coa_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus Akun Perkiraan (hard delete).

    Hanya berhasil kalau akun belum punya dependency apapun: belum ada
    transaksi jurnal, tidak punya sub-akun, tidak terhubung ke
    pelanggan/supplier/setting_akun/kas_bank_akun. Kalau ada salah satu,
    return 400 dengan pesan jelas — akun sebaiknya dinonaktifkan (PUT
    active=false) bukan dihapus.
    """
    coa = coa_service.get_coa_by_id(db, coa_id=coa_id)
    if not coa:
        raise HTTPException(status_code=404, detail="Akun Perkiraan tidak ditemukan")

    try:
        coa_service.delete_coa(db, coa=coa)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"message": "Akun berhasil dihapus"}
