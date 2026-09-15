"""asset_validation.py

Helper untuk enforce Master Roadmap §24 (Fixed Asset):
- validate_asset_category_mapping: cek KategoriAset punya 3 akun FK terisi
- validate_asset_cost_immutable: cek nilai_perolehan tidak diubah setelah capitalization
- validate_asset_active: cek AsetTetap status AKTIF sebelum dipakai transaksi

Dipakai oleh:
- app/services/asset_cycle_service.py (create/update/post event)
- app/services/aset_tetap_service.py (create/update aset)
"""
from uuid import UUID
from typing import Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from decimal import Decimal

from app.models.transaksi.aset_tetap.aset_tetap import (
    AsetTetap, StatusAsetTetap, AcquisitionSourceType,
)
from app.models.master.kategori_aset import KategoriAset


# ==========================================
# Asset Category Validation
# ==========================================
def validate_asset_category_mapping(
    db: Session,
    kategori_aset_id: UUID,
    context: str = "Create Aset Tetap",
) -> KategoriAset:
    """Cek apakah KategoriAset punya 3 akun FK terisi (asset_cost, accum_depr, depreciation_expense).

    Sesuai Master Roadmap §24:
        "Asset category accounting mapping" (P0)

    Dipanggil saat create aset tetap baru untuk memastikan kategori sudah
    punya mapping akun yang lengkap.

    Raises:
        HTTPException 400 kalau kategori tidak ditemukan atau akun belum di-configure
    """
    kategori = db.get(KategoriAset, kategori_aset_id)
    if kategori is None:
        raise HTTPException(
            status_code=404,
            detail=f"Kategori Aset dengan ID {kategori_aset_id} tidak ditemukan. {context} dibatalkan.",
        )
    if kategori.status != "AKTIF":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Kategori Aset '{kategori.nama}' (kode: {kategori.kode}) "
                f"sudah {kategori.status} — tidak boleh dipakai transaksi baru. "
                f"{context} dibatalkan."
            ),
        )

    # Cek 3 akun FK terisi
    missing = []
    if not kategori.akun_aset_id:
        missing.append("akun_aset_id (Asset Cost Account)")
    if not kategori.akun_akumulasi_id:
        missing.append("akun_akumulasi_id (Accumulated Depreciation Account)")
    if not kategori.akun_beban_id:
        missing.append("akun_beban_id (Depreciation Expense Account)")

    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Kategori Aset '{kategori.nama}' belum punya mapping akun lengkap. "
                f"Missing: {', '.join(missing)}. "
                f"Lengkapi mapping akun di Kategori Aset terlebih dahulu. "
                f"{context} dibatalkan."
            ),
        )

    return kategori


# ==========================================
# Asset Cost Immutable Validation
# ==========================================
def validate_asset_cost_immutable(
    asset: AsetTetap,
    new_nilai_perolehan: Optional[Decimal] = None,
    context: str = "Update Aset Tetap",
) -> None:
    """Cek apakah nilai_perolehan tidak diubah setelah capitalization.

    Sesuai Master Roadmap §24:
        "Asset original cost immutable after capitalization except controlled adjustment"

    Logic:
    - Kalau asset.capitalized == True (sudah dikapitalisasi), nilai_perolehan
      tidak boleh diubah. Hanya controlled adjustment (mis. via AssetEvent
      jenis KAPITALISASI ulang) yang bisa ubah.
    - Kalau asset.capitalized == False (belum dikapitalisasi), nilai_perolehan
      boleh diubah (kasus typo saat create).

    Raises:
        HTTPException 400 kalau coba ubah nilai_perolehan setelah capitalization
    """
    if not asset.capitalized:
        # Belum dikapitalisasi — boleh ubah
        return

    if new_nilai_perolehan is None:
        # Tidak ada perubahan nilai_perolehan
        return

    if Decimal(str(new_nilai_perolehan)) != Decimal(str(asset.nilai_perolehan)):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Nilai perolehan aset '{asset.nama}' (kode: {asset.kode}) tidak boleh diubah "
                f"setelah capitalization. Current: {asset.nilai_perolehan}, "
                f"attempted: {new_nilai_perolehan}. "
                f"Gunakan AssetEvent jenis KAPITALISASI ulang (controlled adjustment) "
                f"kalau memang perlu koreksi nilai. "
                f"{context} dibatalkan."
            ),
        )


