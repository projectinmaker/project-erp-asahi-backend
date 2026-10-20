"""Update #02 — Identifikasi & Alokasi transaksi kas Belum Teridentifikasi.

Dokumen kas (penerimaan/pembayaran) dengan jenis_transaksi =
BELUM_TERIDENTIFIKASI diposting ke akun penampung sementara (setting
KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI). Begitu asal mutasi diketahui, user
menjalankan Identifikasi & Alokasi: nilai dipindahkan dari akun penampung
ke akun yang benar (bisa beberapa baris alokasi sekaligus / bertahap)
melalui SATU jurnal reklasifikasi yang HANYA menyentuh akun penampung dan
akun tujuan — sisi Kas/Bank tidak dijurnal ulang (tidak ada jurnal bank
ganda):

    Penerimaan:  Dr Penampung  |  Cr Akun tujuan (per alokasi)
    Pembayaran:   Dr Akun tujuan (per alokasi)  |  Cr Penampung

Jurnal dibuat lewat posting engine yang sama (auto_posting_jurnal —
validasi debit-kredit balance, akun DETAIL/aktif, periode terbuka) dan
terhubung ke dokumen ini via ref_id; pembatalan identifikasi memakai
reverse_journal (jurnal pembalik berantai reversal_of_id — audit trail
utuh). Nomor dokumen: "IDN ASI/YYYY/NNN".
"""
from sqlalchemy import Column, String, Text, Numeric, ForeignKey, Enum as SQLEnum, DateTime, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin
from app.models.transaksi.kas_bank.pembayaran import StatusTransaksi


class KasIdentifikasi(BaseModel, BaseMixin):
    __tablename__ = "kas_identifikasi"
    __table_args__ = (
        CheckConstraint(
            "(penerimaan_id IS NOT NULL AND pembayaran_id IS NULL) "
            "OR (penerimaan_id IS NULL AND pembayaran_id IS NOT NULL)",
            name="ck_identifikasi_doc_pair",
        ),
        CheckConstraint("total_nilai > 0", name="ck_identifikasi_positive"),
    )

    no_bukti = Column(String(30), unique=True, nullable=False, index=True)
    tanggal = Column(DateTime(timezone=True), nullable=False)
    penerimaan_id = Column(UUID(as_uuid=True), ForeignKey("penerimaan_kas.id"), nullable=True, index=True)
    pembayaran_id = Column(UUID(as_uuid=True), ForeignKey("pembayaran_kas.id"), nullable=True, index=True)
    catatan = Column(Text, nullable=True)
    total_nilai = Column(Numeric(18, 2), default=0, nullable=False)
    jurnal_umum_id = Column(UUID(as_uuid=True), ForeignKey("jurnal_umum.id"), nullable=True)
    status = Column(SQLEnum(StatusTransaksi), default=StatusTransaksi.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    penerimaan = relationship("PenerimaanKas")
    pembayaran = relationship("PembayaranKas")
    jurnal = relationship("JurnalUmum")
    creator = relationship("Pengguna", foreign_keys=[created_by])
    rincian = relationship(
        "KasIdentifikasiRincian", back_populates="identifikasi",
        cascade="all, delete-orphan", order_by="KasIdentifikasiRincian.created_at",
    )

    @property
    def dokumen_id(self):
        return self.penerimaan_id or self.pembayaran_id


class KasIdentifikasiRincian(BaseModel, BaseMixin):
    __tablename__ = "kas_identifikasi_rincian"
    __table_args__ = (
        CheckConstraint("nilai > 0", name="ck_identifikasi_rincian_positive"),
    )

    identifikasi_id = Column(UUID(as_uuid=True), ForeignKey("kas_identifikasi.id"), nullable=False, index=True)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)
    nilai = Column(Numeric(18, 2), nullable=False)
    keterangan = Column(String(255), nullable=True)

    # Relationships
    identifikasi = relationship("KasIdentifikasi", back_populates="rincian")
    akun_perkiraan = relationship("AkunPerkiraan")
