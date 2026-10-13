from sqlalchemy import Column, String, Text, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class LppbDetail(BaseModel, BaseMixin):
    """Satu baris pemeriksaan/pengujian pada LPPB (update ASAHI #005).

    Dua jenis baris:
    - Baris barang: ditarik dari PO (purchase_order_detail_id + barang_id
      terisi, snapshot kode/deskripsi/satuan/qty_po dari PO saat dibuat).
    - Baris jasa pengujian (mis. NDT): manual — barang_id NULL, dibuat
      berpasangan TEPAT DI BAWAH barang yang diujinya (lewat urutan).

    Kolom kuantitas:
    - qty_penerimaan   = jumlah diterima pada penerimaan ini
    - qty_baik / qty_tidak_baik = Hasil Pengujian (dua sub-kolom Baik/Tidak Baik)
    - qty_sudah_diterima = akumulasi penerimaan TERMASUK kali ini
      (prev_diterima + qty_penerimaan, snapshot saat LPPB dibuat)
    - qty_belum_diterima = sisa PO = qty_po − qty_sudah_diterima (min 0)
    """

    __tablename__ = "lppb_detail"

    lppb_id = Column(UUID(as_uuid=True), ForeignKey("lppb.id"), nullable=False, index=True)
    urutan = Column(Integer, nullable=False)

    barang_id = Column(UUID(as_uuid=True), ForeignKey("barang.id"), nullable=True, index=True)
    purchase_order_detail_id = Column(UUID(as_uuid=True), ForeignKey("purchase_order_detail.id"), nullable=True, index=True)

    # Snapshot (baris barang: dari PO; baris jasa: ketikan user)
    kode_material = Column(String(100), nullable=True)
    deskripsi_spesifikasi = Column(Text, nullable=False)
    satuan = Column(String(50), nullable=True)

    qty_po = Column(Integer, default=0, nullable=False, server_default="0")
    qty_penerimaan = Column(Integer, default=0, nullable=False, server_default="0")
    qty_baik = Column(Integer, default=0, nullable=False, server_default="0")
    qty_tidak_baik = Column(Integer, default=0, nullable=False, server_default="0")
    qty_sudah_diterima = Column(Integer, default=0, nullable=False, server_default="0")
    qty_belum_diterima = Column(Integer, default=0, nullable=False, server_default="0")

    # Relationships
    lppb = relationship("Lppb", back_populates="details")
    barang = relationship("Barang")
    purchase_order_detail = relationship("PurchaseOrderDetail")
