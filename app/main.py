"""
Asahi ERP - FastAPI Application Entry Point
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import check_database_connection
from app.utils.logging import setup_logging

# Setup logging terlebih dahulu
setup_logging()

# Get settings
settings = get_settings()

# Import loguru
from loguru import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan handler.
    Dieksekusi saat startup dan shutdown.
    """
    # ==========================================
    # STARTUP
    # ==========================================
    logger.info("=" * 60)
    logger.info(f"Starting {settings.APP_NAME}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Debug mode: {settings.DEBUG}")
    logger.info("=" * 60)

    # Check database connection
    db_status = check_database_connection()
    if db_status["status"] == "healthy":
        logger.info("✓ Database connection: OK")
    else:
        logger.error(f"✗ Database connection: FAILED - {db_status.get('error')}")

    logger.info("Application startup complete")
    logger.info("=" * 60)

    yield  # Application running

    # ==========================================
    # SHUTDOWN
    # ==========================================
    logger.info("=" * 60)
    logger.info("Application shutting down...")
    logger.info("=" * 60)


def create_application() -> FastAPI:
    """
    Factory function untuk membuat FastAPI application.
    Memisahkan creation dari running untuk memudahkan testing.
    """

    app = FastAPI(
        title=settings.APP_NAME,
        description="""
        ## Asahi ERP Backend API

        ### Modules
        - **Pengaturan**: COA, Pengguna, Karyawan, Pelanggan, Supplier
        - **Kas & Bank**: Pembayaran, Penerimaan, Transfer Bank
        - **Penjualan**: Sales Order, Pengiriman, Invoice, Retur
        - **Pembelian**: Purchase Order, Penerimaan Barang, Invoice, Retur
        - **Persediaan**: Permintaan, Pemindahan, Penyesuaian Stok
        - **Aset Tetap**: Kategori Aset, Aset Tetap (penyusutan auto)
        - **Buku Besar**: Jurnal Umum
        - **Laporan**: Berbagai laporan keuangan

        ### Tech Stack
        - Python 3.11+
        - FastAPI
        - SQLAlchemy 2.0
        - PostgreSQL 16
        """,
        version="0.1.0",
        debug=settings.DEBUG,
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
    )

    # ==========================================
    # Middleware
    # ==========================================

    # CORS Middleware - penting untuk Next.js frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ==========================================
    # Exception Handlers
    # ==========================================

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle semua uncaught exceptions"""
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Internal server error",
                "detail": str(exc) if settings.is_development else "An unexpected error occurred",
            },
        )

    # ==========================================
    # Routes
    # ==========================================

    # Health check endpoint
    @app.get("/health", tags=["Health"])
    async def health_check():
        """Cek status aplikasi dan database"""
        db_status = check_database_connection()
        return {
            "status": "ok" if db_status["status"] == "healthy" else "degraded",
            "app": settings.APP_NAME,
            "version": "0.1.0",
            "environment": settings.ENVIRONMENT,
            "database": db_status,
        }

    # Root endpoint
    @app.get("/", tags=["Root"])
    async def root():
        """Root endpoint - API info"""
        return {
            "name": settings.APP_NAME,
            "version": "0.1.0",
            "docs": "/docs" if settings.is_development else None,
        }

    # API v1 Router (akan diisi di phase selanjutnya)
    from app.api.v1.router import api_router

    app.include_router(api_router, prefix="/api/v1")

    return app


# Create application instance
app = create_application()


# ==========================================
# Direct Run Entry Point
# ==========================================
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting development server...")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Auto-reload saat code berubah
        log_level="info",
        access_log=True,
    )
