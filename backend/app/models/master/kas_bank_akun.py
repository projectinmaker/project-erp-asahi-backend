import enum
from sqlalchemy import Column, String, Numeric, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class JenisKasBank(str, enum.Enum):
    KAS = "KAS"
    BANK = "BANK"


class KasBankAkun(BaseModel, BaseMixin):
    __tablename__ = "kas_bank_akun"
    kode = Column(String(20), unique=True, nullable=False, index=True)
    nama = Column(String(100), nullable=False)
    jenis = Column(SQLEnum(JenisKasBank), nullable=False)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    saldo = Column(Numeric(18, 2), default=0, nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)

    akun_perkiraan = relationship("AkunPerkiraan", foreign_keys=[akun_perkiraan_id])
