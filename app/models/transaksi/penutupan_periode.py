import enum
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (Column, String, Integer, Text, Boolean, ForeignKey, DateTime,
    UniqueConstraint, Numeric)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class StatusPeriode(str, enum.Enum):
    DITUTUP = "DITUTUP"
    DIBUKA = "DIBUKA"


class PenutupanPeriode(BaseModel, BaseMixin):
    __tablename__ = "penutupan_periode"
    __table_args__ = (
        UniqueConstraint("tahun", "bulan", name="uq_penutupan_periode_tahun_bulan"),
    )

    tahun = Column(Integer, nullable=False)
    bulan = Column(Integer, nullable=False)  # 1-12
    status = Column(String(20), nullable=False, default=StatusPeriode.DITUTUP.value)
    keterangan = Column(Text, nullable=True)
    jurnal_penutupan_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    laba_rugi = Column(Numeric(18, 2), nullable=True)  # Laba/rugi periode ini

    closed_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    reopened_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=True)
    reopened_at = Column(DateTime(timezone=True), nullable=True)

    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    jurnal = relationship("JurnalUmum")
    closer = relationship("Pengguna", foreign_keys=[closed_by])
    reopener = relationship("Pengguna", foreign_keys=[reopened_by])
    creator = relationship("Pengguna", foreign_keys=[created_by])
