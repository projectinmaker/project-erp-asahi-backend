import enum
from sqlalchemy import text, Date, Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class StatusTransaksi(str, enum.Enum):
    DRAFT = "DRAFT"
    SELESAI = "SELESAI"
    BATAL = "BATAL"


# === Update #02 — Jenis Transaksi Kas & Bank (Penerimaan/Pembayaran) ===
# Dua jenis transaksi dipilih user di form Penerimaan/Pembayaran:
# 1. PELUNASAN_INVOICE    — pelunasan piutang (penerimaan, pilih pelanggan →
#                            invoice penjualan outstanding) atau pelunasan
#                            hutang (pembayaran, pilih supplier → invoice
#                            pembelian outstanding). Dicatat lewat alur
#                            settlement (/pelunasan/{piutang|hutang}) dengan
#                            alokasi per invoice; jurnal Dr/Kr akun
#                            piutang/hutang subledger pihak + Kas/Bank.
# 2. TRANSAKSI_LAINNYA    — penerimaan modal, pendapatan lain, biaya
#                            operasional, dan transaksi non-invoice lain;
#                            user memilih akun lawan dari COA per baris
#                            rincian. TIDAK BOLEH memakai akun piutang/hutang
#                            (guard di service) tanpa alokasi invoice.
# (Update #14 — jenis BELUM_TERIDENTIFIKASI dihapus bersama seluruh fitur
#  Identifikasi & Alokasi; dokumen lama dikonversi migration
#  cp0000000021 ke TRANSAKSI_LAINNYA.)
# Disimpan sebagai String (bukan pg enum) supaya migration ringan dan aman
# untuk upgrade database berjalan.
JENIS_TRANSAKSI_PELUNASAN_INVOICE = "PELUNASAN_INVOICE"
JENIS_TRANSAKSI_LAINNYA = "TRANSAKSI_LAINNYA"
JENIS_TRANSAKSI_KAS = (
    JENIS_TRANSAKSI_PELUNASAN_INVOICE,
    JENIS_TRANSAKSI_LAINNYA,
)


class PembayaranKas(BaseModel, BaseMixin):
    __tablename__ = "pembayaran_kas"

    # === Update AUD-12 — dimensi perusahaan (default ASAHI via migration cp14;
    # kolom nullable + server_default agar baris lama/single-company aman) ===
    company_id = Column(UUID(as_uuid=True), ForeignKey("organization_unit.id"),
                        nullable=True,
                        server_default=text("'0e9f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b'"))

    no_bukti = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    kas_bank_id = Column(UUID(as_uuid=True), ForeignKey("kas_bank_akun.id"), nullable=False)
    no_nukti = Column(String(50), nullable=False)
    no_cek = Column(String(50), nullable=True)
    # === Update cetak "Bayar Pemasok" — info cek & mata uang untuk layout print ===
    tanggal_cek = Column(Date, nullable=True)
    jumlah_cek = Column(Numeric(18, 2), nullable=True)
    mata_uang = Column(String(8), nullable=False, default="IDR", server_default="IDR")
    nilai_tukar = Column(Numeric(18, 4), nullable=False, default=1, server_default="1")
    penerima = Column(String(255), nullable=True)
    catatan = Column(Text, nullable=True)
    total_nilai = Column(Numeric(18, 2), default=0, nullable=False)
    # Update #5 — penalti pelunasan hutang (menambah total kas dibayar)
    penalti = Column(Numeric(18, 2), default=0, nullable=False)
    akun_penalti_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)
    auto_post_jurnal = Column(Boolean, default=False, nullable=False)  # Phase F: deprecated
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusTransaksi), default=StatusTransaksi.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    supplier_id = Column(UUID(as_uuid=True), ForeignKey("supplier.id"), nullable=True)
    supplier = relationship("Supplier")
    alokasi = relationship("PaymentAllocation", back_populates="pembayaran", cascade="all, delete-orphan")
    # === Update #02 — jenis transaksi kas (lihat konstanta JENIS_TRANSAKSI_*) ===
    jenis_transaksi = Column(
        String(30), nullable=False, default=JENIS_TRANSAKSI_LAINNYA,
        server_default=JENIS_TRANSAKSI_LAINNYA,
    )

    # Relationships
    kas_bank = relationship("KasBankAkun")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    akun_penalti = relationship("AkunPerkiraan", foreign_keys=[akun_penalti_id])
    rincian = relationship("PembayaranRincian", back_populates="pembayaran", cascade="all, delete-orphan")
