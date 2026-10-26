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
# AUD-11 (update #04): + PELUNASAN_PAJAK — pembayaran hutang pajak ke kas/bank
# (akun lawan wajib akun hutang pajak 213xxx, divalidasi service).
# Update #14 — BELUM_TERIDENTIFIKASI dihapus (fitur Identifikasi & Alokasi
# dicabut; dokumen lama dikonversi migration cp0000000021).
JENIS_TRANSAKSI_KAS_VALUES = ("TRANSAKSI_LAINNYA", "PELUNASAN_INVOICE", "PELUNASAN_PAJAK")


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
    # === Update #16 — keterangan per baris rincian (kolom Catatan pada grid
    # detail, paritas Accurate "Pembayaran Lain") ===
    keterangan: Optional[str] = None


class PembayaranRincianCreate(PembayaranRincianBase):
    pass


class PembayaranRincianResponse(PembayaranRincianBase):
    id: UUID
    akun_perkiraan: AkunPerkiraanSimpleResponse


class PembayaranKasBase(BaseSchema):
    tanggal: datetime
    kas_bank_id: UUID
    # === Update #16 — no_nukti kini opsional (field dihapus dari form ASAHI
    # ala Accurate); kosong → service auto-isi = nomor dokumen (no_bukti). ===
    no_nukti: Optional[str] = None
    no_cek: Optional[str] = None
    # === Update #16 — "Cek Kosong" (paritas Accurate): True = pembayaran via
    # cek kosong, No. Cek boleh kosong ===
    cek_kosong: bool = False
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
    # === Update #02 — jenis transaksi (Update #14: nilai tunggal Belum
    # Teridentifikasi dihapus bersama fiturnya) ===
    jenis_transaksi: Optional[str] = None

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
        if not self.rincian:
            raise ValueError("Rincian akun lawan wajib diisi untuk Transaksi Lainnya")
        self.jenis_transaksi = jenis
        return self


class PembayaranKasUpdate(BaseSchema):
    tanggal: Optional[datetime] = None
    kas_bank_id: Optional[UUID] = None
    no_nukti: Optional[str] = None
    no_cek: Optional[str] = None
    # === Update #16 — "Cek Kosong" (paritas Accurate) ===
    cek_kosong: Optional[bool] = None
    # === Update cetak "Bayar Pemasok" — info cek & mata uang (editable) ===
    tanggal_cek: Optional[date] = None
    jumlah_cek: Optional[Decimal] = None
    mata_uang: Optional[str] = None
    nilai_tukar: Optional[Decimal] = None
    penerima: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: Optional[bool] = None
    # === Update #02 — jenis immutable (guard service) ===
    jenis_transaksi: Optional[str] = None


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
    # === Update #02 — jenis transaksi ===
    jenis_transaksi: str = "TRANSAKSI_LAINNYA"


# ==========================================
# PENERIMAAN KAS
# ==========================================
class PenerimaanRincianBase(BaseSchema):
    akun_perkiraan_id: UUID
    nilai: Decimal = Decimal("0")
    # === Update #16 — keterangan per baris rincian (kolom Catatan pada grid
    # detail, paritas Accurate "Penerimaan Lain") ===
    keterangan: Optional[str] = None


class PenerimaanRincianCreate(PenerimaanRincianBase):
    pass


class PenerimaanRincianResponse(PenerimaanRincianBase):
    id: UUID
    akun_perkiraan: AkunPerkiraanSimpleResponse


class PenerimaanKasBase(BaseSchema):
    tanggal: datetime
    kas_bank_id: UUID
    # === Update #16 — no_nukti kini opsional (field dihapus dari form ASAHI
    # ala Accurate); kosong → service auto-isi = nomor dokumen (no_bukti). ===
    no_nukti: Optional[str] = None
    no_cek: Optional[str] = None
    pemberi: Optional[str] = None
    catatan: Optional[str] = None
    auto_post_jurnal: bool = False


