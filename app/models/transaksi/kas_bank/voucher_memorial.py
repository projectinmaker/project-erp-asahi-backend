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

=== Update #072 — Voucher Memorial HUTANG (Outstanding Memorial) ===

Fitur Outstanding Memorial (Pengaturan uraian hutang & piutang modul Kas &
Bank) menjadikan Gagal Tagih tersedia untuk HUTANG juga — permintaan user:
"penerimaan penuh, penerimaan sebagian, dan gagal tagih itu berlaku untuk
Hutang juga bukan hanya untuk Piutang".

- Kolom ``jenis``: 'PIUTANG' (default — semua data lama) | 'HUTANG'.
- PIUTANG: ``pelanggan_id`` + rincian ``sales_invoice_id`` — jurnal
  Dr Beban Penghapusan Piutang, Cr Piutang Usaha (kontrol).
- HUTANG : ``supplier_id``   + rincian ``purchase_invoice_id`` — jurnal
  Dr Hutang Usaha (kontrol per detail), Cr Pendapatan Penghapusan Hutang
  (kolom ``akun_beban_id`` dipakai ulang sebagai akun lawan sisi Kredit).
- ``settlement_service.balances`` memotong sisa hutang dengan penghapusan
  aktif jenis HUTANG (mirror ``total_gagal_tagih`` piutang).
"""
from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.kas_bank.pembayaran import StatusTransaksi


class VoucherMemorial(BaseModel, BaseMixin):
    __tablename__ = "voucher_memorial"

    no_bukti = Column(String(30), unique=True, nullable=False)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    # === Update #072 — jenis penghapusan. PIUTANG (default): pelanggan +
    # invoice penjualan; HUTANG: supplier + invoice pembelian. ===
    jenis = Column(String(10), nullable=False, default="PIUTANG", server_default="PIUTANG")
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=True)
    # === Update #072 — pihak untuk jenis HUTANG (mutually exclusive dengan
    # pelanggan_id; salah satu wajib terisi sesuai jenis). ===
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("supplier.id"), nullable=True)
    # === Update #044 — akun beban penghapusan piutang (Dr). Opsional saat create:
    # kosong → resolve dari Setting Akun KEY_BEBAN_PENGHAPUSAN_PIUTANG saat posting.
    # === Update #072 — untuk jenis HUTANG kolom ini dipakai sebagai AKUN
    # LAWAN sisi Kredit (default Setting Akun PENDAPATAN_PENGHAPUSAN_HUTANG). ===
    akun_beban_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)
    catatan = Column(Text, nullable=True)
    total_nilai = Column(Numeric(18, 2), default=0, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusTransaksi), default=StatusTransaksi.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    pelanggan = relationship("Pelanggan")
    # === Update #072 — relasi supplier (jenis HUTANG). ===
    supplier = relationship("Supplier")
    akun_beban = relationship("AkunPerkiraan", foreign_keys=[akun_beban_id])
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    rincian = relationship(
        "VoucherMemorialDetail", back_populates="voucher", cascade="all, delete-orphan"
    )


class VoucherMemorialDetail(BaseModel, BaseMixin):
    __tablename__ = "voucher_memorial_detail"

    voucher_id = Column(UUID(as_uuid=True), ForeignKey("voucher_memorial.id"), nullable=False, index=True)
    # === Update #072 — nullable: jenis PIUTANG memakai sales_invoice_id,
    # jenis HUTANG memakai purchase_invoice_id (salah satu wajib terisi). ===
    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey("sales_invoice.id"), nullable=True)
    purchase_invoice_id = Column(UUID(as_uuid=True), ForeignKey("purchase_invoice.id"), nullable=True)
    # Akun piutang/hutang kontrol invoice — dibekukan saat dokumen dibuat
    # (paritas PaymentAllocation.akun_perkiraan_id / control_account()).
    akun_piutang_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    nilai = Column(Numeric(18, 2), nullable=False)
    # Snapshot sisa tagihan SEBELUM penghapusan ini (kolom "Sisa Sebelum"
    # pada layout cetak Voucher Memorial).
    terutang = Column(Numeric(18, 2), nullable=True)

    voucher = relationship("VoucherMemorial", back_populates="rincian")
    sales_invoice = relationship("SalesInvoice")
    # === Update #072 — relasi invoice pembelian (jenis HUTANG). ===
    purchase_invoice = relationship("PurchaseInvoice")
    akun_piutang = relationship("AkunPerkiraan", foreign_keys=[akun_piutang_id])

    @property
    def invoice_id(self):
        """=== Update #072 — id invoice generik (piutang ATAU hutang) untuk

        response schema VoucherMemorialRincianResponse (from_attributes):
        sebelumnya schema menuntut atribut ``invoice_id`` yang tidak pernah
        ada di model — GET /kas-bank/voucher-memorial 500 begitu daftar
        berisi ≥1 dokumen (dengan daftar kosong error tidak terlihat). ===
        """
        return self.sales_invoice_id or self.purchase_invoice_id
