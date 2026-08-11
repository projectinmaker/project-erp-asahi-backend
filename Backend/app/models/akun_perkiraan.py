import enum
from sqlalchemy import Column, String, Numeric, Integer, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class HeaderCOA(str, enum.Enum):
    AKTIVA = "AKTIVA"
    KEWAJIBAN = "KEWAJIBAN"
    MODAL = "MODAL"
    PENDAPATAN = "PENDAPATAN"
    HPP = "HPP"
    BEBAN = "BEBAN"


class SaldoNormal(str, enum.Enum):
    DEBIT = "DEBIT"
    KREDIT = "KREDIT"


class TingkatAkun(int, enum.Enum):
    HEADER = 1
    GROUP = 2
    DETAIL = 3


class AkunPerkiraan(BaseModel, BaseMixin):
    __tablename__ = "akun_perkiraan"

    kode = Column(String(20), unique=True, nullable=False, index=True)
    nama = Column(String(200), nullable=False)
    header = Column(SQLEnum(HeaderCOA), nullable=False)
    tingkat = Column(SQLEnum(TingkatAkun), nullable=False)

    # Self-referencing untuk hierarchy
    induk_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)
    induk_kode = Column(String(20), nullable=True)  # Denormalized untuk query cepat

    saldo_normal = Column(SQLEnum(SaldoNormal), nullable=False)
    saldo = Column(Numeric(18, 2), default=0, nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)

    # Relationships
    child_accounts = relationship(
        "AkunPerkiraan", back_populates="parent_account", foreign_keys=[induk_id]
    )
    parent_account = relationship(
        "AkunPerkiraan",
        back_populates="child_accounts",
        remote_side="AkunPerkiraan.id",
        foreign_keys=[induk_id],
    )
