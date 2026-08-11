"""
Asahi ERP - API Dependencies
Common dependencies untuk digunakan di endpoint routers
"""

from typing import Generator
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db

# Dependency untuk mendapatkan database session
# Sudah didefinisikan di database.py, re-export di sini untuk kemudahan
DbSession = Generator[Session, None, None]


def get_current_db() -> DbSession:
    """
    Dependency untuk database session.
    Wrapper sederhana, akan diperluas dengan auth di phase selanjutnya.
    """
    return Depends(get_db)


# Placeholder untuk auth dependency
# Akan diimplementasi di phase authentication
# async def get_current_user(...) -> User:
#     ...
