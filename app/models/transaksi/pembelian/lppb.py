from sqlalchemy import Column, String, Text, ForeignKey, Enum as SQLEnum, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.penjualan.sales_order import StatusPenjualan


class Lppb(BaseModel, BaseMixin):
    """LPPB — Laporan Pemeriksaan dan Pengujian Barang (update ASAHI #005).

    Dokumen RECORD QMS untuk inspeksi barang masuk dari supplier.
    Ter-link ke Purchase Order (tarik item + hitung sisa); kolom-kolom
    snapshot (nomor_do, nomor_ri, proyek, dst.) disimpan sebagai teks agar
    cetakan tidak berubah ketika master diedit.

    TIDAK posting stok / jurnal — Penerimaan Barang tetap menjadi pencatat
    stok gudang; LPPB murni catatan hasil pemeriksaan & pengujian (QC).
    """

    __tablename__ = "lppb"

    no_form = Column(String(40), unique=True, nullable=False, index=True)  # "LPPB ASI/YYYY/NNN"
    tanggal = Column(DateTime(timezone=True), nullable=False)  # Tanggal LPPB
    tanggal_terima = Column(DateTime(timezone=True), nullable=True)  # Tgl Terima
    purchase_order_id = Column(UUID(as_uuid=True), ForeignKey("purchase_order.id"), nullable=False, index=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("supplier.id"), nullable=False, index=True)

    # Referensi eksternal (teks manual — tidak ada entity-nya di sistem)
    nomor_do = Column(String(100), nullable=True)  # surat jalan / DO supplier
    nomor_ri = Column(String(100), nullable=True)  # nomor referensi inspeksi eksternal
    proyek = Column(String(200), nullable=True)

    # Kontrol dokumen QMS (kop kanan + footer cetakan)
    revisi = Column(String(20), nullable=True)
    no_format = Column(String(50), nullable=True)

    keterangan = Column(Text, nullable=True)  # baris keterangan di bawah tabel

    # Blok pengesahan "Mengetahui"
    mengetahui_jabatan = Column(String(100), nullable=True)
    mengetahui_nama = Column(String(150), nullable=True)

    status = Column(SQLEnum(StatusPenjualan), default=StatusPenjualan.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    purchase_order = relationship("PurchaseOrder")
    supplier = relationship("Supplier")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship("LppbDetail", back_populates="lppb", cascade="all, delete-orphan",
                           order_by="LppbDetail.urutan")
