from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.kas_bank.pembayaran import StatusTransaksi


class TransferBank(BaseModel, BaseMixin):
    __tablename__ = "transfer_bank"

    no_transfer = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    dari_kas_bank_id = Column(UUID(as_uuid=True), ForeignKey("kas_bank_akun.id"), nullable=False)
    ke_kas_bank_id = Column(UUID(as_uuid=True), ForeignKey("kas_bank_akun.id"), nullable=False)
    nilai_transfer = Column(Numeric(18, 2), default=0, nullable=False)
    biaya_transfer = Column(Numeric(18, 2), default=0, nullable=False)
    informasi = Column(Text, nullable=True)
    auto_post_jurnal = Column(Boolean, default=True, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusTransaksi), default=StatusTransaksi.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    dari_kas_bank = relationship("KasBankAkun", foreign_keys=[dari_kas_bank_id])
    ke_kas_bank = relationship("KasBankAkun", foreign_keys=[ke_kas_bank_id])
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
