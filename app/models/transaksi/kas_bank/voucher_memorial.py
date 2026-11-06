"""=== Update #044 — Voucher Memorial (Penghapusan Piutang / Gagal Tagih) ===

Alur Penerimaan Piutang modul Kas & Bank (revisi pemilik, poin 5):

    5. Penerimaan Piutang
       ├─ Penerimaan Penuh     ──→ Voucher Penerimaan Bank / Kas
       ├─ Penerimaan Sebagian  ──→ Voucher Penerimaan Bank / Kas
       └─ Gagal Tagih          ──→ Voucher Memorial (Adjustment)

Cabang "Gagal Tagih" TIDAK menyetor kas — sisa tagihan invoice dihapusbukukan
(penghapusan piutang) lewat dokumen Voucher Memorial:

- Header  ``voucher_memorial``        : pelanggan, akun beban (Dr), total.
- Detail  ``voucher_memorial_detail`` : per invoice — akun piutang kontrol
  (K, dibekukan dari jurnal invoice — pola ``settlement_service.control_account``)
  + snapshot ``terutang`` (sisa sebelum penghapusan, paritas kolom Terutang
  pada layout cetak Bayar Pemasok).

Jurnal sistem saat posting (``is_manual=False`` — akun kontrol piutang memang
tidak boleh tersentuh jurnal manual):

    Dr  Beban Penghapusan Piutang   (akun_beban_id)
    Cr  Piutang Usaha (kontrol)     (per detail, dikelompokkan per akun)

Sisa tagihan invoice dihitung ulang di ``settlement_service.balances`` dengan
mengurangkan total penghapusan aktif (kolom ``total_gagal_tagih``), sehingga
invoice yang dihapusbukukan penuh berstatus LUNAS.
"""
from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.kas_bank.pembayaran import StatusTransaksi


class VoucherMemorial(BaseModel, BaseMixin):
    __tablename__ = "voucher_memorial"

    no_bukti = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    # === Update #044 — akun beban penghapusan piutang (Dr). Opsional saat create:
    # kosong → resolve dari Setting Akun KEY_BEBAN_PENGHAPUSAN_PIUTANG saat posting. ===
    akun_beban_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)
    catatan = Column(Text, nullable=True)
    total_nilai = Column(Numeric(18, 2), default=0, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusTransaksi), default=StatusTransaksi.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    pelanggan = relationship("Pelanggan")
    akun_beban = relationship("AkunPerkiraan", foreign_keys=[akun_beban_id])
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    rincian = relationship(
        "VoucherMemorialDetail", back_populates="voucher", cascade="all, delete-orphan"
    )


class VoucherMemorialDetail(BaseModel, BaseMixin):
    __tablename__ = "voucher_memorial_detail"

    voucher_id = Column(UUID(as_uuid=True), ForeignKey("voucher_memorial.id"), nullable=False, index=True)
    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey("sales_invoice.id"), nullable=False)
    # Akun piutang kontrol invoice — dibekukan saat dokumen dibuat (paritas
    # PaymentAllocation.akun_perkiraan_id / control_account()).
    akun_piutang_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    nilai = Column(Numeric(18, 2), nullable=False)
    # Snapshot sisa tagihan SEBELUM penghapusan ini (kolom "Sisa Sebelum"
    # pada layout cetak Voucher Memorial).
    terutang = Column(Numeric(18, 2), nullable=True)

    voucher = relationship("VoucherMemorial", back_populates="rincian")
    sales_invoice = relationship("SalesInvoice")
    akun_piutang = relationship("AkunPerkiraan", foreign_keys=[akun_piutang_id])
