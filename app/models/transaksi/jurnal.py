import enum
from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class RefModule(str, enum.Enum):
    """
    Sumber modul transaksi yang menghasilkan jurnal.

    === LEGACY VALUES (Indonesian names — backward compatibility) ===
    Nilai-nilai ini tetap dipertahankan karena data historis di database
    masih memakainya. Service code yang baru WAJIB memakai nilai canonical
    (English names) di bawah ini:
    """
    # === LEGACY (jangan dipakai untuk transaksi baru) ===
    PEMBAYARAN = "PEMBAYARAN"           # legacy generic payment; lihat AP_SETTLEMENT utk pelunasan AP
    PENERIMAAN = "PENERIMAAN"           # legacy generic receipt; lihat AR_SETTLEMENT utk pelunasan AR
    TRANSFER_BANK = "TRANSFER_BANK"     # legacy; lihat BANK_TRANSFER
    SALES_ORDER = "SALES_ORDER"         # SO tidak membuat jurnal — hanya untuk traceability
    SALES_INVOICE = "SALES_INVOICE"     # tetap dipakai untuk invoice (AR/Revenue/VAT)
    SALES_RETUR = "SALES_RETUR"         # tetap dipakai untuk sales return / credit note
    PURCHASE_ORDER = "PURCHASE_ORDER"   # PO tidak membuat jurnal — hanya untuk traceability
    PURCHASE_INVOICE = "PURCHASE_INVOICE"  # tetap dipakai untuk invoice (AP/VAT/GRNI clearing)
    PURCHASE_RETUR = "PURCHASE_RETUR"   # tetap dipakai untuk purchase return / debit note
    PENYESUAIAN_STOK = "PENYESUAIAN_STOK"  # legacy; lihat INVENTORY_ADJUSTMENT
    PENYUSUTAN = "PENYUSUTAN"           # legacy; lihat ASSET_DEPRECIATION
    SALDO_AWAL = "SALDO_AWAL"           # saldo awal
    PENUTUPAN_PERIODE = "PENUTUPAN_PERIODE"
    REKONSILIASI_BANK = "REKONSILIASI_BANK"  # legacy; lihat BANK_RECONCILIATION
    MANUAL = "MANUAL"

    # === CANONICAL (target architecture per Master Roadmap §8 — RefModule) ===
    # Nilai-nilai baru sesuai target ASAHI Accounting System. Service code yang
    # baru WAJIB memakai nilai-nilai ini untuk transaksi baru.
    SALES_DELIVERY = "SALES_DELIVERY"           # PengirimanBarang (delivery / stock-out + COGS)
    PURCHASE_RECEIPT = "PURCHASE_RECEIPT"       # PenerimaanBarang (goods receipt / stock-in + GRNI)
    AR_SETTLEMENT = "AR_SETTLEMENT"            # Pelunasan piutang via PenerimaanKas w/ allocation
    AP_SETTLEMENT = "AP_SETTLEMENT"            # Pelunasan hutang via PembayaranKas w/ allocation
    INVENTORY_ADJUSTMENT = "INVENTORY_ADJUSTMENT"  # Penyesuaian stok (opname / adjustment)
    INVENTORY_TRANSFER = "INVENTORY_TRANSFER"   # Pemindahan barang antar gudang
    ASSET_CAPITALIZATION = "ASSET_CAPITALIZATION"  # Kapitalisasi aset tetap
    ASSET_DEPRECIATION = "ASSET_DEPRECIATION"   # Jurnal penyusutan aset tetap (bulanan)
    ASSET_DISPOSAL = "ASSET_DISPOSAL"           # Penghentian/penjualan aset tetap
    BANK_TRANSFER = "BANK_TRANSFER"             # Transfer antar kas/bank
    BANK_RECONCILIATION = "BANK_RECONCILIATION"  # Rekonsiliasi bank


class StatusJurnal(str, enum.Enum):
    POSTED = "POSTED"
    DRAFT = "DRAFT"


class JurnalUmum(BaseModel, BaseMixin):
    __tablename__ = "jurnal_umum"
    __table_args__ = (UniqueConstraint("reversal_of_id", name="uq_jurnal_reversal"),)

    company_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'), nullable=True, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'), nullable=True, index=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'), nullable=True, index=True)
    cost_center_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'), nullable=True, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey('organization_unit.id'), nullable=True, index=True)
    no_jurnal = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)

    tipe_transaksi = Column(String(100), nullable=True)
    ref_module = Column(SQLEnum(RefModule), nullable=True)
    ref_no = Column(String(30), nullable=True, index=True)
    ref_id = Column(UUID(as_uuid=True), nullable=True)
    reversal_of_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id", name="fk_jurnal_reversal"), nullable=True)

    total_debit = Column(Numeric(18, 2), default=0, nullable=False)
    total_kredit = Column(Numeric(18, 2), default=0, nullable=False)

    keterangan = Column(Text, nullable=True)
    status = Column(SQLEnum(StatusJurnal), default=StatusJurnal.DRAFT, nullable=False)

    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    details = relationship("JurnalDetail", back_populates="jurnal", cascade="all, delete-orphan")
    creator = relationship("Pengguna", foreign_keys=[created_by])
