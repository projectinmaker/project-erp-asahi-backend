import enum
from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class RefModule(str, enum.Enum):
    PEMBAYARAN = "PEMBAYARAN"
    PENERIMAAN = "PENERIMAAN"
    TRANSFER_BANK = "TRANSFER_BANK"
    SALES_ORDER = "SALES_ORDER"
    SALES_INVOICE = "SALES_INVOICE"
    SALES_RETUR = "SALES_RETUR"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    PURCHASE_INVOICE = "PURCHASE_INVOICE"
    PURCHASE_RETUR = "PURCHASE_RETUR"
    PENYESUAIAN_STOK = "PENYESUAIAN_STOK"
    PENYUSUTAN = "PENYUSUTAN"
    MANUAL = "MANUAL"


class StatusJurnal(str, enum.Enum):
    POSTED = "POSTED"
    DRAFT = "DRAFT"


class JurnalUmum(BaseModel, BaseMixin):
    __tablename__ = "jurnal_umum"

    no_jurnal = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(String(10), nullable=False)  # Format YYYY-MM-DD

    tipe_transaksi = Column(String(100), nullable=True)
    ref_module = Column(SQLEnum(RefModule), nullable=True)
    ref_no = Column(String(30), nullable=True, index=True)
    ref_id = Column(UUID(as_uuid=True), nullable=True)

    total_debit = Column(Numeric(18, 2), default=0, nullable=False)
    total_kredit = Column(Numeric(18, 2), default=0, nullable=False)

    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusJurnal), default=StatusJurnal.DRAFT, nullable=False)

    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    details = relationship("JurnalDetail", back_populates="jurnal", cascade="all, delete-orphan")
    creator = relationship("Pengguna", foreign_keys=[created_by])
