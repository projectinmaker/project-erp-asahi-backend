from sqlalchemy import Column, String, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class JurnalDetail(BaseModel, BaseMixin):
    __tablename__ = "jurnal_detail"

    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=False)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)

    debit = Column(Numeric(18, 2), default=0, nullable=False)
    kredit = Column(Numeric(18, 2), default=0, nullable=False)

    keterangan = Column(String(255), nullable=True)

    # Relationships
    jurnal = relationship("JurnalUmum", back_populates="details")
    akun_perkiraan = relationship("AkunPerkiraan")
