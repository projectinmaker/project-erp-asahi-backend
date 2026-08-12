from sqlalchemy import Column, String
from app.database import BaseModel
from app.models.base import BaseMixin


class KategoriAset(BaseModel, BaseMixin):
    __tablename__ = "kategori_aset"
    kode = Column(String(20), nullable=False)
    nama = Column(String(100), nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)
