from fastapi import APIRouter, Depends
from app.api.permissions import module_access

api_router = APIRouter()

# System Check
@api_router.get("/ping", tags=["System"])
async def ping():
    return {"message": "pong", "version": "v1"}

# ==========================================
# Endpoint routers
# ==========================================
from app.api.v1.endpoints import (
    coa, auth, master,
    kas_bank, penjualan, pembelian,
    persediaan, aset_tetap,
    jurnal, pengguna, karyawan,
    dashboard, laporan, penutupan_periode, rekonsiliasi_bank,
    stok_kartu,
)

api_router.include_router(coa.router, prefix="/coa", tags=["Chart of Accounts"], dependencies=[Depends(module_access("coa"))])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(master.router, prefix="/master", tags=["Master Data"], dependencies=[Depends(module_access("master"))])
api_router.include_router(kas_bank.router, prefix="/kas-bank", tags=["Kas & Bank"], dependencies=[Depends(module_access("kas_bank"))])
api_router.include_router(penjualan.router, prefix="/penjualan", tags=["Penjualan"], dependencies=[Depends(module_access("penjualan"))])
api_router.include_router(pembelian.router, prefix="/pembelian", tags=["Pembelian"], dependencies=[Depends(module_access("pembelian"))])
api_router.include_router(persediaan.router, prefix="/persediaan", tags=["Persediaan"], dependencies=[Depends(module_access("persediaan"))])
api_router.include_router(aset_tetap.router, prefix="/aset-tetap", tags=["Aset Tetap"], dependencies=[Depends(module_access("aset_tetap"))])
api_router.include_router(jurnal.router, prefix="/jurnal", tags=["Jurnal Umum"], dependencies=[Depends(module_access("jurnal"))])
api_router.include_router(pengguna.router, prefix="/pengguna", tags=["Pengguna"], dependencies=[Depends(module_access("pengguna"))])
api_router.include_router(karyawan.router, prefix="/karyawan", tags=["Karyawan"], dependencies=[Depends(module_access("karyawan"))])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"], dependencies=[Depends(module_access("dashboard"))])
api_router.include_router(laporan.router, prefix="/laporan", tags=["Laporan"], dependencies=[Depends(module_access("laporan"))])
api_router.include_router(penutupan_periode.router, prefix="/periode", tags=["Penutupan Periode"], dependencies=[Depends(module_access("penutupan_periode"))])
api_router.include_router(rekonsiliasi_bank.router, prefix="/kas-bank", tags=["Rekonsiliasi Bank"], dependencies=[Depends(module_access("rekonsiliasi_bank"))])
api_router.include_router(stok_kartu.router, tags=["Stok Kartu"], dependencies=[Depends(module_access("stok_kartu"))])

from app.api.v1.endpoints import workflow
api_router.include_router(workflow.router, prefix="/workflow", tags=["Workflow"], dependencies=[Depends(module_access("workflow"))])
