import enum
from sqlalchemy import Column, String, Integer, Numeric, Boolean, ForeignKey, Enum as SQLEnum, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class MetodeValuasi(str, enum.Enum):
    """Metode valuasi persediaan (P0 Roadmap §9 Product: inventory_method)."""
    AVERAGE = "AVERAGE"
    FIFO = "FIFO"
    FEFO = "FEFO"


class ItemTypeBarang(str, enum.Enum):
    """Tipe barang / item (P0 Roadmap §9 Product: item_type).

    Mapping canonical:
    - BARANG_DAGANG: barang dagang untuk dijual kembali (trading goods)
    - BARANG_JADI:  produk jadi (finished goods) hasil produksi
    - BARANG_BAKU:  bahan baku untuk produksi (raw material)
    - BARANG_BANTU: bahan pembantu untuk produksi (auxiliary material)
    - JASA:         jasa (tidak ada stok fisik, stock_item=False)
    """
    BARANG_DAGANG = "BARANG_DAGANG"
    BARANG_JADI = "BARANG_JADI"
    BARANG_BAKU = "BARANG_BAKU"
    BARANG_BANTU = "BARANG_BANTU"
    JASA = "JASA"


class Barang(BaseModel, BaseMixin):
    """Master data Barang / Item.

    Sesuai Master Roadmap §9 Product:
    P0 minimum fields: kode, nama, item_type, base_uom, inventory_account,
        COGS_account, sales_account, inventory_method, stock_item flag
    """
    __tablename__ = "barang"
    __table_args__ = (
        Index("ix_barang_kode", "kode", unique=True, postgresql_where=text("status = 'AKTIF'")),
    )

    # === P0 minimum ===
    kode = Column(String(20), nullable=False)
    nama = Column(String(200), nullable=False)
    kategori_id = Column(UUID(as_uuid=True), ForeignKey("kategori_barang.id"), nullable=False)
    satuan_id = Column(UUID(as_uuid=True), ForeignKey("satuan.id"), nullable=False)  # base_uom
    metode_valuasi = Column(  # inventory_method
        SQLEnum(MetodeValuasi), default=MetodeValuasi.AVERAGE, nullable=False
    )
    status = Column(String(20), default="AKTIF", nullable=False)
    akun_persediaan_id = Column(  # inventory_account
        UUID(as_uuid=True), ForeignKey('akun_perkiraan.id', name='fk_barang_akun_persediaan'),
        nullable=True, index=True
    )

    # === NEW Phase 2 — P0 fields ===
    item_type = Column(  # item_type canonical enum
        SQLEnum(ItemTypeBarang), nullable=True
    )
    akun_hpp_id = Column(  # COGS_account
        UUID(as_uuid=True), ForeignKey('akun_perkiraan.id', name='fk_barang_akun_hpp'),
        nullable=True, index=True
    )
    akun_penjualan_id = Column(  # sales_account (revenue)
        UUID(as_uuid=True), ForeignKey('akun_perkiraan.id', name='fk_barang_akun_penjualan'),
        nullable=True, index=True
    )
    stock_item = Column(  # stock_item flag (True = stock-tracked, False = non-stock/jasa)
        Boolean, default=True, nullable=False
    )

    # === Operational fields ===
    harga_pokok = Column(Numeric(18, 2), default=0, nullable=False)
    stok = Column(Integer, default=0, nullable=False)
    stok_minimum = Column(Integer, default=0, nullable=False)

    # === LEGACY (akan di-drop di Phase berikutnya) ===
    jenis_barang = Column(String(20), nullable=True)  # legacy string, ganti dengan item_type

    # Relationships
    akun_persediaan = relationship('AkunPerkiraan', foreign_keys=[akun_persediaan_id])
    akun_hpp = relationship('AkunPerkiraan', foreign_keys=[akun_hpp_id])
    akun_penjualan = relationship('AkunPerkiraan', foreign_keys=[akun_penjualan_id])
    kategori = relationship("KategoriBarang", backref="barangs")
    satuan = relationship("Satuan")
