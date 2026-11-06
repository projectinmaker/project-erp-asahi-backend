"""=== Update #044 — Schemas Voucher Memorial (Gagal Tagih / penghapusan piutang) ===

Cabang "Gagal Tagih" alur Penerimaan Piutang modul Kas & Bank: dokumen
non-kas yang menghapusbukukan sisa tagihan invoice (Dr Beban Penghapusan
Piutang, Cr Piutang Usaha kontrol per invoice).
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import BaseSchema
from app.schemas.kas_bank import (
    AkunPerkiraanSimpleResponse,
    PenggunaSimpleResponse,
    JurnalSimpleResponse,
    PelangganSimpleResponse,
)


# ==========================================
# VOUCHER MEMORIAL (GAGAL TAGIH)
# ==========================================

class WriteoffAllocationCreate(BaseModel):
    """Baris alokasi penghapusan per invoice (mirror AllocationInput)."""
    invoice_id: UUID
    nilai: Decimal = Field(gt=0, description="Nilai penghapusan (0 < nilai <= sisa tagihan)")


class VoucherMemorialCreate(BaseSchema):
    """Payload POST /kas-bank/voucher-memorial (alur Gagal Tagih)."""
    pelanggan_id: UUID
    tanggal: datetime
    alokasi: List[WriteoffAllocationCreate] = Field(min_length=1, max_length=100)
    catatan: Optional[str] = None
    # Opsional — kosong → default Setting Akun BEBAN_PENGHAPUSAN_PIUTANG.
    akun_beban_id: Optional[UUID] = None


class VoucherMemorialRincianResponse(BaseSchema):
    """Baris detail penghapusan per invoice."""
    id: UUID
    invoice_id: UUID
    no_invoice: Optional[str] = None
    akun_piutang: Optional[AkunPerkiraanSimpleResponse] = None
    nilai: Decimal
    terutang: Optional[Decimal] = None


class VoucherMemorialResponse(BaseSchema):
    id: UUID
    no_bukti: str
    tanggal: datetime
    pelanggan_id: UUID
    pelanggan: Optional[PelangganSimpleResponse] = None
    akun_beban_id: Optional[UUID] = None
    akun_beban: Optional[AkunPerkiraanSimpleResponse] = None
    catatan: Optional[str] = None
    total_nilai: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    jurnal: Optional[JurnalSimpleResponse] = None
    creator: Optional[PenggunaSimpleResponse] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    rincian: List[VoucherMemorialRincianResponse] = []


class VoucherMemorialSummaryResponse(BaseSchema):
    """Ringkasan hasil create (mirror payment_summary settlement)."""
    id: UUID
    no_bukti: str
    tanggal: datetime
    total_nilai: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    pelanggan_id: UUID
    akun_beban_id: Optional[UUID] = None
    alokasi: List[WriteoffAllocationCreate] = []
