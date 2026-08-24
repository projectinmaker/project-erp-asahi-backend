"""
Asahi ERP - API v1 Router
Mengumpulkan semua endpoint routers
"""

from fastapi import APIRouter

api_router = APIRouter()

# ==========================================
# Import dan include endpoint routers
# Akan ditambahkan di phase selanjutnya:
# ==========================================


# System Check
@api_router.get("/ping", tags=["System"])
async def ping():
    return {"message": "pong", "version": "v1"}


# Import dan daftarkan router baru
from app.api.v1.endpoints import coa, auth, master, kas_bank, penjualan, pembelian, persediaan, aset_tetap, jurnal, pengguna, karyawan, dashboard, laporan

api_router.include_router(coa.router, prefix="/coa", tags=["Chart of Accounts"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(master.router, prefix="/master", tags=["Master Data"])
api_router.include_router(kas_bank.router, prefix="/kas-bank", tags=["Kas & Bank"])
api_router.include_router(penjualan.router, prefix="/penjualan", tags=["Penjualan"])
api_router.include_router(pembelian.router, prefix="/pembelian", tags=["Pembelian"])
api_router.include_router(persediaan.router, prefix="/persediaan", tags=["Persediaan"])
api_router.include_router(aset_tetap.router, prefix="/aset-tetap", tags=["Aset Tetap"])
api_router.include_router(jurnal.router, prefix="/jurnal", tags=["Jurnal Umum"])
api_router.include_router(pengguna.router, prefix="/pengguna", tags=["Pengguna"])
api_router.include_router(karyawan.router, prefix="/karyawan", tags=["Karyawan"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(laporan.router, prefix="/laporan", tags=["Laporan"])

# ==========================================
# Placeholder endpoints untuk verifikasi setup
# ==========================================
