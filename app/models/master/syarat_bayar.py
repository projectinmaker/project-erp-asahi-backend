from sqlalchemy import Column, String, Integer
from app.database import BaseModel
from app.models.base import BaseMixin


class SyaratBayar(BaseModel, BaseMixin):
    __tablename__ = "syarat_bayar"
    nama = Column(String(50), nullable=False)  # Tunai, Net 30, dll
    hari = Column(Integer, nullable=True)  # NULL untuk Tunai
