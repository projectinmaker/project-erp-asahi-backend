from sqlalchemy import Column, String, Text, Numeric, Boolean
from app.database import BaseModel
from app.models.base import BaseMixin


class BiayaTambahan(BaseModel, BaseMixin):
    __tablename__ = "biaya_tambahan"

    kode = Column(String(20), unique=True, nullable=False, index=True)
    nama = Column(String(200), nullable=False)
    jenis = Column(String(50), nullable=False)  # ONGKOS_KIRIM, ASURANSI, PAJAK, LAINNYA
    persen = Column(Numeric(5, 2), default=0, nullable=False)  # Persen dari subtotal (opsional)
    nilai_tetap = Column(Numeric(18, 2), default=0, nullable=False)  # Nilai tetap (opsional)
    keterangan = Column(Text, nullable=True)
    is_aktif = Column(Boolean, default=True, nullable=False)
