from sqlalchemy import Column, Numeric, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PenerimaanRincian(BaseModel, BaseMixin):
    __tablename__ = "penerimaan_rincian"

    penerimaan_id = Column(UUID(as_uuid=True), ForeignKey("penerimaan_kas.id"), nullable=False)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    nilai = Column(Numeric(18, 2), default=0, nullable=False)
    # === Update #16 — keterangan per baris rincian (paritas Accurate "Penerimaan
    # Lain": kolom Catatan pada grid detail) ===
    keterangan = Column(Text, nullable=True)

    # Relationships
    penerimaan = relationship("PenerimaanKas", back_populates="rincian")
    akun_perkiraan = relationship("AkunPerkiraan")
