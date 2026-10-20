"""AUD-14 (update #04) — Closing tahunan (transfer laba berjalan → laba ditahan).

Mirror penutupan_periode (bulanan): status tahun buku + jurnal transfer
saldo LABA_RUGI_TAHUN_BERJALAN (322000) → LABA_DITAHAN (321000), dibuat
SEBELUM flag DITUTUP (guard periode), dibalik saat tahun dibuka kembali.
"""
import enum

from sqlalchemy import (Column, String, Integer, Text, ForeignKey, DateTime,
    UniqueConstraint, Numeric, Index)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class StatusTahun(str, enum.Enum):
    DITUTUP = "DITUTUP"
    DIBUKA = "DIBUKA"


class PenutupanTahun(BaseModel, BaseMixin):
    __tablename__ = "penutupan_tahun"
    __table_args__ = (
        # AUD-17 (update #05): tutup tahun global (NULL) atau per perusahaan.
        UniqueConstraint("tahun", "company_id", name="uq_penutupan_tahun_tahun_company",
                        postgresql_nulls_not_distinct=True),
        Index("ix_penutupan_tahun_company", "company_id"),
    )

    tahun = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default=StatusTahun.DITUTUP.value)
    keterangan = Column(Text, nullable=True)
    # AUD-17: NULL = global; terisi = khusus perusahaan.
    company_id = Column(UUID(as_uuid=True), ForeignKey("organization_unit.id"), nullable=True)
    # Jurnal transfer: Dr 322000 Laba (Rugi) Tahun Berjalan / Cr 321000 Laba
    # (Rugi) Ditahan (laba) — atau kebalikannya untuk rugi. ref_no CL-YEAR-YYYY,
    # ref_module PENUTUPAN_PERIODE (otomatis dikecualikan dari laba rugi).
    jurnal_penutupan_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    laba_rugi = Column(Numeric(18, 2), nullable=True)  # laba/rugi tahun ini

    closed_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    reopened_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=True)
    reopened_at = Column(DateTime(timezone=True), nullable=True)
    reopen_alasan = Column(Text, nullable=True)

    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    jurnal = relationship("JurnalUmum")
    closer = relationship("Pengguna", foreign_keys=[closed_by])
    reopener = relationship("Pengguna", foreign_keys=[reopened_by])
    creator = relationship("Pengguna", foreign_keys=[created_by])
