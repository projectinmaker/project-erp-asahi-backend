import enum
from sqlalchemy import Column, String, Numeric, ForeignKey, Enum as SQLEnum, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class JenisKasBank(str, enum.Enum):
    KAS = "KAS"
    BANK = "BANK"


class KasBankAkun(BaseModel, BaseMixin):
    """Master data Kas / Bank.

    Sesuai Master Roadmap §9 Cash/Bank Master:
    P0 minimum: cash_bank_code, name, GL account, type CASH/BANK, currency, status

    Catatan: per Master Roadmap §23, master `saldo` tidak boleh di-edit langsung
    sebagai accounting truth — saldo berasal dari GL. Field `saldo` tetap
    dipertahankan untuk backward compat, tapi service code yang baru membaca
    saldo dari GL (lihat app/services/reporting_ledger.py).
    """
    __tablename__ = "kas_bank_akun"
    __table_args__ = (
        Index("ix_kas_bank_akun_kode", "kode", unique=True, postgresql_where=text("status = 'AKTIF'")),
    )

    # === P0 minimum ===
    kode = Column(String(20), nullable=False)
    nama = Column(String(100), nullable=False)
    jenis = Column(SQLEnum(JenisKasBank), nullable=False)  # type CASH/BANK
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)  # GL account
    status = Column(String(20), default="AKTIF", nullable=False)

    # === NEW Phase 2 — P0 currency ===
    currency = Column(String(3), nullable=False, default='IDR')  # ISO 4217, default IDR

    # === Operational fields (legitimate, bukan accounting truth) ===
    saldo = Column(Numeric(18, 2), default=0, nullable=False)  # snapshot, bukan source of truth

    # Relationships
    akun_perkiraan = relationship("AkunPerkiraan", foreign_keys=[akun_perkiraan_id])
