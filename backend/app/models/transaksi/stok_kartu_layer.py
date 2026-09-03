"""
StokKartuLayer — Tabel pelacak lapisan harga untuk valuasi FIFO/FEFO.

Setiap kali barang masuk dengan harga tertentu, sebuah 'layer' dibuat.
Saat barang keluar, layer paling lama (FIFO) atau paling dekat kadaluarsa (FEFO)
dikonsumsi terlebih dahulu.

Untuk metode AVERAGE, layer ini TIDAK digunakan — cukup update Barang.harga_pokok.
"""

from sqlalchemy import Column, Integer, Numeric, ForeignKey, DateTime, String, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.jurnal import RefModule


class StokKartuLayer(BaseModel, BaseMixin):
    __tablename__ = "stok_kartu_layer"

    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False, index=True)
    gudang_id = Column(UUID(as_uuid=True), ForeignKey("gudang.id"), nullable=True)

    # Harga satuan saat masuk
    harga_satuan = Column(Numeric(18, 2), nullable=False)

    # Qty yang MASUK dalam layer ini
    qty_masuk = Column(Integer, nullable=False)

    # Qty yang SISA (belum terkonsumsi oleh transaksi keluar)
    qty_sisa = Column(Integer, nullable=False)

    # Tanggal masuk (digunakan FEFO — semakin dekat expiry, semakin diprioritaskan)
    tanggal_masuk = Column(DateTime(timezone=True), nullable=False)

    # Referensi sumber transaksi
    ref_module = Column(SQLEnum(RefModule), nullable=True)
    ref_no = Column(String(30), nullable=True)
    ref_id = Column(UUID(as_uuid=True), nullable=True)

    # Relationships
    barang = relationship("Barang")
    gudang = relationship("Gudang")