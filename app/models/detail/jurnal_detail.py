from sqlalchemy import Column, String, Numeric, ForeignKey, CheckConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class JurnalDetail(BaseModel, BaseMixin):
    __tablename__ = "jurnal_detail"
    # AUD-02: baris jurnal satu sisi & non-negatif ditegakkan di DB
    # (migration cp0000000014) — sama dengan aturan validate_entries.
    __table_args__ = (
        CheckConstraint("debit >= 0 AND kredit >= 0", name="ck_jurnal_detail_nonneg"),
        CheckConstraint(
            "(debit > 0 AND kredit = 0) OR (kredit > 0 AND debit = 0)",
            name="ck_jurnal_detail_one_sided",
        ),
        Index("ix_jurnal_detail_jurnal_umum_id", "jurnal_umum_id"),
        Index("ix_jurnal_detail_akun_perkiraan_id", "akun_perkiraan_id"),
    )

    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=False)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)

    debit = Column(Numeric(18, 2), default=0, nullable=False)
    kredit = Column(Numeric(18, 2), default=0, nullable=False)

    keterangan = Column(String(255), nullable=True)

    # Relationships
    jurnal = relationship("JurnalUmum", back_populates="details")
    akun_perkiraan = relationship("AkunPerkiraan")
