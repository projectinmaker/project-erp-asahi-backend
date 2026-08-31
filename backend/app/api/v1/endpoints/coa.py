from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.akun_perkiraan import HeaderCOA, TingkatAkun
from app.models.master.pengguna import Pengguna
from app.schemas.coa import COACreate, COAUpdate, COAResponse
from app.services import coa_service
from app.schemas.base import PaginatedResponse

router = APIRouter()


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

    # Inject jenis_kas_bank ke setiap item
    result_data = []
    for coa in data:
        jkb = coa_service._get_jenis_kas_bank_for_coa(db, coa.id)
        result_data.append({
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
            "created_at": coa.created_at,
            "updated_at": coa.updated_at,
            "jenis_kas_bank": jkb,
        })

    return {
        "data": result_data,
        "total": total,
        "skip": skip,
        "limit": limit
    }


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

    # Validasi jenis_kas_bank
    if coa_in.jenis_kas_bank and coa_in.jenis_kas_bank not in ("KAS", "BANK"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="jenisKasBank harus 'KAS' atau 'BANK'.",
        )

    # Validasi: jenis_kas_bank hanya untuk DETAIL
    if coa_in.jenis_kas_bank and coa_in.tingkat != TingkatAkun.DETAIL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="jenisKasBank hanya boleh diisi untuk akun tingkat DETAIL.",
        )

    coa = coa_service.create_coa(db, coa_in=coa_in)

    # Inject jenis_kas_bank di response
    jkb = coa_service._get_jenis_kas_bank_for_coa(db, coa.id)
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
        "created_at": coa.created_at,
        "updated_at": coa.updated_at,
        "jenis_kas_bank": jkb,
    }


@router.get("/{coa_id}", response_model=COAResponse)
def read_coa_detail(
    coa_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Akun Perkiraan berdasarkan ID.

    Response include jenisKasBank ('KAS'/'BANK') jika COA terhubung ke KasBankAkun.
    """
    coa = coa_service.get_coa_by_id(db, coa_id=coa_id)
    if not coa:
        raise HTTPException(status_code=404, detail="Akun Perkiraan tidak ditemukan")

    jkb = coa_service._get_jenis_kas_bank_for_coa(db, coa.id)
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
        "created_at": coa.created_at,
        "updated_at": coa.updated_at,
        "jenis_kas_bank": jkb,
    }


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

    jkb = coa_service._get_jenis_kas_bank_for_coa(db, updated.id)
    return {
        "id": updated.id,
        "kode": updated.kode,
        "nama": updated.nama,
        "header": updated.header,
        "tingkat": updated.tingkat,
        "induk_id": updated.induk_id,
        "induk_kode": updated.induk_kode,
        "saldo_normal": updated.saldo_normal,
        "status": updated.status,
        "saldo": updated.saldo,
        "created_at": updated.created_at,
        "updated_at": updated.updated_at,
        "jenis_kas_bank": jkb,
    }
