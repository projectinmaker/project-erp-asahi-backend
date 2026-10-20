import enum
from sqlalchemy import text, Column, String, Text, Integer, Numeric, Date, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.persediaan.permintaan_barang import StatusPersediaan


class TipePenyesuaian(str, enum.Enum):
    TAMBAH = "TAMBAH"
    KURANG = "KURANG"


class PenyesuaianStok(BaseModel, BaseMixin):
    __tablename__ = "penyesuaian_stok"

    # === Update AUD-12 — dimensi perusahaan (default ASAHI via migration cp14;
    # kolom nullable + server_default agar baris lama/single-company aman) ===
    company_id = Column(UUID(as_uuid=True), ForeignKey("organization_unit.id"),
                        nullable=True,
                        server_default=text("'0e9f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b'"))

    no_adj = Column(String(30), unique=True, nullable=False, index=True)
    gudang_id = Column(UUID(as_uuid=True), ForeignKey("gudang.id"), nullable=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=False)
    tipe = Column(SQLEnum(TipePenyesuaian), nullable=False)
    qty = Column(Integer, default=0, nullable=False)
    biaya_satuan = Column(Numeric(18, 2), default=0, nullable=False)
    total = Column(Numeric(18, 2), default=0, nullable=False)
    alasan = Column(Text, nullable=True)
    auto_post_jurnal = Column(Boolean, default=True, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusPersediaan), default=StatusPersediaan.DIAJUKAN, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # === NEW Phase A — FEFO adjustment expiry (Catatan Audit Inventory §3 item 5) ===
    # Required for FEFO method when tipe=TAMBAH. Kalau barang metode_valuasi=FEFO
    # dan tipe=TAMBAH, tanggal_kedaluwarsa wajib diisi (di-enforce di service layer).
    # Untuk AVERAGE/FIFO, field ini optional (diabaikan).
    tanggal_kedaluwarsa = Column(Date, nullable=True)

    # Relationships
    barang = relationship("Barang")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