# ==========================================
# Asset Active Validation
# ==========================================
def validate_asset_active(
    db: Session,
    aset_id: UUID,
    context: str = "Transaksi Aset",
) -> AsetTetap:
    """Cek apakah AsetTetap masih AKTIF sebelum dipakai transaksi.

    Sesuai Master Roadmap §24:
        Asset harus AKTIF untuk dipakai event baru (penyusutan, pelepasan, mutasi)

    Raises:
        HTTPException 404 kalau aset tidak ditemukan
        HTTPException 400 kalau aset DIHAPUSKAN atau DALAM_PERBAIKAN
    """
    asset = db.get(AsetTetap, aset_id)
    if asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Aset Tetap dengan ID {aset_id} tidak ditemukan. {context} dibatalkan.",
        )
    if asset.status == StatusAsetTetap.DIHAPUSKAN:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Aset '{asset.nama}' (kode: {asset.kode}) sudah DIHAPUSKAN — "
                f"tidak bisa dipakai transaksi baru. {context} dibatalkan."
            ),
        )
    if asset.status == StatusAsetTetap.DALAM_PERBAIKAN:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Aset '{asset.nama}' (kode: {asset.kode}) sedang DALAM_PERBAIKAN — "
                f"tidak bisa dipakai transaksi baru sampai status kembali AKTIF. "
                f"{context} dibatalkan."
            ),
        )
    return asset


# ==========================================
# Acquisition Source Validation
# ==========================================
def validate_acquisition_source(
    db: Session,
    acquisition_source_type: Optional[str],
    acquisition_source_id: Optional[UUID],
    context: str = "Create Aset Tetap",
) -> None:
    """Cek apakah acquisition source valid (kalau diisi).

    Sesuai Master Roadmap §24:
        "Acquisition source trace"

    Logic:
    - Kalau acquisition_source_type diisi, acquisition_source_id harus diisi juga
    - Kalau acquisition_source_type = 'MANUAL_JOURNAL', acquisition_source_id
      harus valid UUID jurnal_umum.id dengan status POSTED
    - Kalau acquisition_source_type = 'PURCHASE_INVOICE', acquisition_source_id
      harus valid UUID purchase_invoice.id dengan status POSTED
    - Kalau acquisition_source_type = 'DIRECT' atau 'SALDO_AWAL',
      acquisition_source_id boleh null

    Raises:
        HTTPException 400 kalau acquisition source invalid
    """
    if not acquisition_source_type:
        # Tidak ada source — boleh (kasus lama sebelum Phase 7)
        return

    try:
        source_type = AcquisitionSourceType(acquisition_source_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Acquisition source type '{acquisition_source_type}' tidak valid. "
                f"Valid values: {[t.value for t in AcquisitionSourceType]}. "
                f"{context} dibatalkan."
            ),
        )

    # DIRECT & SALDO_AWAL tidak perlu source_id
    if source_type in (AcquisitionSourceType.DIRECT, AcquisitionSourceType.SALDO_AWAL):
        return

    # MANUAL_JOURNAL & PURCHASE_INVOICE perlu source_id
    if not acquisition_source_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Acquisition source type '{source_type.value}' memerlukan acquisition_source_id. "
                f"{context} dibatalkan."
            ),
        )

    # Validate source exists
    if source_type == AcquisitionSourceType.MANUAL_JOURNAL:
        from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal
        source = db.get(JurnalUmum, acquisition_source_id)
        if source is None:
            raise HTTPException(
                status_code=404,
                detail=f"Jurnal sumber dengan ID {acquisition_source_id} tidak ditemukan. {context} dibatalkan.",
            )
        if source.status != StatusJurnal.POSTED:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Jurnal sumber {source.no_jurnal} status={source.status.value} — "
                    f"hanya jurnal POSTED yang bisa dipakai acquisition source. "
                    f"{context} dibatalkan."
                ),
            )
    elif source_type == AcquisitionSourceType.PURCHASE_INVOICE:
        from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
        from app.models.transaksi.penjualan.sales_order import StatusPenjualan
        source = db.get(PurchaseInvoice, acquisition_source_id)
        if source is None:
            raise HTTPException(
                status_code=404,
                detail=f"Purchase Invoice dengan ID {acquisition_source_id} tidak ditemukan. {context} dibatalkan.",
            )
        if source.status not in (StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Purchase Invoice {source.no_form} status={source.status.value} — "
                    f"hanya invoice POSTED yang bisa dipakai acquisition source. "
                    f"{context} dibatalkan."
                ),
            )
