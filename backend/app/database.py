"""
Asahi ERP - Database Configuration
SQLAlchemy engine, session factory, dan dependency injection
"""

from typing import Generator

from loguru import logger
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker, declarative_base

from app.config import get_settings

# Get settings
settings = get_settings()

# ==========================================
# SQLAlchemy Engine
# ==========================================
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,  # Recycle connections after 1 hour
    echo=settings.DATABASE_ECHO,
)


# ==========================================
# Connection Pool Events (for debugging)
# ==========================================
@event.listens_for(Engine, "connect")
def receive_connect(dbapi_conn: object, connection_record: object) -> None:
    """Log saat koneksi baru dibuat"""
    if settings.is_development:
        logger.debug("New database connection established")


@event.listens_for(Engine, "checkout")
def receive_checkout(
    dbapi_conn: object, connection_record: object, connection_proxy: object
) -> None:
    """Log saat koneksi di-checkout dari pool"""
    if settings.is_development:
        logger.trace("Connection checked out from pool")


@event.listens_for(Engine, "checkin")
def receive_checkin(dbapi_conn: object, connection_record: object) -> None:
    """Log saat koneksi dikembalikan ke pool"""
    if settings.is_development:
        logger.trace("Connection returned to pool")


# ==========================================
# Session Factory
# ==========================================
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ==========================================
# Declarative Base
# ==========================================
class Base:
    """Base class for all SQLAlchemy models"""

    pass


# Create declarative base with custom Base class
from sqlalchemy.orm import DeclarativeBase


class BaseModel(DeclarativeBase):
    """
    SQLAlchemy declarative base untuk semua models.
    Semua model harus inherit dari class ini.
    """

    pass


# ==========================================
# Dependency Injection
# ==========================================
def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency untuk mendapatkan database session.

    Usage:
        @router.get("/items/")
        def read_items(db: Session = Depends(get_db)):
            ...

    Session akan otomatis di-close setelah request selesai,
    bahkan jika terjadi exception.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================
# Database Health Check
# ==========================================
def check_database_connection() -> dict:
    """
    Cek koneksi database. Return dict dengan status.

    Returns:
        dict: {"status": "healthy", "pool_size": N, "checked_out": N}
        atau {"status": "unhealthy", "error": "..."}
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        pool = engine.pool
        return {
            "status": "healthy",
            "database": settings.DATABASE_URL.split("@")[-1].split("/")[0],
            "pool_size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
        }


# ==========================================
# Module Init Check
# ==========================================
def init_database() -> None:
    """
    Inisialisasi database - dipanggil saat app startup.
    Sekarang hanya log, migration dilakukan via Alembic.
    """
    logger.info("Database engine configured")
    logger.info(f"Database URL: {settings.DATABASE_URL.split('@')[-1]}")
    logger.info(f"Pool size: {settings.DATABASE_POOL_SIZE}")
    logger.info(f"Max overflow: {settings.DATABASE_MAX_OVERFLOW}")
