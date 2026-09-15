from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class Supplier(BaseModel, BaseMixin):
    """Master data Supplier.

    Sesuai Master Roadmap §9 Supplier:
    P0 minimum fields: kode, nama, status, AP mapping (akun_hutang_id)
    Optional fields (nullable): NPWP, NITKU, address, PIC, phone, email,
        payment term, credit limit, tax status
    """
    __tablename__ = "supplier"
    __table_args__ = (
        Index("ix_supplier_kode", "kode", unique=True, postgresql_where=text("status = 'AKTIF'")),
    )

    # === P0 minimum ===
    kode = Column(String(20), nullable=False)
    nama = Column(String(200), nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)
    akun_hutang_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)

    # === Optional fields (P0 nullable) ===
    alamat = Column(Text, nullable=True)
    telepon = Column(String(30), nullable=True)
    email = Column(String(100), nullable=True)
    kontak_person = Column(String(150), nullable=True)
    npwp = Column(String(50), nullable=True)
    nitku = Column(String(50), nullable=True)  # NEW Phase 2
    syarat_bayar_default = Column(String(50), nullable=True)  # LEGACY string
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id"), nullable=True)  # NEW Phase 2
    credit_limit = Column(Numeric(18, 2), nullable=True)  # NEW Phase 2
    tax_status = Column(String(20), nullable=True)  # NEW Phase 2 — PKP / NON_PKP / NULL

    # Relationships
    akun_hutang = relationship("AkunPerkiraan", foreign_keys=[akun_hutang_id])
    syarat_bayar = relationship("SyaratBayar", foreign_keys=[syarat_bayar_id])

    @property
    def effective_syarat_bayar_id(self):
        """Resolve effective payment term ID — prefer FK baru kalau diisi."""
        return self.syarat_bayar_id
