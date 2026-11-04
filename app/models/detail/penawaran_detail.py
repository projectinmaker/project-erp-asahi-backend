from sqlalchemy import Column, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class PenawaranDetail(BaseModel, BaseMixin):
    __tablename__ = "penawaran_detail"

    penawaran_id = Column(UUID(as_uuid=True), ForeignKey("penawaran.id"), nullable=False)
    # === Update #031 — item baris BEBAS di penawaran ===
    # barang_id kini nullable: baris bisa berupa item bebas yang diketik manual
    # (belum ada di master barang — item penawaran memang "belum ada di gudang").
    # Untuk baris bebas, item_nama/item_satuan wajib terisi dan katalog_id
    # menunjuk baris katalog penawaran (upsert saat simpan).
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=True)
    katalog_id = Column(UUID(as_uuid=True), ForeignKey("penawaran_katalog_item.id"), nullable=True)
    item_nama = Column(String(200), nullable=True)
    item_satuan = Column(String(50), nullable=True)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=True)
    qty = Column(Integer, default=0, nullable=False)
    harga = Column(Numeric(18, 2), default=0, nullable=False)
    diskon = Column(Numeric(5, 2), default=0, nullable=True)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    keterangan = Column(String(255), nullable=True)

    # Relationships
    penawaran = relationship("Penawaran", back_populates="details")
    barang = relationship("Barang")
    satuan = relationship("Satuan", foreign_keys=[satuan_id])
    katalog = relationship("PenawaranKatalogItem")
