from uuid import UUID
"""
Schemas untuk Laporan/Reporting.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from app.schemas.base import BaseSchema


class Periode(BaseSchema):
    dari: Optional[str] = None
    sampai: Optional[str] = None


class AkunItem(BaseSchema):
    kode_akun: str
    nama_akun: str
    total: Decimal = Decimal("0")


# --- Neraca Saldo (Trial Balance) ---
class NeracaSaldoItem(BaseSchema):
    kode_akun: str
    nama_akun: str
    saldo_normal: str  # DEBIT / KREDIT
    total_debit: Decimal = Decimal("0")
    total_kredit: Decimal = Decimal("0")
    saldo: Decimal = Decimal("0")  # period movement, normal-side sign
    saldo_awal: Decimal = Decimal("0")
    saldo_akhir: Decimal = Decimal("0")
    saldo_debit: Decimal = Decimal("0")
    saldo_kredit: Decimal = Decimal("0")


class NeracaSaldoResponse(BaseSchema):
    total_saldo_debit: Decimal = Decimal("0")
    total_saldo_kredit: Decimal = Decimal("0")
    periode: Periode
    akun: List[NeracaSaldoItem] = []
    total_debit: Decimal = Decimal("0")
    total_kredit: Decimal = Decimal("0")
    selisih: Decimal = Decimal("0")  # harus 0 jika balance


# --- Perubahan Modal (Statement of Changes in Equity) ---
class PerubahanModalItem(BaseSchema):
    kode_akun: str
    nama_akun: str
    saldo_awal: Decimal = Decimal("0")
    mutasi_debit: Decimal = Decimal("0")
    mutasi_kredit: Decimal = Decimal("0")
    perubahan: Decimal = Decimal("0")  # net: kredit - debit (KREDIT normal)
    saldo_akhir: Decimal = Decimal("0")


class PerubahanModalResponse(BaseSchema):
    mutasi_modal_non_penutupan: Decimal = Decimal("0")
    transfer_penutupan: Decimal = Decimal("0")
    laba_belum_ditutup_awal: Decimal = Decimal("0")
    laba_belum_ditutup_akhir: Decimal = Decimal("0")
    selisih_rekonsiliasi: Decimal = Decimal("0")
    periode: Periode
    akun_modal: List[PerubahanModalItem] = []
    laba_rugi_berjalan: Decimal = Decimal("0")
    total_modal_awal: Decimal = Decimal("0")
    total_modal_akhir: Decimal = Decimal("0")


# --- Laba Rugi ---
class LabaRugiResponse(BaseSchema):
    periode: Periode
    pendapatan: List[AkunItem] = []
    hpp: List[AkunItem] = []
    beban: List[AkunItem] = []
    total_pendapatan: Decimal = Decimal("0")
    total_hpp: Decimal = Decimal("0")
    total_beban: Decimal = Decimal("0")
    laba_kotor: Decimal = Decimal("0")
    laba_bersih: Decimal = Decimal("0")


# --- Neraca ---
class NeracaResponse(BaseSchema):
    selisih: Decimal = Decimal("0")
    tanggal: str
    aset: List[AkunItem] = []
    kewajiban: List[AkunItem] = []
    ekuitas: List[AkunItem] = []
    total_aset: Decimal = Decimal("0")
    total_kewajiban: Decimal = Decimal("0")
    total_ekuitas: Decimal = Decimal("0")


# --- Arus Kas ---
class ArusKasItem(BaseSchema):
    journal_id: Optional[UUID] = None
    no_jurnal: Optional[str] = None
    nama: str
    jumlah: Decimal = Decimal("0")


class ArusKasBagian(BaseSchema):
    items: List[ArusKasItem] = []
    total: Decimal = Decimal("0")


class ArusKasResponse(BaseSchema):
    belum_diklasifikasikan: ArusKasBagian
    jumlah_jurnal_belum_diklasifikasi: int = 0
    klasifikasi_lengkap: bool = True
    selisih_rekonsiliasi: Decimal = Decimal("0")
    periode: Periode
    operasional: ArusKasBagian
    investasi: ArusKasBagian
    pembiayaan: ArusKasBagian
    net_change: Decimal = Decimal("0")
    saldo_awal: Decimal = Decimal("0")
    saldo_akhir: Decimal = Decimal("0")


# --- Buku Besar ---
class AkunInfo(BaseSchema):
    kode: str
    nama: str


class BukuBesarTransaksi(BaseSchema):
    tanggal: str
    no_jurnal: str
    deskripsi: str
    debit: Decimal = Decimal("0")
    kredit: Decimal = Decimal("0")
    saldo: Decimal = Decimal("0")


class BukuBesarResponse(BaseSchema):
    akun: AkunInfo
    periode: Periode
    saldo_awal: Decimal = Decimal("0")
    transaksi: List[BukuBesarTransaksi] = []
    total_debit: Decimal = Decimal("0")
    total_kredit: Decimal = Decimal("0")
    saldo_akhir: Decimal = Decimal("0")


# --- Mutasi Kas / Bank ---
class MutasiKasBankTransaksi(BaseSchema):
    tanggal: str
    no_jurnal: str
    deskripsi: str
    debit: Decimal = Decimal("0")
    kredit: Decimal = Decimal("0")
    saldo: Decimal = Decimal("0")
    akun: str = ""


class MutasiKasBankResponse(BaseSchema):
    periode: Periode
    transaksi: List[MutasiKasBankTransaksi] = []


# --- Umur Piutang / Hutang (Aging) ---
class UmurInvoice(BaseSchema):
    invoice_id: Optional[UUID] = None
    nilai_tagihan: Decimal = Decimal("0")
    total_bayar: Decimal = Decimal("0")
    total_retur: Decimal = Decimal("0")
    status_pembayaran: Optional[str] = None
    no_dokumen: str
    tanggal: str
    jatuh_tempo: str
    nilai: Decimal = Decimal("0")
    umur_hari: int = 0  # negatif = belum jatuh tempo


class UmurItem(BaseSchema):
    pihak_id: Optional[UUID] = None
    nama: str
    total: Decimal = Decimal("0")
    belum_jatuh_tempo: Decimal = Decimal("0")
    umur_1_30: Decimal = Decimal("0")
    umur_31_60: Decimal = Decimal("0")
    umur_61_90: Decimal = Decimal("0")
    umur_91_plus: Decimal = Decimal("0")
    rincian: List[UmurInvoice] = []


class UmurPiutangResponse(BaseSchema):
    dokumen_kas_tanpa_alokasi: int = 0
    nilai_kas_tanpa_alokasi: Decimal = Decimal("0")
    retur_tanpa_invoice: int = 0
    nilai_retur_tanpa_invoice: Decimal = Decimal("0")
    kelebihan_pelunasan: Decimal = Decimal("0")
    as_of_date: str
    items: List[UmurItem] = []
    total: Decimal = Decimal("0")
    total_belum_jatuh_tempo: Decimal = Decimal("0")
    total_umur_1_30: Decimal = Decimal("0")
    total_umur_31_60: Decimal = Decimal("0")
    total_umur_61_90: Decimal = Decimal("0")
    total_umur_91_plus: Decimal = Decimal("0")


class UmurHutangResponse(BaseSchema):
    dokumen_kas_tanpa_alokasi: int = 0
    nilai_kas_tanpa_alokasi: Decimal = Decimal("0")
    retur_tanpa_invoice: int = 0
    nilai_retur_tanpa_invoice: Decimal = Decimal("0")
    kelebihan_pelunasan: Decimal = Decimal("0")
    as_of_date: str
    items: List[UmurItem] = []
    total: Decimal = Decimal("0")
    total_belum_jatuh_tempo: Decimal = Decimal("0")
    total_umur_1_30: Decimal = Decimal("0")
    total_umur_31_60: Decimal = Decimal("0")
    total_umur_61_90: Decimal = Decimal("0")
    total_umur_91_plus: Decimal = Decimal("0")


# --- Rekap Kas & Bank ---
class RekapKasBankItem(BaseSchema):
    kode: str
    nama: str
    jenis: str
    saldo_awal: Decimal = Decimal("0")
    total_masuk: Decimal = Decimal("0")
    total_keluar: Decimal = Decimal("0")
    saldo_akhir: Decimal = Decimal("0")


class RekapKasBankResponse(BaseSchema):
    periode: Periode
    akun: List[RekapKasBankItem] = []


class InvalidReportingJournal(BaseSchema):
    journal_id: UUID
    no_jurnal: str


class ReportValidationResponse(BaseSchema):
    periode: Periode
    valid: bool
    checks: dict[str, Decimal]
    jurnal_tidak_valid: List[InvalidReportingJournal]
    klasifikasi_arus_kas_lengkap: bool
    jumlah_jurnal_belum_diklasifikasi: int
