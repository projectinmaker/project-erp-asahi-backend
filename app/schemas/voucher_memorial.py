"""=== Update #044 — Schemas Voucher Memorial (Gagal Tagih / penghapusan piutang) ===

Cabang "Gagal Tagih" alur Penerimaan Piutang modul Kas & Bank: dokumen
non-kas yang menghapusbukukan sisa tagihan invoice (Dr Beban Penghapusan
Piutang, Cr Piutang Usaha kontrol per invoice).

=== Update #072 — Voucher Memorial HUTANG (fitur Outstanding Memorial) ===

Gagal Tagih kini juga untuk HUTANG: ``jenis='HUTANG'`` + ``supplier_id`` +
rincian invoice pembelian — jurnal Dr Hutang Usaha (kontrol), Cr Pendapatan
Penghapusan Hutang (kolom ``akun_beban_id`` = akun lawan sisi Kredit).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema
from app.schemas.kas_bank import (
    AkunPerkiraanSimpleResponse,
    PenggunaSimpleResponse,
    JurnalSimpleResponse,
    PelangganSimpleResponse,
    # === Update #072 — pihak jenis HUTANG. ===
    SupplierSimpleResponse,
)


# ==========================================
# VOUCHER MEMORIAL (GAGAL TAGIH)
# ==========================================

class WriteoffAllocationCreate(BaseSchema):
    """Baris alokasi penghapusan per invoice (mirror AllocationInput).

    === Update #072 — mewarisi BaseSchema (bukan BaseModel polos) supaya
    menerima payload camelCase (`invoiceId`) seperti yang dikirim frontend
    (sebelumnya 422 "invoice_id required" — payload Gagal Tagih lama maupun
    aksi Outstanding Memorial baru sama-sama memakai camelCase). ===
    """
    invoice_id: UUID
    nilai: Decimal = Field(gt=0, description="Nilai penghapusan (0 < nilai <= sisa tagihan)")


class VoucherMemorialCreate(BaseSchema):
    """Payload POST /kas-bank/voucher-memorial (alur Gagal Tagih).

    === Update #072 — jenis penghapusan: 'PIUTANG' (default — pelanggan +
    invoice penjualan) atau 'HUTANG' (supplier + invoice pembelian,
    Outstanding Memorial). Pihak dikirim lewat field sesuai jenis. ===
    """
    jenis: str = Field(default="PIUTANG", description="PIUTANG | HUTANG")
    tanggal: datetime
    alokasi: List[WriteoffAllocationCreate] = Field(min_length=1, max_length=100)
    catatan: Optional[str] = None
    # Pihak — salah satu wajib sesuai jenis (server memvalidasi).
    pelanggan_id: Optional[UUID] = None
    # === Update #072 — supplier untuk jenis HUTANG. ===
    supplier_id: Optional[UUID] = None
    # Opsional — kosong → default Setting Akun (PIUTANG: BEBAN_PENGHAPUSAN_
    # PIUTANG; HUTANG: PENDAPATAN_PENGHAPUSAN_HUTANG).
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
    # === Update #072 — jenis penghapusan + pihak supplier. ===
    jenis: str = "PIUTANG"
    pelanggan_id: Optional[UUID] = None
    pelanggan: Optional[PelangganSimpleResponse] = None
    supplier_id: Optional[UUID] = None
    supplier: Optional[SupplierSimpleResponse] = None
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
    # === Update #072 — jenis + supplier. ===
    jenis: str = "PIUTANG"
    total_nilai: Decimal
    status: str
    jurnal_umum_id: Optional[UUID] = None
    pelanggan_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    akun_beban_id: Optional[UUID] = None
    alokasi: List[WriteoffAllocationCreate] = []


# ==========================================
# === Update #072 — OUTSTANDING MEMORIAL ===
# ==========================================

class OutstandingMemorialRow(BaseSchema):
    """Satu baris uraian hutang/piutang (per invoice outstanding).

    Kolom daftar Outstanding Memorial (permintaan user):
    no dokumen, tanggal, nama pelanggan/supplier, inv, nominal, status, aksi.
    """
    jenis: str  # PIUTANG | HUTANG
    invoice_id: UUID
    pihak_id: UUID
    no_dokumen: str
    tanggal: datetime
    pihak_nama: str
    # Referensi eksternal: hutang → No Faktur supplier; piutang → No SO asal
    # ('-' bila invoice mandiri tanpa SO).
    inv: Optional[str] = None
    nilai_tagihan: Decimal
    total_bayar: Decimal
    total_retur: Decimal
    uang_muka: Decimal = Decimal("0")
    total_gagal_tagih: Decimal = Decimal("0")
    # Nominal outstanding (sisa tagihan/hutang) — angka yang diproses aksi.
    nominal: Decimal
    kelebihan: Decimal
    status: str  # BELUM_DIBAYAR / PARSIAL / LUNAS / LEBIH_BAYAR
    # due_date() settlement mengembalikan date (bukan datetime).
    jatuh_tempo: Optional[date] = None
    aktif: bool


class OutstandingMemorialResponse(BaseSchema):
    """Respons GET /kas-bank/outstanding-memorial (gabungan/perorang jenis)."""
    total_piutang: Decimal
    total_hutang: Decimal
    data: List[OutstandingMemorialRow]
    total: int
    skip: int
    limit: int
