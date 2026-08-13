from sqlalchemy import Column, String, Text, Numeric

# FK sementara di-comment karena tabel transaksi belum dibuat (Phase 4)
# from sqlalchemy.dialects.postgresql import UUID
# from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class BiayaTambahan(BaseModel, BaseMixin):
    __tablename__ = "biaya_tambahan"

    nama = Column(String(200), nullable=False)
    jenis = Column(String(50), nullable=False)  # Ongkos kirim, Asuransi, dll
    nilai = Column(Numeric(18, 2), default=0, nullable=False)
    keterangan = Column(Text, nullable=True)

    # ==========================================
    # 4 FK TERPISAH (Akan diaktifkan di Phase 4)
    # ==========================================
    # sales_order_id = Column(UUID(as_uuid=True), ForeignKey("sales_order.id"), nullable=True)
    # purchase_order_id = Column(UUID(as_uuid=True), ForeignKey("purchase_order.id"), nullable=True)
    # sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey("sales_invoice.id"), nullable=True)
    # purchase_invoice_id = Column(UUID(as_uuid=True), ForeignKey("purchase_invoice.id"), nullable=True)
