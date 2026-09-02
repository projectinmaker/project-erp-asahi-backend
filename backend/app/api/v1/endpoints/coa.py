from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.akun_perkiraan import HeaderCOA, TingkatAkun
from app.models.master.pengguna import Pengguna
from app.schemas.coa import COACreate, COAUpdate, COAResponse, SaldoAwalRequest, SaldoAwalResponse
from app.services import coa_service
from app.schemas.base import PaginatedResponse

router = APIRouter()


def _coa_to_dict(coa, jenis_kas_bank=None) -> dict:
    """Convert AkunPerkiraan ORM object to dict for COAResponse."""
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
    }


@router.get("/", response_model=PaginatedResponse[COAResponse])
def read_coa_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    header: Optional[HeaderCOA] = Query(None),
    tingkat: Optional[TingkatAkun] = Query(None),
    search: Optional[str] = Query(None, description="Cari berdasarkan kode atau nama"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Ambil daftar Chart of Accounts (COA) dengan info pagination.

    Response include jenisKasBank ('KAS'/'BANK') jika COA terhubung ke KasBankAkun.
    """
    data, total = coa_service.get_coa_list(
        db, skip=skip, limit=limit, header=header, tingkat=tingkat, search=search
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

    Jika field jenisKasBank diisi ('KAS'/'BANK'), akan auto-membuat
    KasBankAkun yang mengaitkan COA ini ke modul Kas & Bank.
    Opsi ini hanya relevan untuk COA DETAIL di bawah akun Kas dan Setara Kas.
    """
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
    jurnal lama akan dihapus dan diganti yang baru.
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
    """Update data Akun Perkiraan."""
    coa = coa_service.get_coa_by_id(db, coa_id=coa_id)
    if not coa:
        raise HTTPException(status_code=404, detail="Akun Perkiraan tidak ditemukan")

    updated = coa_service.update_coa(db, db_obj=coa, obj_in=coa_in)
    return _coa_to_dict(updated, coa_service._get_jenis_kas_bank_for_coa(db, updated.id))
