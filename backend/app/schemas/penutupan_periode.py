"""
Schemas untuk modul Penutupan Periode.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from app.schemas.base import BaseSchema


class PenggunaSimpleResponse(BaseSchema):
    id: UUID
    nama: str


class JurnalSimpleResponse(BaseSchema):
    id: UUID
    no_jurnal: str


class PenutupanPeriodeResponse(BaseSchema):
    id: UUID
    tahun: int
    bulan: int
    status: str
    laba_rugi: Optional[Decimal] = None
    keterangan: Optional[str] = None
    jurnal_penutupan_id: Optional[UUID] = None
    closed_by: Optional[UUID] = None
    closed_at: Optional[datetime] = None
    reopened_by: Optional[UUID] = None
    reopened_at: Optional[datetime] = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    closer: Optional[PenggunaSimpleResponse] = None
    reopener: Optional[PenggunaSimpleResponse] = None
    jurnal: Optional[JurnalSimpleResponse] = None


class TutupPeriodeRequest(BaseSchema):
    tahun: int
    bulan: int
    keterangan: Optional[str] = None
    with_closing_entry: bool = True  # Default: bikin jurnal penutupan


class BukaPeriodeRequest(BaseSchema):
    tahun: int
    bulan: int
    alasan: Optional[str] = None
