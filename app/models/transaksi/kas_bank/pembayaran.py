import enum
from sqlalchemy import Date, Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class StatusTransaksi(str, enum.Enum):
    DRAFT = "DRAFT"
    SELESAI = "SELESAI"
    BATAL = "BATAL"


class PembayaranKas(BaseModel, BaseMixin):
    __tablename__ = "pembayaran_kas"

    no_bukti = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    kas_bank_id = Column(UUID(as_uuid=True), ForeignKey("kas_bank_akun.id"), nullable=False)
    no_nukti = Column(String(50), nullable=False)
    no_cek = Column(String(50), nullable=True)
    # === Update cetak "Bayar Pemasok" — info cek & mata uang untuk layout print ===
    tanggal_cek = Column(Date, nullable=True)
    jumlah_cek = Column(Numeric(18, 2), nullable=True)
    mata_uang = Column(String(8), nullable=False, default="IDR", server_default="IDR")
    nilai_tukar = Column(Numeric(18, 4), nullable=False, default=1, server_default="1")
    penerima = Column(String(255), nullable=True)
    catatan = Column(Text, nullable=True)
    total_nilai = Column(Numeric(18, 2), default=0, nullable=False)
    # Update #5 — penalti pelunasan hutang (menambah total kas dibayar)
    penalti = Column(Numeric(18, 2), default=0, nullable=False)
    akun_penalti_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)
    auto_post_jurnal = Column(Boolean, default=False, nullable=False)  # Phase F: deprecated
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusTransaksi), default=StatusTransaksi.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    supplier_id = Column(UUID(as_uuid=True), ForeignKey("supplier.id"), nullable=True)
    supplier = relationship("Supplier")
    alokasi = relationship("PaymentAllocation", back_populates="pembayaran", cascade="all, delete-orphan")

    # Relationships
    kas_bank = relationship("KasBankAkun")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    akun_penalti = relationship("AkunPerkiraan", foreign_keys=[akun_penalti_id])
    rincian = relationship("PembayaranRincian", back_populates="pembayaran", cascade="all, delete-orphan")
