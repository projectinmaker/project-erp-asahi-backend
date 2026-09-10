from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from app.database import BaseModel
from app.models.base import BaseMixin


class AssetEvent(BaseModel, BaseMixin):
    __tablename__ = 'asset_event'
    aset_id = Column(UUID(as_uuid=True), ForeignKey('aset_tetap.id'), nullable=False, index=True)
    jenis = Column(String(24), nullable=False)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(16), nullable=False, default='DRAFT')
    total = Column(Numeric(18, 2), nullable=False, default=0)
    parameter = Column(JSON, nullable=False)
    sebelum = Column(JSON, nullable=True)
    sesudah = Column(JSON, nullable=True)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey('jurnal_umum.id'), nullable=True)
    source_journal_id = Column(UUID(as_uuid=True), ForeignKey('jurnal_umum.id'), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=False)
