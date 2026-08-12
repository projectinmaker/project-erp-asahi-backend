from sqlalchemy import Column, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class Pelanggan(BaseModel, BaseMixin):
    __tablename__ = "pelanggan"
    kode = Column(String(20), unique=True, nullable=False, index=True)
    nama = Column(String(200), nullable=False)
    alamat = Column(Text, nullable=True)
    telepon = Column(String(30), nullable=True)
    email = Column(String(100), nullable=True)
    kontak_person = Column(String(150), nullable=True)
    akun_piutang_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    syarat_bayar_default = Column(String(50), nullable=True, default="Tunai")
    status = Column(String(20), default="AKTIF", nullable=False)

    akun_piutang = relationship("AkunPerkiraan", foreign_keys=[akun_piutang_id])
