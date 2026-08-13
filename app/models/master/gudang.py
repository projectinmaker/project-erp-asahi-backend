from sqlalchemy import Column, String, Text, Integer
from app.database import BaseModel
from app.models.base import BaseMixin


class Gudang(BaseModel, BaseMixin):
    __tablename__ = "gudang"
    kode = Column(String(20), nullable=False)
    nama = Column(String(100), nullable=False)
    alamat = Column(Text, nullable=True)
    total_barang = Column(Integer, default=0, nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)
