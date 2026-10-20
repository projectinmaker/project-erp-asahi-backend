"""
Schemas untuk modul Kas & Bank.
PembayaranKas, PenerimaanKas, TransferBank + Detail tabel.
FIX v2: Tambah JurnalSimpleResponse + relasi jurnal di 3 Response class.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import BaseSchema
from app.schemas.settlement import AllocationResponse


# === Update #02 — jenis transaksi kas (lihat model JENIS_TRANSAKSI_KAS) ===
JENIS_TRANSAKSI_KAS_VALUES = ("TRANSAKSI_LAINNYA", "BELUM_TERIDENTIFIKASI", "PELUNASAN_INVOICE")


# ==========================================
# HELPER: Nested response untuk relasi
# ==========================================
class AkunPerkiraanSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    username: str
    nama_lengkap: str


# FIX v2: Definisikan JurnalSimpleResponse
class JurnalSimpleResponse(BaseSchema):
    id: UUID
    no_jurnal: str


class KasBankSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    jenis: str
    akun_perkiraan: AkunPerkiraanSimpleResponse


# === Update cetak "Bayar Pemasok" — blok Pemasok (nama + alamat) di print ===
class SupplierSimpleResponse(BaseSchema):
    id: UUID
    nama: str
    alamat: Optional[str] = None


# === Update #01 — blok Pelanggan untuk Penerimaan Kas (pelunasan piutang) ===
class PelangganSimpleResponse(BaseSchema):
    id: UUID
    nama: str
    alamat: Optional[str] = None


# ==========================================
# PEMBAYARAN KAS
# ==========================================
class PembayaranRincianBase(BaseSchema):
    akun_perkiraan_id: UUID
    nilai: Decimal = Decimal("0")


class PembayaranRincianCreate(PembayaranRincianBase):
    pass


class PembayaranRincianResponse(PembayaranRincianBase):
    id: UUID
    akun_perkiraan: AkunPerkiraanSimpleResponse


class PembayaranKasBase(BaseSchema):
    tanggal: datetime
    kas_bank_id: UUID
    no_nukti: str
    no_cek: Optional[str] = None
    # === Update cetak "Bayar Pemasok" — info cek & mata uang ===
    tanggal_cek: Optional[date] = None
    jumlah_cek: Optional[Decimal] = None
    mata_uang: str = "IDR"
    nilai_tukar: Decimal = Decimal("1")
    penerima: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: bool = False


class PembayaranKasCreate(PembayaranKasBase):
    rincian: List[PembayaranRincianCreate] = []
    # === Update #02 — jenis transaksi & nilai tunggal (Belum Teridentifikasi) ===
    jenis_transaksi: Optional[str] = None
    nilai: Optional[Decimal] = Field(default=None, gt=0, max_digits=18, decimal_places=2)

    @model_validator(mode="after")
    def _validasi_jenis_transaksi(self):
        jenis = (self.jenis_transaksi or "TRANSAKSI_LAINNYA").strip().upper() or "TRANSAKSI_LAINNYA"
        if jenis == "PELUNASAN_INVOICE":
            raise ValueError(
                "Pelunasan invoice harus dicatat lewat jenis transaksi 'Pelunasan Invoice' "
                "(pilih supplier → invoice outstanding → nominal → posting), bukan lewat "
                "payload pembayaran umum"
            )
        if jenis not in JENIS_TRANSAKSI_KAS_VALUES:
            raise ValueError(
                f"Jenis transaksi tidak dikenal: {self.jenis_transaksi} "
                f"(pilihan: {', '.join(JENIS_TRANSAKSI_KAS_VALUES)})"
            )
        if jenis == "BELUM_TERIDENTIFIKASI":
            if self.rincian:
                raise ValueError(
                    "Jenis 'Belum Teridentifikasi' tidak memakai rincian akun lawan — "
                    "kirim nilai total saja"
                )
            if self.nilai is None:
                raise ValueError("Nilai wajib diisi untuk jenis 'Belum Teridentifikasi'")
        else:
            if self.nilai is not None:
                raise ValueError("Field nilai hanya untuk jenis 'Belum Teridentifikasi'")
            if not self.rincian:
                raise ValueError("Rincian akun lawan wajib diisi untuk Transaksi Lainnya")
        self.jenis_transaksi = jenis
        return self


class PembayaranKasUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    kas_bank_id: Optional[UUID] = None
    no_nukti: Optional[str] = None
    no_cek: Optional[str] = None
    # === Update cetak "Bayar Pemasok" — info cek & mata uang (editable) ===
    tanggal_cek: Optional[date] = None
    jumlah_cek: Optional[Decimal] = None
    mata_uang: Optional[str] = None
    nilai_tukar: Optional[Decimal] = None
    penerima: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None
    # === Update #02 — jenis immutable (guard service); nilai khusus
    # dokumen BELUM_TERIDENTIFIKASI ===
    jenis_transaksi: Optional[str] = None
    nilai: Optional[Decimal] = Field(default=None, gt=0, max_digits=18, decimal_places=2)


class PembayaranKasResponse(PembayaranKasBase):
    supplier_id: Optional[UUID] = None
    alokasi: List[AllocationResponse] = []
    # === Update cetak "Bayar Pemasok" — relasi supplier untuk blok Pemasok ===
    supplier: Optional[SupplierSimpleResponse] = None
    id: UUID
    no_bukti: str
    total_nilai: Decimal
    penalti: Decimal = Decimal("0")  # Update #5
    akun_penalti_id: UUID | None = None  # Update #5
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    kas_bank: Optional[KasBankSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    rincian: List[PembayaranRincianResponse] = []
    jurnal: Optional[JurnalSimpleResponse] = None
    # === Update #02 — jenis transaksi + progres identifikasi ===
    jenis_transaksi: str = "TRANSAKSI_LAINNYA"
    teridentifikasi: Optional[Decimal] = None
    sisa_belum_teridentifikasi: Optional[Decimal] = None


# ==========================================
# PENERIMAAN KAS
# ==========================================
class PenerimaanRincianBase(BaseSchema):
    akun_perkiraan_id: UUID
    nilai: Decimal = Decimal("0")


class PenerimaanRincianCreate(PenerimaanRincianBase):
    pass


class PenerimaanRincianResponse(PenerimaanRincianBase):
    id: UUID
    akun_perkiraan: AkunPerkiraanSimpleResponse


class PenerimaanKasBase(BaseSchema):
    tanggal: datetime
    kas_bank_id: UUID
    no_nukti: str
    no_cek: Optional[str] = None
    pemberi: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: bool = False


class PenerimaanKasCreate(PenerimaanKasBase):
    rincian: List[PenerimaanRincianCreate] = []
    # === Update #02 — jenis transaksi & nilai tunggal (Belum Teridentifikasi) ===
    jenis_transaksi: Optional[str] = None
    nilai: Optional[Decimal] = Field(default=None, gt=0, max_digits=18, decimal_places=2)

    @model_validator(mode="after")
    def _validasi_jenis_transaksi(self):
        jenis = (self.jenis_transaksi or "TRANSAKSI_LAINNYA").strip().upper() or "TRANSAKSI_LAINNYA"
        if jenis == "PELUNASAN_INVOICE":
            raise ValueError(
                "Pelunasan invoice harus dicatat lewat jenis transaksi 'Pelunasan Invoice' "
                "(pilih pelanggan → invoice penjualan outstanding → nominal → posting), "
                "bukan lewat payload penerimaan umum"
            )
        if jenis not in JENIS_TRANSAKSI_KAS_VALUES:
            raise ValueError(
                f"Jenis transaksi tidak dikenal: {self.jenis_transaksi} "
                f"(pilihan: {', '.join(JENIS_TRANSAKSI_KAS_VALUES)})"
            )
        if jenis == "BELUM_TERIDENTIFIKASI":
            if self.rincian:
                raise ValueError(
                    "Jenis 'Belum Teridentifikasi' tidak memakai rincian akun lawan — "
                    "kirim nilai total saja"
                )
            if self.nilai is None:
                raise ValueError("Nilai wajib diisi untuk jenis 'Belum Teridentifikasi'")
        else:
            if self.nilai is not None:
                raise ValueError("Field nilai hanya untuk jenis 'Belum Teridentifikasi'")
            if not self.rincian:
                raise ValueError("Rincian akun lawan wajib diisi untuk Transaksi Lainnya")
        self.jenis_transaksi = jenis
        return self


class PenerimaanKasUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    kas_bank_id: Optional[UUID] = None
    no_nukti: Optional[str] = None
    no_cek: Optional[str] = None
    pemberi: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None
    # === Update #02 — jenis immutable (guard service); nilai khusus
    # dokumen BELUM_TERIDENTIFIKASI ===
    jenis_transaksi: Optional[str] = None
    nilai: Optional[Decimal] = Field(default=None, gt=0, max_digits=18, decimal_places=2)


class PenerimaanKasResponse(PenerimaanKasBase):
    pelanggan_id: Optional[UUID] = None
    alokasi: List[AllocationResponse] = []
    # === Update #01 — relasi pelanggan untuk blok Pelanggan (pelunasan piutang) ===
    pelanggan: Optional[PelangganSimpleResponse] = None
    id: UUID
    no_bukti: str
    total_nilai: Decimal
    penalti: Decimal = Decimal("0")  # Update #5
    akun_penalti_id: UUID | None = None  # Update #5
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    kas_bank: Optional[KasBankSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    rincian: List[PenerimaanRincianResponse] = []
    jurnal: Optional[JurnalSimpleResponse] = None
    # === Update #02 — jenis transaksi + progres identifikasi ===
    jenis_transaksi: str = "TRANSAKSI_LAINNYA"
    teridentifikasi: Optional[Decimal] = None
    sisa_belum_teridentifikasi: Optional[Decimal] = None


# ==========================================
# TRANSFER BANK
# ==========================================
class TransferBankBase(BaseSchema):
    tanggal: datetime
    dari_kas_bank_id: UUID
    ke_kas_bank_id: UUID
    nilai_transfer: Decimal = Decimal("0")
    biaya_transfer: Decimal = Decimal("0")
    informasi: Optional[str] = None
    auto_post_jurnal: bool = False


class TransferBankCreate(TransferBankBase):
    pass


class TransferBankUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    dari_kas_bank_id: Optional[UUID] = None
    ke_kas_bank_id: Optional[UUID] = None
    nilai_transfer: Optional[Decimal] = None
    biaya_transfer: Optional[Decimal] = None
    informasi: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None


class TransferBankResponse(TransferBankBase):
    id: UUID
    no_transfer: str
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    dari_kas_bank: Optional[KasBankSimpleResponse] = None
    ke_kas_bank: Optional[KasBankSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None

# ==========================================
# UPDATE #02 — IDENTIFIKASI & ALOKASI KAS
# (dokumen BELUM_TERIDENTIFIKASI)
# ==========================================
class IdentifikasiAlokasiCreate(BaseSchema):
    """Satu baris alokasi identifikasi: akun tujuan + nilai (+ keterangan)."""
    akun_perkiraan_id: UUID
    nilai: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    keterangan: Optional[str] = Field(default=None, max_length=255)


class KasIdentifikasiCreate(BaseSchema):
    """Payload sesi Identifikasi & Alokasi atas satu dokumen kas."""
    tanggal: datetime
    alokasi: List[IdentifikasiAlokasiCreate] = Field(min_length=1, max_length=100)
    catatan: Optional[str] = None


# === Update #03 — Identifikasi Piutang/Hutang (pihak + invoice) ===
class IdentifikasiPiutangAlokasiCreate(BaseSchema):
    """Satu baris alokasi identifikasi piutang: sales invoice + nilai (+ PPh 23)."""
    sales_invoice_id: UUID
    nilai: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    pph23: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)


class IdentifikasiPiutangCreate(BaseSchema):
    """Payload identifikasi penerimaan Belum Teridentifikasi ke pelanggan.

    Jurnal reklasifikasi: Dr Penampung / Dr PPh 23 Dibayar di Muka /
    Cr Piutang pelanggan + alokasi invoice (outstanding terbarui).
    """
    pelanggan_id: UUID
    tanggal: datetime
    alokasi: List[IdentifikasiPiutangAlokasiCreate] = Field(min_length=1, max_length=100)
    catatan: Optional[str] = None


class IdentifikasiHutangAlokasiCreate(BaseSchema):
    """Satu baris alokasi identifikasi hutang: purchase invoice + nilai (+ diskon + PPh 23)."""
    purchase_invoice_id: UUID
    nilai: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    diskon: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    pph23: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)


class IdentifikasiHutangCreate(BaseSchema):
    """Payload identifikasi pembayaran Belum Teridentifikasi ke supplier.

    Jurnal reklasifikasi: Cr Penampung / Dr Hutang supplier / Cr Potongan
    Hutang / Cr Hutang PPh 23 + alokasi invoice (outstanding terbarui).
    """
    supplier_id: UUID
    tanggal: datetime
    alokasi: List[IdentifikasiHutangAlokasiCreate] = Field(min_length=1, max_length=100)
    catatan: Optional[str] = None


class IdentifikasiRincianResponse(BaseSchema):
    id: UUID
    akun_perkiraan_id: UUID
    nilai: Decimal
    keterangan: Optional[str] = None
    akun_perkiraan: Optional[AkunPerkiraanSimpleResponse] = None
    # Update #03 — snapshot invoice + potongan per faktur (sesi PIUTANG/HUTANG)
    sales_invoice_id: Optional[UUID] = None
    purchase_invoice_id: Optional[UUID] = None
    diskon: Decimal = Decimal("0")
    pph23: Decimal = Decimal("0")


class KasIdentifikasiResponse(BaseSchema):
    id: UUID
    no_bukti: str
    tanggal: datetime
    penerimaan_id: Optional[UUID] = None
    pembayaran_id: Optional[UUID] = None
    catatan: Optional[str] = None
    total_nilai: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    jurnal: Optional[JurnalSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    rincian: List[IdentifikasiRincianResponse] = []
    # Update #03 — jenis sesi + pihak yang diidentifikasi
    jenis: str = "LAINNYA"
    pelanggan_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None


class KasIdentifikasiListResponse(BaseSchema):
    """Dokumen kas + histori identifikasi + progres teridentifikasi/sisa."""
    dokumen_id: UUID
    jenis_dokumen: str
    no_bukti_dokumen: str
    tanggal_dokumen: datetime
    total_nilai_dokumen: Decimal
    jenis_transaksi_dokumen: str
    status_dokumen: str
    kas_bank: Optional[KasBankSimpleResponse] = None
    penampung_akun: Optional[AkunPerkiraanSimpleResponse] = None
    teridentifikasi: Decimal
    sisa_belum_teridentifikasi: Decimal
    identifikasi: List[KasIdentifikasiResponse] = []


# === Update #03 — Monitoring & rekonsiliasi akun penampung ===
class PenampungAkunMonitoringResponse(BaseSchema):
    """Saldo satu akun penampung + Σ sisa dokumen Belum Teridentifikasi."""
    akun: AkunPerkiraanSimpleResponse
    setting_key: Optional[str] = None
    arah: str
    saldo_gl: Decimal
    jumlah_dokumen_aktif: int
    sisa_penerimaan: Decimal = Decimal("0")
    sisa_pembayaran: Decimal = Decimal("0")
    total_sisa_dokumen: Decimal
    selisih: Decimal
    konsisten: bool


class PenampungCheckResponse(BaseSchema):
    """Hasil satu cek rekonsiliasi penampung."""
    kode: str
    label: str
    ok: bool
    detail: Optional[str] = None


class PenampungMonitoringResponse(BaseSchema):
    """Monitoring & rekonsiliasi semua akun penampung (penerimaan + pembayaran)."""
    akun: List[PenampungAkunMonitoringResponse] = []
    total_sisa: Decimal = Decimal("0")
    rekonsiliasi_ok: bool
    checks: List[PenampungCheckResponse] = []
