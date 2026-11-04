from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class SalesOrderTermin(BaseModel, BaseMixin):
    """Termin pembayaran Sales Order — Update #031.

    Field "Pembayaran Termin" di form SO (baris-baris nominal, mis. 3x
    pembayaran). Satu SO bisa dipanggil berkali-kali untuk pembayaran di form
    Penerimaan (jenis TERMIN_SO): satu penerimaan = satu termin.

    - nominal > 0; total seluruh termin == grand_total SO (divalidasi service).
    - penerimaan_kas_id terisi saat termin DIBAYAR (PenerimaanKas jenis
      TERMIN_SO; jurnal D Kas/Bank, K akun lawan — default Uang Muka
      Penjualan, bisa diganti per transaksi).
    - Pembatalan penerimaan (jurnal pembalik) melepas link otomatis → termin
      bisa dibayar ulang (audit tetap utuh lewat jurnal).
    """

    __tablename__ = "sales_order_termin"

    sales_order_id = Column(
        UUID(as_uuid=True), ForeignKey("sales_order.id", ondelete="CASCADE"), nullable=False, index=True
    )
    urutan = Column(Integer, nullable=False)
    nominal = Column(Numeric(18, 2), nullable=False)
    # Terisi saat termin dibayar (PenerimaanKas jenis TERMIN_SO). Dilepas
    # otomatis saat penerimaan dibatalkan/dihapus (cancel service / FK SET NULL).
    penerimaan_kas_id = Column(
        UUID(as_uuid=True), ForeignKey("penerimaan_kas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dibayar_pada = Column(DateTime(timezone=True), nullable=True)  # saat link dibuat

    sales_order = relationship("SalesOrder", back_populates="termin_pembayaran")
    penerimaan = relationship("PenerimaanKas")
