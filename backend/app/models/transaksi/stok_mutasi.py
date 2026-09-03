import enum
from sqlalchemy import Column, String, Text, Integer, Numeric, ForeignKey, Enum as SQLEnum, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.jurnal import RefModule


class TipeMutasiStok(str, enum.Enum):
    MASUK = "MASUK"
    KELUAR = "KELUAR"
    PENYESUAIAN_TAMBAH = "PENYESUAIAN_TAMBAH"
    PENYESUAIAN_KURANG = "PENYESUAIAN_KURANG"
    PEMINDAHAN_KELUAR = "PEMINDAHAN_KELUAR"
    PEMINDAHAN_MASUK = "PEMINDAHAN_MASUK"


class StokMutasi(BaseModel, BaseMixin):
    __tablename__ = "stok_mutasi"

    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    tipe = Column(SQLEnum(TipeMutasiStok), nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    saldo_sebelum = Column(Integer, default=0, nullable=False)
    saldo_sesudah = Column(Integer, default=0, nullable=False)
    ref_module = Column(SQLEnum(RefModule), nullable=True)
    ref_no = Column(String(30), nullable=True, index=True)
    ref_id = Column(UUID(as_uuid=True), nullable=True)
    gudang_id = Column(UUID(as_uuid=True), ForeignKey("gudang.id"), nullable=True)
    keterangan = Column(Text, nullable=True)

    # ---- GAP #6: Kolom valuasi stok ----
    # Harga satuan yang digunakan dalam transaksi ini
    harga_satuan = Column(Numeric(18, 2), nullable=True)
    # Total nilai = qty * harga_satuan
    total_nilai = Column(Numeric(18, 2), nullable=True)
    # Total nilai stok sebelum transaksi ini
    saldo_nilai_sebelum = Column(Numeric(18, 2), nullable=True)
    # Total nilai stok sesudah transaksi ini
    saldo_nilai_sesudah = Column(Numeric(18, 2), nullable=True)

    # Relationships
    barang = relationship("Barang")
    gudang = relationship("Gudang")