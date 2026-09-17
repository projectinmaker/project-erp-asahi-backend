"""Warehouse quantity/value control, with an explicit unassigned legacy bucket."""
from sqlalchemy import Column, String, Integer, Numeric, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.database import BaseModel
from app.models.base import BaseMixin


class StockBalance(BaseModel, BaseMixin):
    __tablename__ = 'stock_balance'
    __table_args__ = (
        UniqueConstraint('barang_id', 'location_key', name='uq_stock_location'),
        CheckConstraint('qty >= 0 AND nilai >= 0', name='ck_stock_nonnegative'),
    )
    barang_id = Column(UUID(as_uuid=True), ForeignKey('barang.id'), nullable=False, index=True)
    gudang_id = Column(UUID(as_uuid=True), ForeignKey('gudang.id'), nullable=True)
    location_key = Column(String(36), nullable=False)
    qty = Column(Integer, nullable=False, default=0)
    nilai = Column(Numeric(18, 2), nullable=False, default=0)
