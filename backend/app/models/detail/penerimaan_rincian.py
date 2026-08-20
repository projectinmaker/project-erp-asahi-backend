from sqlalchemy import Column, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PenerimaanRincian(BaseModel, BaseMixin):
    __tablename__ = "penerimaan_rincian"

    penerimaan_id = Column(UUID(as_uuid=True), ForeignKey("penerimaan_kas.id"), nullable=False)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    nilai = Column(Numeric(18, 2), default=0, nullable=False)

    # Relationships
    penerimaan = relationship("PenerimaanKas", back_populates="rincian")
    akun_perkiraan = relationship("AkunPerkiraan")
