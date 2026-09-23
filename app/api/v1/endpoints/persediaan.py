"""
Persediaan Endpoints.
PenyesuaianStok, PemindahanBarang, PermintaanBarang.
"""

from datetime import date, datetime
from typing import Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.schemas.base import PaginatedResponse
from app.schemas.persediaan import (
    PenyesuaianStokCreate, PenyesuaianStokUpdate, PenyesuaianStokResponse,
    PemindahanBarangCreate, PemindahanBarangUpdate, PemindahanBarangResponse,
    PermintaanBarangCreate, PermintaanBarangUpdate, PermintaanBarangResponse,
)
from app.services import persediaan_service as svc
from app.services import dashboard_service

router = APIRouter()


# ==========================================
# INVENTORY VALUATION SUMMARY (P0-02 Re-Audit)
# ==========================================
# Catatan Audit Re-Audit §6:
#   "Inventory master summary harus consume backend aggregate:
#    inventory valuation summary, low stock summary
#    bukan menghitung current page."
#
# Endpoint ini menyediakan kedua aggregate dalam satu panggilan
# supaya halaman Inventory master tidak perlu menghitung dari
# paginated `data` (yang menghasilkan KPI salah).
@router.get("/valuation-summary")
def get_inventory_valuation_summary(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
) -> Dict[str, Any]:
    """Aggregate inventory valuation + low stock summary (backend full dataset).

    Dipakai oleh frontend Inventory master page untuk KPI cards:
        - Total Nilai Persediaan (SUM StockBalance.nilai)
        - Stok Menipis (count of low stock items)

    Return shape:
        {
            "inventory_value": {
                "total_nilai": "12345678.00",
                "total_qty": 500,
                "barang_count": 25,
                "as_of": "2026-09-18T..."
            },
            "low_stock": {
                "count": 5,
                "items": [ {barang_id, kode, nama, stok, stok_minimum, selisih}, ... ]
            }
        }
    """
    as_of = datetime.now()
    inventory_value = dashboard_service.get_inventory_value_widget(db, as_of=as_of)
    low_stock = dashboard_service.get_low_stock_widget(db, limit=10)
    return {
        "inventory_value": inventory_value,
        "low_stock": low_stock,
    }



# ==========================================
# PENYESUAIAN STOK
# ==========================================

