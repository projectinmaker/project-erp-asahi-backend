from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class Supplier(BaseModel, BaseMixin):
    """Master data Supplier.

    Sesuai Catatan Update Supplier Master ASAHI:
    P0 minimum fields: supplier_code (kode), supplier_name (nama), status, AP mapping (akun_hutang_id)
    Optional fields (nullable): NPWP, NITKU, address, city, province, country, PIC, phone, email,
        payment term, credit limit, tax status, supplier_type, bank info, currency

    Golden Rules:
    - supplier_code unique, immutable setelah dipakai transaksi
    - Jangan simpan saldo hutang sebagai master field (derive from transaksi)
    - Historical transaction tidak berubah ketika Supplier Master diperbarui
    - Purchase account mengikuti item/transaction mapping, bukan dipaksa dari supplier
    - AP Subledger harus dapat direkonsiliasi dengan GL 211000 Hutang Usaha
    """
    __tablename__ = "supplier"
    __table_args__ = (
        Index("ix_supplier_kode", "kode", unique=True, postgresql_where=text("status = 'AKTIF'")),
    )

    # === P0 minimum ===
    kode = Column(String(20), nullable=False)  # supplier_code
    nama = Column(String(200), nullable=False)  # supplier_name
    status = Column(String(20), default="AKTIF", nullable=False)  # ACTIVE / INACTIVE
    akun_hutang_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)  # AP Control mapping

    # === Optional fields (all nullable) ===
    # Legal/Tax
    npwp = Column(String(50), nullable=True)
    nitku = Column(String(50), nullable=True)
    tax_status = Column(String(20), nullable=True)  # PKP / NON_PKP / NULL — jangan ditebak
    supplier_type = Column(String(20), nullable=True)  # COMPANY / INDIVIDUAL

    # Address & Contact
    alamat = Column(Text, nullable=True)  # address
    city = Column(String(100), nullable=True)
    province = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)  # penting untuk supplier luar negeri
    postal_code = Column(String(10), nullable=True)
    telepon = Column(String(30), nullable=True)  # phone
    email = Column(String(100), nullable=True)
    kontak_person = Column(String(150), nullable=True)  # pic_name

    # Commercial
    syarat_bayar_default = Column(String(50), nullable=True)  # LEGACY string
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id"), nullable=True)
    credit_limit = Column(Numeric(18, 2), nullable=True)
    currency = Column(String(3), nullable=True, default='IDR')  # editable, bulk-updateable, transaction-snapshot

    # Bank Information (sensitive data — audit trail recommended)
    bank_name = Column(String(100), nullable=True)
    bank_account_no = Column(String(50), nullable=True)
    bank_account_name = Column(String(200), nullable=True)

    # Migration audit
    supplier_name_raw = Column(String(200), nullable=True)  # original name before normalization

    # Relationships
    akun_hutang = relationship("AkunPerkiraan", foreign_keys=[akun_hutang_id])
    syarat_bayar = relationship("SyaratBayar", foreign_keys=[syarat_bayar_id])

    @property
    def effective_syarat_bayar_id(self):
        """Resolve effective payment term ID — prefer FK baru kalau diisi."""
        return self.syarat_bayar_id
