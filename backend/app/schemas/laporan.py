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
    saldo: Decimal = Decimal("0")  # net saldo sesuai saldo_normal


class NeracaSaldoResponse(BaseSchema):
    periode: Periode
    akun: List[NeracaSaldoItem] = []
    total_debit: Decimal = Decimal("0")
    total_kredit: Decimal = Decimal("0")
    selisih: Decimal = Decimal("0")  # harus 0 jika balance


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
    tanggal: str
    aset: List[AkunItem] = []
    kewajiban: List[AkunItem] = []
    ekuitas: List[AkunItem] = []
    total_aset: Decimal = Decimal("0")
    total_kewajiban: Decimal = Decimal("0")
    total_ekuitas: Decimal = Decimal("0")


# --- Arus Kas ---
class ArusKasItem(BaseSchema):
    nama: str
    jumlah: Decimal = Decimal("0")


class ArusKasBagian(BaseSchema):
    items: List[ArusKasItem] = []
    total: Decimal = Decimal("0")


class ArusKasResponse(BaseSchema):
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
