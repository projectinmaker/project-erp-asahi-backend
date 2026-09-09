from sqlalchemy import Column, String, Text, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class Pelanggan(BaseModel, BaseMixin):
    __tablename__ = "pelanggan"
    __table_args__ = (
        Index("ix_pelanggan_kode", "kode", unique=True, postgresql_where=text("status = 'AKTIF'")),
    )
    kode = Column(String(20), nullable=False)
    nama = Column(String(200), nullable=False)
    alamat = Column(Text, nullable=True)
    telepon = Column(String(30), nullable=True)
    email = Column(String(100), nullable=True)
    kontak_person = Column(String(150), nullable=True)
    akun_piutang_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)
    npwp = Column(String(50), nullable=True)
    syarat_bayar_default = Column(String(50), nullable=True, default="Tunai")
    status = Column(String(20), default="AKTIF", nullable=False)

    akun_piutang = relationship("AkunPerkiraan", foreign_keys=[akun_piutang_id])
