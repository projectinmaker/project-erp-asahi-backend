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
from app.api.v1.endpoints import coa, auth, master

api_router.include_router(coa.router, prefix="/coa", tags=["Chart of Accounts"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(master.router, prefix="/master", tags=["Master Data"])

# from app.api.v1.endpoints.auth import router as auth_router
# api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])

# from app.api.v1.endpoints.coa import router as coa_router
# api_router.include_router(coa_router, prefix="/coa", tags=["Chart of Accounts"])

# from app.api.v1.endpoints.pelanggan import router as pelanggan_router
# api_router.include_router(pelanggan_router, prefix="/pelanggan", tags=["Pelanggan"])

# ... dan seterusnya


# ==========================================
# Placeholder endpoints untuk verifikasi setup
# ==========================================
