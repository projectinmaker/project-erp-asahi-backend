from sqlalchemy import Column, String
from app.database import BaseModel
from app.models.base import BaseMixin


class Satuan(BaseModel, BaseMixin):
    __tablename__ = "satuan"
    nama = Column(String(20), nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)