class PenerimaanKasCreate(PenerimaanKasBase):
    rincian: List[PenerimaanRincianCreate] = []
    # === Update #02 — jenis transaksi (Update #14: nilai tunggal Belum
    # Teridentifikasi dihapus bersama fiturnya) ===
    jenis_transaksi: Optional[str] = None

    @model_validator(mode="after")
    def _validasi_jenis_transaksi(self):
        jenis = (self.jenis_transaksi or "TRANSAKSI_LAINNYA").strip().upper() or "TRANSAKSI_LAINNYA"
        if jenis == "PELUNASAN_INVOICE":
            raise ValueError(
                "Pelunasan invoice harus dicatat lewat jenis transaksi 'Pelunasan Invoice' "
                "(pilih pelanggan → invoice penjualan outstanding → nominal → posting), "
                "bukan lewat payload penerimaan umum"
            )
        # AUD-11 — pelunasan pajak adalah pengeluaran (modul Pembayaran saja)
        if jenis == "PELUNASAN_PAJAK":
            raise ValueError(
                "Jenis 'Pelunasan Pajak' hanya tersedia di modul Pembayaran "
                "(kas keluar untuk membayar hutang pajak)"
            )
        if jenis not in JENIS_TRANSAKSI_KAS_VALUES:
            raise ValueError(
                f"Jenis transaksi tidak dikenal: {self.jenis_transaksi} "
                f"(pilihan: {', '.join(JENIS_TRANSAKSI_KAS_VALUES)})"
            )
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
    # === Update #02 — jenis immutable (guard service) ===
    jenis_transaksi: Optional[str] = None


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
    # === Update #02 — jenis transaksi ===
    jenis_transaksi: str = "TRANSAKSI_LAINNYA"


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
# === AUD-11 (update #04) — Monitoring hutang pajak ===
# ==========================================
class PajakAkunMonitoringResponse(BaseSchema):
    """Saldo satu akun hutang pajak (system_account_type=TAX_PAYABLE)."""
    id: UUID
    kode: str
    nama: str
    saldo_gl: Decimal = Decimal("0")  # debit - kredit (POSTED)
    hutang: Decimal = Decimal("0")    # positif = masih harus dibayar
    dibayar_lebih: Decimal = Decimal("0")  # debit > kredit (overpayment)


class PajakPelunasanRiwayatResponse(BaseSchema):
    """Satu dokumen pembayaran jenis PELUNASAN_PAJAK."""
    id: UUID
    no_bukti: str
    no_nukti: str
    tanggal: datetime
    total_nilai: Decimal
    catatan: Optional[str] = None
    kas_bank: Optional[dict] = None
    status: str
    jurnal_umum_id: Optional[UUID] = None
    akun: List[dict] = []


class PajakMonitoringResponse(BaseSchema):
    """AUD-11 — rekap hutang pajak + riwayat pelunasan pajak."""
    akun_pajak: List[PajakAkunMonitoringResponse] = []
    total_hutang: Decimal = Decimal("0")
    total_dibayar_lebih: Decimal = Decimal("0")
    riwayat_pelunasan: List[PajakPelunasanRiwayatResponse] = []


# ==========================================
# Update #16 — PREVIEW NOMOR DOKUMEN (form ala Accurate)
# ==========================================
class NomorPreviewResponse(BaseSchema):
    """Nomor dokumen BERIKUTNYA untuk ditampilkan di form (indikatif).

    Prefix per jenis transaksi (reset tahunan, sequence terpisah):
    - Pembayaran: PELUNASAN_INVOICE → "PAY ASI/YYYY/NNN" (tetap);
      lainnya (TRANSAKSI_LAINNYA / PELUNASAN_PAJAK) → "VC ASI/YYYY/NNN"
    - Penerimaan: PELUNASAN_INVOICE → "REC ASI/YYYY/NNN" (tetap);
      lainnya → "PB ASI/YYYY/NNN"

    Angka bisa bergeser bila ada dokumen lain dibuat lebih dulu — nomor final
    tetap di-generate ulang service saat create (preview tidak menyimpan state).
    """
    nomor: str
