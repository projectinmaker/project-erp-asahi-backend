from sqlalchemy import Column, String, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class SettingAkun(BaseModel, BaseMixin):
    __tablename__ = "setting_akun"
    __table_args__ = (
        # AUD-17 (update #05): override per perusahaan — (key, company_id)
        # NULLS NOT DISTINCT; baris company NULL = fallback global.
        UniqueConstraint("key", "company_id", name="uq_setting_akun_key_company",
                         postgresql_nulls_not_distinct=True),
        Index("ix_setting_akun_company", "company_id"),
    )

    key = Column(String(100), nullable=False, index=True)
    label = Column(String(200), nullable=False)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    # AUD-17: NULL = global (berlaku semua perusahaan); terisi = override
    # khusus perusahaan tsb (lookup: (key, company) → fallback (key, NULL)).
    company_id = Column(UUID(as_uuid=True), ForeignKey("organization_unit.id"), nullable=True)

    # Relationships
    akun_perkiraan = relationship("AkunPerkiraan")
    company = relationship("OrganizationUnit")
