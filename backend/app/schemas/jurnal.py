from datetime import datetime
from uuid import UUID
from decimal import Decimal
from typing import Optional, List

from app.schemas.base import BaseSchema, PaginatedResponse
from app.schemas.pengguna import PenggunaResponse
from app.schemas.master import COASimpleResponse
from app.models.transaksi.jurnal import RefModule, StatusJurnal


# ==========================================
# HELPER: Detail Line
# ==========================================
class JurnalDetailItemCreate(BaseSchema):
    akun_perkiraan_id: UUID
    debit: Decimal = Decimal("0")
    kredit: Decimal = Decimal("0")
    keterangan: Optional[str] = None


class JurnalDetailItemResponse(BaseSchema):
    id: UUID
    akun_perkiraan_id: UUID
    debit: Decimal
    kredit: Decimal
    keterangan: Optional[str] = None
    akun_perkiraan: COASimpleResponse


class PenggunaSimpleResponse(BaseSchema):
    """Simplified user info for nested responses"""
    id: UUID
    username: str
    nama_lengkap: str


# ==========================================
# JURNAL UMUM - Response
# ==========================================
class JurnalUmumListResponse(BaseSchema):
    """Ringkasan jurnal untuk list (tanpa detail lines)"""
    id: UUID
    no_jurnal: str
    tanggal: datetime
    tipe_transaksi: Optional[str] = None
    ref_module: Optional[RefModule] = None
    ref_no: Optional[str] = None
    total_debit: Decimal
    total_kredit: Decimal
    keterangan: Optional[str] = None
    status: StatusJurnal
    created_by: UUID
    creator: PenggunaSimpleResponse
    created_at: datetime


class JurnalUmumDetailResponse(BaseSchema):
    """Jurnal lengkap dengan detail lines"""
    id: UUID
    no_jurnal: str
    tanggal: datetime
    tipe_transaksi: Optional[str] = None
    ref_module: Optional[RefModule] = None
    ref_no: Optional[str] = None
    ref_id: Optional[UUID] = None
    total_debit: Decimal
    total_kredit: Decimal
    keterangan: Optional[str] = None
    status: StatusJurnal
    created_by: UUID
    creator: PenggunaSimpleResponse
    details: List[JurnalDetailItemResponse] = []
    created_at: datetime
    updated_at: datetime
