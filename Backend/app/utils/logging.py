"""
Asahi ERP - Logging Configuration
Menggunakan loguru untuk logging yang lebih mudah
"""

import sys
from loguru import logger

from app.config import get_settings

settings = get_settings()


def setup_logging() -> None:
    """
    Configure loguru logger berdasarkan environment.
    Dipanggil sekali saat app startup.
    """
    # Remove default handler
    logger.remove()

    # Console output dengan format warna
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        format=log_format,
        level=settings.LOG_LEVEL,
        colorize=True,
        backtrace=True if settings.is_development else False,
        diagnose=True if settings.is_development else False,
    )

    # File logging (hanya jika bukan development atau jika diinginkan)
    if not settings.is_development:
        logger.add(
            "logs/asahi_erp_{time:YYYY-MM-DD}.log",
            rotation="00:00",  # Rotate setiap hari
            retention="30 days",  # Simpan 30 hari
            compression="gz",  # Compress old logs
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        )

    logger.info(f"Logging initialized - Level: {settings.LOG_LEVEL}")
