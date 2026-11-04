from decimal import Decimal

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.penjualan.sales_order import StatusPenjualan


class Penawaran(BaseModel, BaseMixin):
    """Penawaran (quotation) — dokumen pra-order penjualan.

    Blueprint = SalesOrder (header + detail + TransaksiBiaya), TANPA jurnal
    dan TANPA workflow (tidak pernah diposting).

    Siklus status (Update #032 — revisi + repeat order):
    - DRAFT     : masih disusun, bisa diedit langsung (tanpa dokumen baru).
    - SELESAI   : sudah DIPAKAI membuat SO (minimal 1×). TETAP bisa dipilih
                  lagi untuk membuat SO berikutnya (repeat order) — penawaran
                  tidak "hangus"; jumlah pemakaian terlihat di daftar
                  (digunakan_count). Tidak bisa diedit langsung lagi — perubahan
                  isi harus lewat tombol Revisi agar SO-SO lama tidak berubah
                  diam-diam.
    - DIREVISI  : sudah digantikan dokumen revisi lebih baru (nomor sama,
                  revisi_ke lebih tinggi). Terkunci: tidak bisa dipakai buat SO,
                  tidak bisa diedit. Jejak harga lama tetap utuh untuk audit.
    - DIBATALKAN: dibatalkan/dihapus permanen (hard delete modul Histori).

    Revisi (Update #032): penawaran SELESAI direvisi → dibuatkan dokumen BARU
    berstatus DRAFT dengan no_penawaran SAMA + revisi_ke+1 (tampil "Rev. N"
    di daftar & cetakan); dokumen asal berubah jadi DIREVISI. Revisi dari
    revisi (Rev 2 SELESAI → Rev 3) berlanjut pada rantai yang sama.

    Total (sub_total/total_diskon/total_ppn/total_biaya_tambahan/grand_total)
    dihitung dengan pola yang sama dengan SalesOrder:
    lihat app.services.document_totals.refresh_totals (order_docs).
    `diskon_global` bersifat PERSEN (mirror SalesOrder.diskon_global).
    """

    __tablename__ = "penawaran"

    # Update #032: unique per (no_penawaran, revisi_ke) — bukan lagi unik
    # sendiri (dokumen revisi memakai nomor sama); ditegakkan lewat index
    # unik komposit (lihat migrasi cp0000000026). Dilebarkan 30 → 60 agar
    # aman untuk singkatan panjang.
    no_penawaran = Column(String(60), nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    berlaku_hingga = Column(Date, nullable=True)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False)
    syarat_bayar_id = Column(UUID(as_uuid=True), ForeignKey("syarat_bayar.id"), nullable=True)
    alamat_pengiriman = Column(String(255), nullable=True)
    keterangan = Column(Text, nullable=True)
    # === Update ASAHI #003 — dibawa ke SO saat konversi (SO langsung lengkap) ===
    # No. PO customer (referensi PO dari pelanggan, mis. diterima bersama
    # konfirmasi order) → SalesOrder.customer_po_number.
    customer_po_number = Column(String(50), nullable=True)
    # Singkatan nama perusahaan customer (snapshot; mirror SO.customer_code)
    # → segmen nomor SO hasil konversi: "SO ASI/<singkatan>/YYYY/NNN".
    customer_code = Column(String(20), nullable=True)
    mata_uang = Column(String(10), nullable=False, default="IDR")
    diskon_global = Column(Numeric(18, 2), default=0, nullable=False)
    ppn = Column(Numeric(5, 2), default=11, nullable=False)
    sub_total = Column(Numeric(18, 2), default=0, nullable=False)
    total_diskon = Column(Numeric(18, 2), default=0, nullable=False)
    total_ppn = Column(Numeric(18, 2), default=0, nullable=False)
    total_biaya_tambahan = Column(Numeric(18, 2), default=0, nullable=False)
    grand_total = Column(Numeric(18, 2), default=0, nullable=False)
    status = Column(String(20), nullable=False, default="DRAFT")
    # === Update #032 — penanda revisi dokumen: 1 = dokumen asal; 2 = Rev. 2;
    # dst. Nomor penawaran TIDAK berubah antar revisi (tampil "Rev. N"). ===
    revisi_ke = Column(Integer, nullable=False, default=1, server_default="1")
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=True)

    # Relationships (mirror SalesOrder)
    pelanggan = relationship("Pelanggan")
    syarat_bayar = relationship("SyaratBayar")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    details = relationship(
        "PenawaranDetail", back_populates="penawaran", cascade="all, delete-orphan"
    )
    biaya_tambahan = relationship(
        "TransaksiBiaya", back_populates="penawaran", cascade="all, delete-orphan"
    )
    # Update #032 — SO-SO yang dibuat dari penawaran ini (repeat order:
    # satu penawaran boleh dipakai beberapa SO; link dipakai untuk counter
    # "dipakai N× SO" dan menjaga hard-delete penawaran yang sudah terpakai).
    sales_orders = relationship("SalesOrder", back_populates="penawaran")

    # === Update #032 — ringkasan untuk response/daftar ===
    @property
    def digunakan_count(self) -> int:
        """Berapa kali penawaran ini dipakai membuat SO (repeat order)."""
        return len(self.sales_orders or [])

    # === Update #039 — SISA PENAWARAN (alur DP bertahap: satu penawaran,
    # banyak SO per tahap pembayaran; acuan tagihan tetap invoice). ===
    # Sisa dihitung ON-THE-FLY dari link SO (tanpa kolom baru yang bisa basi):
    #   sisa = grand_total penawaran − Σ grand_total SO AKTIF turunannya.
    # SO DIBATALKAN tidak dihitung → nilainya otomatis kembali ke sisa begitu
    # SO dibatalkan. Sisa bisa NEGATIF bila SO-SO turunan melebihi nilai
    # penawaran (repeat order nambah scope) — UI menampilkan warning, bukan
    # blok (keputusan user #039). ===
    @property
    def dipakai_so_total(self) -> Decimal:
        """Σ grand_total SO AKTIF (non-DIBATALKAN) dari penawaran ini."""
        return sum(
            (
                (so.grand_total or Decimal("0"))
                for so in (self.sales_orders or [])
                if so.status != StatusPenjualan.DIBATALKAN
            ),
            Decimal("0"),
        )

    @property
    def sisa_penawaran(self) -> Decimal:
        """Grand Total − dipakai_so_total (bisa negatif bila SO melebihi)."""
        return (self.grand_total or Decimal("0")) - self.dipakai_so_total

    @property
    def so_list(self) -> list:
        """SO turunan AKTIF (non-DIBATALKAN) urut tanggal — kartu sisa di UI."""
        aktif = [
            so
            for so in (self.sales_orders or [])
            if so.status != StatusPenjualan.DIBATALKAN
        ]
        return sorted(aktif, key=lambda so: (so.tanggal, so.created_at or so.tanggal))
