from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from app.schemas.base import BaseSchema


class AssetEventCreate(BaseSchema):
    aset_id: UUID
    jenis: Literal['KAPITALISASI', 'REGISTRASI', 'PENYUSUTAN', 'MUTASI', 'PELEPASAN']
    tanggal: datetime
    umur_bulan: int = Field(default=1, ge=1, le=1200)
    nilai_sisa: Decimal = Field(default=0, ge=0, max_digits=18, decimal_places=2)
    nilai_pelepasan: Decimal = Field(default=0, ge=0, max_digits=18, decimal_places=2)
    akun_lawan_id: UUID | None = None
    akun_laba_rugi_id: UUID | None = None
    lokasi: str | None = Field(default=None, max_length=150)
    source_journal_id: UUID | None = None
    akumulasi_awal: Decimal = Field(default=0, ge=0, max_digits=18, decimal_places=2)

    @model_validator(mode='after')
    def required_fields(self):
        if self.jenis == 'KAPITALISASI' and not self.akun_lawan_id:
            raise ValueError('akunLawanId wajib untuk kapitalisasi')
        if self.jenis == 'REGISTRASI' and not self.source_journal_id:
            raise ValueError('sourceJournalId wajib untuk registrasi aset yang sudah tercatat di buku besar')
        if self.jenis == 'PELEPASAN' and (not self.akun_laba_rugi_id or (self.nilai_pelepasan and not self.akun_lawan_id)):
            raise ValueError('Akun pelepasan belum lengkap')
        if self.jenis == 'MUTASI' and (not self.lokasi or not self.lokasi.strip()):
            raise ValueError('Lokasi wajib diisi')
        return self


class AssetEventResponse(BaseSchema):
    id: UUID
    aset_id: UUID
    jenis: str
    tanggal: datetime
    status: str
    total: Decimal
    parameter: dict
    sebelum: dict | None = None
    sesudah: dict | None = None
    jurnal_umum_id: UUID | None = None
    source_journal_id: UUID | None = None
    created_by: UUID


class AssetCancel(BaseSchema):
    reason: str = Field(min_length=1, max_length=1000)
