from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.laporan import (
    LabaRugiResponse,
    NeracaResponse,
    ArusKasResponse,
    BukuBesarResponse,
    RekapKasBankResponse,
)
from app.services import laporan_service

router = APIRouter()


# ==========================================
# LAPORAN KEUANGAN
# ==========================================

@router.get("/laba-rugi", response_model=LabaRugiResponse)
def get_laba_rugi(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Laba Rugi."""
    try:
        date_from = datetime.strptime(dari, "%Y-%m-%d")
        date_to = datetime.strptime(sampai, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_laba_rugi(db, date_from, date_to)


@router.get("/neraca", response_model=NeracaResponse)
def get_neraca(
    tanggal: str = Query(..., description="Tanggal posisi neraca (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Neraca (posisi keuangan)."""
    try:
        dt = datetime.strptime(tanggal, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_neraca(db, dt)


@router.get("/arus-kas", response_model=ArusKasResponse)
def get_arus_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Arus Kas."""
    try:
        date_from = datetime.strptime(dari, "%Y-%m-%d")
        date_to = datetime.strptime(sampai, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_arus_kas(db, date_from, date_to)


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
        date_from = datetime.strptime(dari, "%Y-%m-%d")
        date_to = datetime.strptime(sampai, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    try:
        return laporan_service.get_buku_besar(db, akun_id, date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==========================================
# KAS & BANK
# ==========================================

@router.get("/mutasi-kas")
def get_mutasi_kas(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Mutasi Kas."""
    try:
        date_from = datetime.strptime(dari, "%Y-%m-%d")
        date_to = datetime.strptime(sampai, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="KAS")


@router.get("/mutasi-bank")
def get_mutasi_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Laporan Mutasi Bank."""
    try:
        date_from = datetime.strptime(dari, "%Y-%m-%d")
        date_to = datetime.strptime(sampai, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_mutasi_kas_bank(db, date_from, date_to, jenis="BANK")


@router.get("/rekap-kas-bank", response_model=RekapKasBankResponse)
def get_rekap_kas_bank(
    dari: str = Query(..., description="Tanggal awal (YYYY-MM-DD)"),
    sampai: str = Query(..., description="Tanggal akhir (YYYY-MM-DD)"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Rekap Kas & Bank (saldo awal, masuk, keluar, akhir per akun)."""
    try:
        date_from = datetime.strptime(dari, "%Y-%m-%d")
        date_to = datetime.strptime(sampai, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")

    return laporan_service.get_rekap_kas_bank(db, date_from, date_to)
