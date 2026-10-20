"""Allocations are draft children; only their posted, unreversed payment settles an invoice."""
from sqlalchemy import Column, Numeric, ForeignKey, CheckConstraint, UniqueConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PaymentAllocation(BaseModel, BaseMixin):
    __tablename__ = 'payment_allocation'
    __table_args__ = (
        CheckConstraint('nilai > 0', name='ck_allocation_positive'),
        CheckConstraint('(penerimaan_id IS NOT NULL AND sales_invoice_id IS NOT NULL AND pembayaran_id IS NULL AND purchase_invoice_id IS NULL) OR (pembayaran_id IS NOT NULL AND purchase_invoice_id IS NOT NULL AND penerimaan_id IS NULL AND sales_invoice_id IS NULL)', name='ck_allocation_pair'),
        UniqueConstraint('penerimaan_id', 'sales_invoice_id', name='uq_allocation_receipt_invoice'),
        UniqueConstraint('pembayaran_id', 'purchase_invoice_id', name='uq_allocation_payment_invoice'),
    )
    penerimaan_id = Column(UUID(as_uuid=True), ForeignKey('penerimaan_kas.id'), nullable=True, index=True)
    pembayaran_id = Column(UUID(as_uuid=True), ForeignKey('pembayaran_kas.id'), nullable=True, index=True)
    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey('sales_invoice.id'), nullable=True, index=True)
    purchase_invoice_id = Column(UUID(as_uuid=True), ForeignKey('purchase_invoice.id'), nullable=True, index=True)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey('akun_perkiraan.id'), nullable=False)
    nilai = Column(Numeric(18, 2), nullable=False)
    # === Update cetak "Bayar Pemasok" — diskon pelunasan per faktur ===
    # nilai = nominal hutang yang dilunasi (mengurangi sisa tagihan);
    # diskon = bagian dari nilai yang tidak dibayar tunai (potongan dari
    # supplier) sehingga kas keluar = nilai - diskon per faktur.
    diskon = Column(Numeric(18, 2), nullable=False, default=0, server_default='0')
    # === Update #01 — PPh 23 saat pelunasan (per faktur) ===
    # pph23 = potongan PPh 23 atas pembayaran faktur ini:
    # - piutang (penerimaan): pelanggan memotong PPh 23 dari setoran,
    #   kas masuk = nilai - pph23, selisihnya didebit ke akun PPh 23 Dibayar
    #   di Muka (setting PPH23_DIBAYAR_DIMUKA);
    # - hutang (pembayaran): perusahaan menahan PPh 23 dari pembayaran ke
    #   supplier, kas keluar = nilai - diskon - pph23, selisihnya dikredit
    #   ke akun Hutang PPh 23 (setting HUTANG_PPH23).
    # Sisa tagihan tetap berkurang sebesar ``nilai`` penuh.
    pph23 = Column(Numeric(18, 2), nullable=False, default=0, server_default='0')
    # Snapshot sisa tagihan faktur SEBELUM pembayaran ini (untuk kolom
    # "Terutang" di layout cetak Bayar Pemasok).
    terutang = Column(Numeric(18, 2), nullable=True)
    # === Update #03 — tautan sesi identifikasi (dokumen Belum Teridentifikasi
    # yang kemudian diidentifikasi ke pelanggan/supplier + invoice). Referensi
    # lunak (tanpa FK): baris dihapus saat sesi identifikasi dibatalkan agar
    # outstanding invoice pulih, sementara jurnal pembalik tetap utuh.
    kas_identifikasi_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    # === AUD-08 (update #04) — bukti potong & masa pajak per alokasi ===
    # no_bukti_potong = nomor bukti potong (BUPTI) yang diterbitkan pelanggan
    #   (piutang — kita dipotong) atau yang kita terbitkan (hutang — kita
    #   memotong) untuk potongan PPh 23 baris ini.
    # masa_pajak = masa pajak 'YYYY-MM' tempat bukti potong dilaporkan
    #   (SPT Masa PPh/PPN). Keduanya opsional (dokumen lama kosong).
    no_bukti_potong = Column(String(50), nullable=True)
    masa_pajak = Column(String(7), nullable=True)
    penerimaan = relationship('PenerimaanKas', back_populates='alokasi')
    pembayaran = relationship('PembayaranKas', back_populates='alokasi')
    sales_invoice = relationship('SalesInvoice')
    purchase_invoice = relationship('PurchaseInvoice')

    @property
    def invoice_id(self):
        return self.sales_invoice_id or self.purchase_invoice_id