@router.get("/penyesuaian-stok", response_model=PaginatedResponse[PenyesuaianStokResponse])
def get_penyesuaian_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no adj, alasan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    barang_id: Optional[UUID] = Query(None, description="Filter barang"),
    tipe: Optional[str] = Query(None, description="Filter tipe (TAMBAH/KURANG)"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Penyesuaian Stok dengan filter dan pagination."""
    data, total = svc.get_penyesuaian_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, barang_id=barang_id, tipe=tipe,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/penyesuaian-stok", response_model=PenyesuaianStokResponse, status_code=status.HTTP_201_CREATED)
def create_penyesuaian(
    data_in: PenyesuaianStokCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Penyesuaian Stok baru (auto-generate no adj + auto-post jurnal)."""
    try:
        return svc.create_penyesuaian(
            db=db,
            gudang_id=data_in.gudang_id,
            tanggal=data_in.tanggal,
            barang_id=data_in.barang_id,
            tipe=data_in.tipe,
            qty=data_in.qty,
            biaya_satuan=data_in.biaya_satuan,
            alasan=data_in.alasan,
            auto_post_jurnal=data_in.auto_post_jurnal,
            created_by=current_user.id,
            tanggal_kedaluwarsa=data_in.tanggal_kedaluwarsa,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/penyesuaian-stok/{adj_id}", response_model=PenyesuaianStokResponse)
def get_penyesuaian_detail(
    adj_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Penyesuaian Stok."""
    item = svc.get_penyesuaian_by_id(db, adj_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penyesuaian Stok tidak ditemukan")
    return item


@router.put("/penyesuaian-stok/{adj_id}", response_model=PenyesuaianStokResponse)
def update_penyesuaian(
    adj_id: UUID,
    data_in: PenyesuaianStokUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Penyesuaian Stok.

    Phase A fix: tanggal_kedaluwarsa hanya di-set kalau explicitly dikirim.
    Kalau field tidak ada di request body, expiry yang sudah tersimpan tetap dipertahankan.
    """
    item = svc.get_penyesuaian_by_id(db, adj_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penyesuaian Stok tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)

    # Phase A: pisahkan tanggal_kedaluwarsa dari update_data lain
    # supaya bisa bedakan "tidak dikirim" (pertahankan existing) vs "dikirim null" (clear expiry)
    tanggal_kedaluwarsa_sent = 'tanggal_kedaluwarsa' in update_data
    tanggal_kedaluwarsa_val = update_data.pop('tanggal_kedaluwarsa', None)

    # Hanya pass tanggal_kedaluwarsa kalau explicitly dikirim
    # Kalau tidak dikirim, service akan terima None (yang artinya "jangan ubah")
    # Tapi karena service update set db_obj.tanggal_kedaluwarsa = tanggal_kedaluwarsa,
    # kita hanya pass kalau explicitly dikirim, dan kalau tidak dikirim kita skip
    if tanggal_kedaluwarsa_sent:
        update_data['tanggal_kedaluwarsa'] = tanggal_kedaluwarsa_val

    try:
        return svc.update_penyesuaian(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/penyesuaian-stok/{adj_id}/approve", response_model=PenyesuaianStokResponse)
def approve_penyesuaian(
    adj_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Setujui Penyesuaian Stok."""
    item = svc.get_penyesuaian_by_id(db, adj_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penyesuaian Stok tidak ditemukan")
    try:
        return svc.approve_penyesuaian(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/penyesuaian-stok/{adj_id}/cancel", response_model=PenyesuaianStokResponse)
def cancel_penyesuaian(
    adj_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Penyesuaian Stok."""
    item = svc.get_penyesuaian_by_id(db, adj_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penyesuaian Stok tidak ditemukan")
    try:
        return svc.cancel_penyesuaian(db, db_obj=item, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PEMINDAHAN BARANG
# ==========================================

@router.get("/pemindahan", response_model=PaginatedResponse[PemindahanBarangResponse])
def get_pemindahan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no pemindahan, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    proses: Optional[str] = Query(None, description="Filter proses (KIRIM/TERIMA)"),
    dari_gudang_id: Optional[UUID] = Query(None, description="Filter gudang asal"),
    ke_gudang_id: Optional[UUID] = Query(None, description="Filter gudang tujuan"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Pemindahan Barang dengan filter dan pagination."""
    data, total = svc.get_pemindahan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, proses=proses,
        dari_gudang_id=dari_gudang_id, ke_gudang_id=ke_gudang_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/pemindahan", response_model=PemindahanBarangResponse, status_code=status.HTTP_201_CREATED)
def create_pemindahan(
    data_in: PemindahanBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Pemindahan Barang baru (auto-generate no pemindahan)."""
    try:
        return svc.create_pemindahan(
            db=db,
            tanggal=data_in.tanggal,
            proses=data_in.proses,
            dari_gudang_id=data_in.dari_gudang_id,
            ke_gudang_id=data_in.ke_gudang_id,
            barang_id=data_in.barang_id,
            qty=data_in.qty,
            auto_post_jurnal=data_in.auto_post_jurnal,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/pemindahan/{pb_id}", response_model=PemindahanBarangResponse)
def get_pemindahan_detail(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Pemindahan Barang."""
    item = svc.get_pemindahan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pemindahan Barang tidak ditemukan")
    return item


@router.put("/pemindahan/{pb_id}", response_model=PemindahanBarangResponse)
def update_pemindahan(
    pb_id: UUID,
    data_in: PemindahanBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Pemindahan Barang."""
    item = svc.get_pemindahan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pemindahan Barang tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_pemindahan(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pemindahan/{pb_id}/approve", response_model=PemindahanBarangResponse)
def approve_pemindahan(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Setujui Pemindahan Barang."""
    item = svc.get_pemindahan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pemindahan Barang tidak ditemukan")
    try:
        return svc.approve_pemindahan(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pemindahan/{pb_id}/cancel", response_model=PemindahanBarangResponse)
def cancel_pemindahan(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Pemindahan Barang."""
    item = svc.get_pemindahan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pemindahan Barang tidak ditemukan")
    try:
        return svc.cancel_pemindahan(db, db_obj=item, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pemindahan/{pb_id}/reverse", response_model=PemindahanBarangResponse)
def reverse_pemindahan(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
    reason: Optional[str] = Query(None, description="Alasan reversal (optional)"),
):
    """Reverse Pemindahan Barang yang sudah di-approve.

    Phase 3 — Master Roadmap §10: "Reversal exact value/layer".

    Berbeda dengan cancel (yang hanya bisa untuk pemindahan status DIAJUKAN),
    reverse bisa untuk pemindahan yang sudah DISETUJUI (stock sudah bergerak).
    Reverse akan:
    1. Restore layer FIFO/FEFO di gudang asal (re-create layer dengan cost asli)
    2. Hapus layer FIFO/FEFO di gudang tujuan (kalau masih ada sisa)
    3. Update StockBalance (qty + nilai) kedua gudang
    4. Update master barang.stok
    5. Set status pemindahan ke BATAL
    6. Catat 2 StokMutasi reversal dengan reversal_of_id link ke mutasi asli

    Catatan: Reverse ini hanya handle stock movement. Jika pemindahan juga
    menghasilkan jurnal (mis. kalau masa depan gudang punya COA terpisah),
    caller perlu reverse jurnal terpisah via endpoint jurnal.
    """
    from app.services.inventory_reversal_service import reverse_pemindahan as _reverse

    try:
        result = _reverse(
            db, pb_id, current_user.id,
            reason=reason or "Reversal pemindahan"
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PERMINTAAN BARANG
# ==========================================

@router.get("/permintaan", response_model=PaginatedResponse[PermintaanBarangResponse])
def get_permintaan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no permintaan, diajukan oleh, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    barang_id: Optional[UUID] = Query(None, description="Filter barang"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Permintaan Barang dengan filter dan pagination."""
    data, total = svc.get_permintaan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, barang_id=barang_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/permintaan", response_model=PermintaanBarangResponse, status_code=status.HTTP_201_CREATED)
def create_permintaan(
    data_in: PermintaanBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Permintaan Barang baru (auto-generate no permintaan)."""
    try:
        return svc.create_permintaan(
            db=db,
            tanggal=data_in.tanggal,
            barang_id=data_in.barang_id,
            qty=data_in.qty,
            diajukan_oleh=data_in.diajukan_oleh,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/permintaan/{req_id}", response_model=PermintaanBarangResponse)
def get_permintaan_detail(
    req_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Permintaan Barang."""
    item = svc.get_permintaan_by_id(db, req_id)
    if not item:
        raise HTTPException(status_code=404, detail="Permintaan Barang tidak ditemukan")
    return item


@router.put("/permintaan/{req_id}", response_model=PermintaanBarangResponse)
def update_permintaan(
    req_id: UUID,
    data_in: PermintaanBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Permintaan Barang."""
    item = svc.get_permintaan_by_id(db, req_id)
    if not item:
        raise HTTPException(status_code=404, detail="Permintaan Barang tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_permintaan(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/permintaan/{req_id}/approve", response_model=PermintaanBarangResponse)
def approve_permintaan(
    req_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Setujui Permintaan Barang."""
    item = svc.get_permintaan_by_id(db, req_id)
    if not item:
        raise HTTPException(status_code=404, detail="Permintaan Barang tidak ditemukan")
    try:
        return svc.approve_permintaan(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/permintaan/{req_id}/cancel", response_model=PermintaanBarangResponse)
def cancel_permintaan(
    req_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Batalkan Permintaan Barang."""
    item = svc.get_permintaan_by_id(db, req_id)
    if not item:
        raise HTTPException(status_code=404, detail="Permintaan Barang tidak ditemukan")
    try:
        return svc.cancel_permintaan(db, db_obj=item, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
