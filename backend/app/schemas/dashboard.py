"""
Schemas untuk Dashboard.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional
from app.schemas.base import BaseSchema


class LabaRugiWidget(BaseSchema):
    """schema hanya untuk field, bukan model SQLAlchemy"""
    pass


# Override: manual schema tanpa from_attributes karena ini pure response
class LabaRugiWidget(BaseSchema):
    pendapatan: Decimal = Decimal("0")
    hpp: Decimal = Decimal("0")
    laba_kotor: Decimal = Decimal("0")
    beban: Decimal = Decimal("0")
    laba_bersih: Decimal = Decimal("0")


class CashflowWidget(BaseSchema):
    saldo_awal: Decimal = Decimal("0")
    penerimaan: Decimal = Decimal("0")
    pengeluaran: Decimal = Decimal("0")
    saldo_akhir: Decimal = Decimal("0")


class BebanItem(BaseSchema):
    nama_beban: str
    jumlah: Decimal = Decimal("0")


class BebanBiayaWidget(BaseSchema):
    items: List[BebanItem] = []


class TrenPenjualanItem(BaseSchema):
    bulan: str
    total: Decimal = Decimal("0")


class TrenPenjualanWidget(BaseSchema):
    items: List[TrenPenjualanItem] = []


class FakturJatuhTempoItem(BaseSchema):
    no_faktur: str
    pelanggan: str
    jumlah: Decimal = Decimal("0")
    jatuh_tempo: str
    status: str


class FakturJatuhTempoWidget(BaseSchema):
    items: List[FakturJatuhTempoItem] = []


class AktivitasItem(BaseSchema):
    tipe: str
    deskripsi: str
    nomor: str
    tanggal: str
    jumlah: Optional[Decimal] = None


class AktivitasTerbaruWidget(BaseSchema):
    items: List[AktivitasItem] = []


class DashboardSummaryResponse(BaseSchema):
    laba_rugi: LabaRugiWidget
    cashflow: CashflowWidget
    beban_biaya: BebanBiayaWidget
    tren_penjualan: TrenPenjualanWidget
    faktur_jatuh_tempo: FakturJatuhTempoWidget
    aktivitas_terbaru: AktivitasTerbaruWidget
