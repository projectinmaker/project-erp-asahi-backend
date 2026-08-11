import enum
from sqlalchemy import Column, String, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class Departemen(str, enum.Enum):
    DIREKSI = "DIREKSI"
    KEUANGAN = "KEUANGAN"
    AKUNTANSI = "AKUNTANSI"
    GUDANG = "GUDANG"
    PENJUALAN = "PENJUALAN"
    ADMINISTRASI = "ADMINISTRASI"
    PRODUKSI = "PRODUKSI"


class Karyawan(BaseModel, BaseMixin):
    __tablename__ = "karyawan"
    nik = Column(String(20), unique=True, nullable=False, index=True)
    nama = Column(String(100), nullable=False)
    jabatan = Column(String(100), nullable=True)
    departemen = Column(SQLEnum(Departemen), nullable=True)
    email = Column(String(100), nullable=True)
    no_hp = Column(String(20), nullable=True)
    akun_piutang_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)

    akun_piutang = relationship("AkunPerkiraan", foreign_keys=[akun_piutang_id])
