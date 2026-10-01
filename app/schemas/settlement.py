from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID
from pydantic import Field
from app.schemas.base import BaseSchema


class AllocationInput(BaseSchema):
    invoice_id: UUID
    nilai: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


class AllocationUpdate(BaseSchema):
    pihak_id: UUID
    alokasi: List[AllocationInput] = Field(min_length=1, max_length=100)
    # Update #5 — penalti pelunasan (opsional di PUT draft; None = tidak mengubah)
    penalti: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    akun_penalti_id: UUID | None = None


class SettlementCreate(AllocationUpdate):
    tanggal: datetime
    kas_bank_id: UUID
    no_nukti: str = Field(min_length=1, max_length=50)
    catatan: Optional[str] = None
    # Update #5 — penalti: menambah total kas yang dibayar/diterima di luar alokasi invoice
    penalti: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)


class AllocationResponse(AllocationInput):
    id: UUID
    akun_perkiraan_id: UUID


class SettlementResponse(BaseSchema):
    id: UUID
    jenis: str
    no_bukti: str
    tanggal: datetime
    total_nilai: Decimal
    penalti: Decimal = Decimal("0")
    akun_penalti_id: UUID | None = None
    status: str
    jurnal_umum_id: Optional[UUID] = None
    pihak_id: Optional[UUID] = None
    alokasi: List[AllocationResponse]


class InvoiceBalanceResponse(BaseSchema):
    invoice_id: UUID
    no_dokumen: str
    pihak_id: UUID
    tanggal: date
    jatuh_tempo: date
    nilai_tagihan: Decimal
    total_bayar: Decimal
    total_retur: Decimal
    sisa_tagihan: Decimal
    kelebihan: Decimal
    status_pembayaran: str
    aktif: bool


class SettlementHistoryResponse(InvoiceBalanceResponse):
    as_of_date: date
    pembayaran: List[dict]
