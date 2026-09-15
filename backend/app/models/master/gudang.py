from sqlalchemy import Column, String, Text, Integer, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class Gudang(BaseModel, BaseMixin):
    """Master data Gudang / Warehouse.

    Sesuai Master Roadmap §9 Warehouse:
    P0 minimum: warehouse_code, warehouse_name, status, organization scope
    """
    __tablename__ = "gudang"
    __table_args__ = (
        Index("ix_gudang_kode", "kode", unique=True, postgresql_where=text("status = 'AKTIF'")),
    )

    # === P0 minimum ===
    kode = Column(String(20), nullable=False)
    nama = Column(String(100), nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)

    # === Organization scope (NEW Phase 2) ===
    company_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'), nullable=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'), nullable=True)

    # === Operational fields ===
    alamat = Column(Text, nullable=True)
    total_barang = Column(Integer, default=0, nullable=False)

    # Relationships
    company = relationship('OrganizationUnit', foreign_keys=[company_id])
    branch = relationship('OrganizationUnit', foreign_keys=[branch_id])
