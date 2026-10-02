import enum

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class StatusRekonsiliasi(enum.StrEnum):
    DRAFT = "DRAFT"
    SELESAI = "SELESAI"
    BATAL = "BATAL"


class TipeRekonsiliasiDetail(enum.StrEnum):
    MEMO = "MEMO"                # Info only, no journal (outstanding cek, deposit in transit)
    PENYESUAIAN = "PENYESUAIAN"  # Will be journalized (bank charges, interest)


class SisiPenyesuaian(enum.StrEnum):
    DEBIT = "DEBIT"
    KREDIT = "KREDIT"


class RekonsiliasiBank(BaseModel, BaseMixin):
    __tablename__ = "rekonsiliasi_bank"
    # B-01 (Update #7): partial unique index — hanya baris status aktif
    # (DRAFT/SELESAI) yang unik per (kas_bank_akun_id, tanggal_akhir).
    # Baris BATAL (hasil void) tidak memblokir periode, sehingga periode yang
    # rekonsiliasinya dibatalkan bisa dibuat ulang. Dibentuk oleh migrasi
    # d7e8f9g0h1i2 sebagai pengganti UniqueConstraint
    # uq_rekonsiliasi_bank_kas_tanggal (yang berlaku untuk semua status).
    __table_args__ = (
        Index(
            "uq_rekonsiliasi_bank_kas_tanggal_active",
            "kas_bank_akun_id",
            "tanggal_akhir",
            unique=True,
            postgresql_where=text("status <> 'BATAL'"),
        ),
    )

    kas_bank_akun_id = Column(UUID(as_uuid=True), ForeignKey("kas_bank_akun.id"), nullable=False)
    tanggal_akhir = Column(DateTime(timezone=True), nullable=False)  # Bank statement date
    saldo_bank = Column(Numeric(18, 2), nullable=False)  # Saldo per rekening koran (user input)
    saldo_buku = Column(Numeric(18, 2), nullable=False)  # Saldo per system (auto-computed)
    selisih = Column(Numeric(18, 2), nullable=False)     # saldo_bank - saldo_buku
    status = Column(String(20), nullable=False, default=StatusRekonsiliasi.DRAFT.value)
    keterangan = Column(Text, nullable=True)
    jurnal_penyesuaian_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    kas_bank = relationship("KasBankAkun")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship(
        "RekonsiliasiBankDetail",
        back_populates="rekonsiliasi",
        cascade="all, delete-orphan",
        order_by="RekonsiliasiBankDetail.created_at",
    )


class RekonsiliasiBankDetail(BaseModel, BaseMixin):
    __tablename__ = "rekonsiliasi_bank_detail"

    rekonsiliasi_bank_id = Column(UUID(as_uuid=True), ForeignKey("rekonsiliasi_bank.id", ondelete="CASCADE"), nullable=False)
    tipe = Column(String(20), nullable=False)  # MEMO or PENYESUAIAN
    keterangan = Column(Text, nullable=False)
    jumlah = Column(Numeric(18, 2), nullable=False)  # Always positive
    sisi = Column(String(10), nullable=False)  # DEBIT or KREDIT
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)  # Required if PENYESUAIAN

    # Relationships
    rekonsiliasi = relationship("RekonsiliasiBank", back_populates="details")
    akun_perkiraan = relationship("AkunPerkiraan")
