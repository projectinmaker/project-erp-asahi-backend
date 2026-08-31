from typing import List, Optional
from uuid import UUID
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.master.barang import Barang
from app.models.master.gudang import Gudang
from app.models.master.syarat_bayar import SyaratBayar
from app.models.master.kategori_aset import KategoriAset
from app.models.master.kas_bank_akun import KasBankAkun
from app.models.master.kategori_barang import KategoriBarang
from app.models.master.satuan import Satuan
from app.models.detail.barang_satuan import BarangSatuan
from app.schemas.base import PaginatedResponse
from app.schemas.master import (
    PelangganCreate, PelangganUpdate, PelangganResponse,
    SupplierCreate, SupplierUpdate, SupplierResponse,
    BarangCreate, BarangUpdate, BarangResponse,
    BarangSatuanCreate, BarangSatuanUpdate, BarangSatuanResponse,
    KategoriBarangCreate, KategoriBarangUpdate, KategoriBarangResponse,
    SatuanCreate, SatuanUpdate, SatuanResponse,
    GudangCreate, GudangUpdate, GudangResponse,
    SyaratBayarCreate, SyaratBayarUpdate, SyaratBayarResponse,
    KategoriAsetCreate, KategoriAsetUpdate, KategoriAsetResponse,
    KasBankAkunCreate, KasBankAkunUpdate, KasBankAkunResponse,
    COASimpleResponse,
)

# Simple schemas for dropdown
from app.schemas.base import BaseSchema


class BarangSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str
    harga_pokok: Decimal = Decimal("0")
    stok: int = 0


class PelangganSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


class SupplierSimpleResponse(BaseSchema):
    id: UUID
    kode: str
    nama: str


from app.services import master_service

router = APIRouter()


# ==========================================
# PELANGGAN ENDPOINTS
# ==========================================
@router.get("/pelanggan", response_model=PaginatedResponse[PelangganResponse])
def get_pelanggan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None, description="Cari berdasarkan nama atau kode"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Pelanggan, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/pelanggan", response_model=PelangganResponse, status_code=status.HTTP_201_CREATED)
def create_pelanggan(
    data_in: PelangganCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Pelanggan, data_in)

@router.get("/pelanggan/{pelanggan_id}", response_model=PelangganResponse)
def get_pelanggan_detail(
    pelanggan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    return item

@router.put("/pelanggan/{pelanggan_id}", response_model=PelangganResponse)
def update_pelanggan(
    pelanggan_id: UUID,
    data_in: PelangganUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/pelanggan/{pelanggan_id}", status_code=status.HTTP_200_OK)
def delete_pelanggan(
    pelanggan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Pelanggan, pelanggan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Pelanggan sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Pelanggan berhasil dinonaktifkan"}


# ==========================================
# SUPPLIER ENDPOINTS
# ==========================================
@router.get("/supplier", response_model=PaginatedResponse[SupplierResponse])
def get_supplier_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Supplier, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/supplier", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
def create_supplier(
    data_in: SupplierCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Supplier, data_in)

@router.get("/supplier/{supplier_id}", response_model=SupplierResponse)
def get_supplier_detail(supplier_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    return item

@router.put("/supplier/{supplier_id}", response_model=SupplierResponse)
def update_supplier(supplier_id: UUID, data_in: SupplierUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/supplier/{supplier_id}", status_code=status.HTTP_200_OK)
def delete_supplier(supplier_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Supplier, supplier_id)
    if not item: raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Supplier sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Supplier berhasil dinonaktifkan"}


# ==========================================
# BARANG ENDPOINTS
# ==========================================
@router.get("/barang", response_model=PaginatedResponse[BarangResponse])
def get_barang_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    data, total = master_service.get_master_list(db, Barang, skip, limit, ["nama", "kode"], search)
    return {"data": data, "total": total, "skip": skip, "limit": limit}

@router.post("/barang", response_model=BarangResponse, status_code=status.HTTP_201_CREATED)
def create_barang(
    data_in: BarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Barang, data_in)

@router.get("/barang/{barang_id}", response_model=BarangResponse)
def get_barang_detail(barang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    return item

@router.put("/barang/{barang_id}", response_model=BarangResponse)
def update_barang(barang_id: UUID, data_in: BarangUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/barang/{barang_id}", status_code=status.HTTP_200_OK)
def delete_barang(barang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Barang, barang_id)
    if not item: raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Barang sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Barang berhasil dinonaktifkan"}


# ==========================================
# BARANG SATUAN ENDPOINTS (Multi-satuan)
# ==========================================
@router.get("/barang/{barang_id}/satuan", response_model=list[BarangSatuanResponse])
def get_barang_satuan_list(
    barang_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Get daftar satuan untuk suatu barang (termasuk satuan utama dari barang.satuan_id)."""
    barang = master_service.get_master_by_id(db, Barang, barang_id)
    if not barang:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    return db.query(BarangSatuan).filter(
        BarangSatuan.barang_id == barang_id
    ).order_by(BarangSatuan.is_utama.desc()).all()


@router.post("/barang/{barang_id}/satuan", response_model=BarangSatuanResponse, status_code=status.HTTP_201_CREATED)
def add_barang_satuan(
    barang_id: UUID,
    data_in: BarangSatuanCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Tambah satuan ke daftar satuan barang."""
    barang = master_service.get_master_by_id(db, Barang, barang_id)
    if not barang:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    if barang_id != data_in.barang_id:
        raise HTTPException(status_code=400, detail="barang_id di path dan body tidak cocok")
    # Cek duplikat satuan
    existing = db.query(BarangSatuan).filter(
        BarangSatuan.barang_id == barang_id,
        BarangSatuan.satuan_id == data_in.satuan_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Satuan ini sudah terdaftar untuk barang tersebut")
    return master_service.create_master(db, BarangSatuan, data_in)


@router.delete("/barang-satuan/{barang_satuan_id}", status_code=status.HTTP_200_OK)
def delete_barang_satuan(
    barang_satuan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Hapus satuan dari daftar satuan barang."""
    item = db.query(BarangSatuan).filter(BarangSatuan.id == barang_satuan_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Barang Satuan tidak ditemukan")
    if item.is_utama:
        raise HTTPException(status_code=400, detail="Satuan utama tidak bisa dihapus")
    db.delete(item)
    db.commit()
    return {"message": "Satuan berhasil dihapus dari barang"}


# ==========================================
# GUDANG ENDPOINTS
# ==========================================
@router.get("/gudang", response_model=list[GudangResponse])
def get_gudang_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return db.query(Gudang).filter(Gudang.status == "AKTIF").order_by(Gudang.nama).all()

@router.post("/gudang", response_model=GudangResponse, status_code=status.HTTP_201_CREATED)
def create_gudang(
    data_in: GudangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Gudang, data_in)

@router.get("/gudang/{gudang_id}", response_model=GudangResponse)
def get_gudang_detail(gudang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Gudang, gudang_id)
    if not item: raise HTTPException(status_code=404, detail="Gudang tidak ditemukan")
    return item

@router.put("/gudang/{gudang_id}", response_model=GudangResponse)
def update_gudang(gudang_id: UUID, data_in: GudangUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Gudang, gudang_id)
    if not item: raise HTTPException(status_code=404, detail="Gudang tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/gudang/{gudang_id}", status_code=status.HTTP_200_OK)
def delete_gudang(gudang_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, Gudang, gudang_id)
    if not item: raise HTTPException(status_code=404, detail="Gudang tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Gudang sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Gudang berhasil dinonaktifkan"}


# ==========================================
# SYARAT BAYAR ENDPOINTS
# ==========================================
@router.get("/syarat-bayar", response_model=list[SyaratBayarResponse])
def get_syarat_bayar_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return db.query(SyaratBayar).order_by(SyaratBayar.nama).all()

@router.post("/syarat-bayar", response_model=SyaratBayarResponse, status_code=status.HTTP_201_CREATED)
def create_syarat_bayar(
    data_in: SyaratBayarCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, SyaratBayar, data_in)

@router.get("/syarat-bayar/{syarat_bayar_id}", response_model=SyaratBayarResponse)
def get_syarat_bayar_detail(syarat_bayar_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, SyaratBayar, syarat_bayar_id)
    if not item: raise HTTPException(status_code=404, detail="Syarat Bayar tidak ditemukan")
    return item

@router.put("/syarat-bayar/{syarat_bayar_id}", response_model=SyaratBayarResponse)
def update_syarat_bayar(syarat_bayar_id: UUID, data_in: SyaratBayarUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, SyaratBayar, syarat_bayar_id)
    if not item: raise HTTPException(status_code=404, detail="Syarat Bayar tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/syarat-bayar/{syarat_bayar_id}", status_code=status.HTTP_200_OK)
def delete_syarat_bayar(syarat_bayar_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, SyaratBayar, syarat_bayar_id)
    if not item: raise HTTPException(status_code=404, detail="Syarat Bayar tidak ditemukan")
    db.delete(item)
    db.commit()
    return {"message": "Syarat Bayar berhasil dihapus"}


# ==========================================
# KATEGORI ASET ENDPOINTS
# ==========================================
@router.get("/kategori-aset", response_model=list[KategoriAsetResponse])
def get_kategori_aset_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return db.query(KategoriAset).filter(KategoriAset.status == "AKTIF").order_by(KategoriAset.nama).all()

@router.post("/kategori-aset", response_model=KategoriAsetResponse, status_code=status.HTTP_201_CREATED)
def create_kategori_aset(
    data_in: KategoriAsetCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, KategoriAset, data_in)

@router.get("/kategori-aset/{kategori_aset_id}", response_model=KategoriAsetResponse)
def get_kategori_aset_detail(kategori_aset_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KategoriAset, kategori_aset_id)
    if not item: raise HTTPException(status_code=404, detail="Kategori Aset tidak ditemukan")
    return item

@router.put("/kategori-aset/{kategori_aset_id}", response_model=KategoriAsetResponse)
def update_kategori_aset(kategori_aset_id: UUID, data_in: KategoriAsetUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KategoriAset, kategori_aset_id)
    if not item: raise HTTPException(status_code=404, detail="Kategori Aset tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/kategori-aset/{kategori_aset_id}", status_code=status.HTTP_200_OK)
def delete_kategori_aset(kategori_aset_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KategoriAset, kategori_aset_id)
    if not item: raise HTTPException(status_code=404, detail="Kategori Aset tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Kategori Aset sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Kategori Aset berhasil dinonaktifkan"}


# ==========================================
# KAS BANK AKUN ENDPOINTS
# ==========================================
@router.get("/kas-bank-akun", response_model=list[KasBankAkunResponse])
def get_kas_bank_akun_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return db.query(KasBankAkun).filter(KasBankAkun.status == "AKTIF").order_by(KasBankAkun.nama).all()

@router.post("/kas-bank-akun", response_model=KasBankAkunResponse, status_code=status.HTTP_201_CREATED)
def create_kas_bank_akun(
    data_in: KasBankAkunCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, KasBankAkun, data_in)

@router.get("/kas-bank-akun/{kas_bank_akun_id}", response_model=KasBankAkunResponse)
def get_kas_bank_akun_detail(kas_bank_akun_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KasBankAkun, kas_bank_akun_id)
    if not item: raise HTTPException(status_code=404, detail="Kas Bank Akun tidak ditemukan")
    return item

@router.put("/kas-bank-akun/{kas_bank_akun_id}", response_model=KasBankAkunResponse)
def update_kas_bank_akun(kas_bank_akun_id: UUID, data_in: KasBankAkunUpdate, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KasBankAkun, kas_bank_akun_id)
    if not item: raise HTTPException(status_code=404, detail="Kas Bank Akun tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/kas-bank-akun/{kas_bank_akun_id}", status_code=status.HTTP_200_OK)
def delete_kas_bank_akun(kas_bank_akun_id: UUID, db: Session = Depends(get_current_db), current_user: Pengguna = Depends(get_current_user)):
    item = master_service.get_master_by_id(db, KasBankAkun, kas_bank_akun_id)
    if not item: raise HTTPException(status_code=404, detail="Kas Bank Akun tidak ditemukan")
    if item.status == "NONAKTIF": raise HTTPException(status_code=400, detail="Kas Bank Akun sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Kas Bank Akun berhasil dinonaktifkan"}


# ==========================================
# KATEGORI BARANG ENDPOINTS
# ==========================================
@router.get("/kategori-barang", response_model=list[KategoriBarangResponse])
def get_kategori_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Get semua kategori barang AKTIF"""
    return db.query(KategoriBarang).filter(KategoriBarang.status == "AKTIF").order_by(KategoriBarang.nama).all()

@router.post("/kategori-barang", response_model=KategoriBarangResponse, status_code=status.HTTP_201_CREATED)
def create_kategori_barang(
    data_in: KategoriBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, KategoriBarang, data_in)

@router.get("/kategori-barang/{kategori_id}", response_model=KategoriBarangResponse)
def get_kategori_barang_detail(
    kategori_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, KategoriBarang, kategori_id)
    if not item:
        raise HTTPException(status_code=404, detail="Kategori Barang tidak ditemukan")
    return item

@router.put("/kategori-barang/{kategori_id}", response_model=KategoriBarangResponse)
def update_kategori_barang(
    kategori_id: UUID,
    data_in: KategoriBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, KategoriBarang, kategori_id)
    if not item:
        raise HTTPException(status_code=404, detail="Kategori Barang tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/kategori-barang/{kategori_id}", status_code=status.HTTP_200_OK)
def delete_kategori_barang(
    kategori_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, KategoriBarang, kategori_id)
    if not item:
        raise HTTPException(status_code=404, detail="Kategori Barang tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Kategori Barang sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Kategori Barang berhasil dinonaktifkan"}


# ==========================================
# SATUAN ENDPOINTS
# ==========================================
@router.get("/satuan", response_model=list[SatuanResponse])
def get_satuan_list(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Get semua satuan AKTIF"""
    return db.query(Satuan).filter(Satuan.status == "AKTIF").order_by(Satuan.nama).all()

@router.post("/satuan", response_model=SatuanResponse, status_code=status.HTTP_201_CREATED)
def create_satuan(
    data_in: SatuanCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    return master_service.create_master(db, Satuan, data_in)

@router.get("/satuan/{satuan_id}", response_model=SatuanResponse)
def get_satuan_detail(
    satuan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Satuan, satuan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
    return item

@router.put("/satuan/{satuan_id}", response_model=SatuanResponse)
def update_satuan(
    satuan_id: UUID,
    data_in: SatuanUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Satuan, satuan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
    return master_service.update_master(db, item, data_in)

@router.delete("/satuan/{satuan_id}", status_code=status.HTTP_200_OK)
def delete_satuan(
    satuan_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    item = master_service.get_master_by_id(db, Satuan, satuan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
    if item.status == "NONAKTIF":
        raise HTTPException(status_code=400, detail="Satuan sudah tidak aktif")
    master_service.soft_delete_master(db, item)
    return {"message": "Satuan berhasil dinonaktifkan"}


# ==========================================
# ENDPOINT DROPDOWN (Untuk Form Transaksi)
# ==========================================
@router.get("/coa-dropdown", response_model=list[COASimpleResponse])
def get_coa_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Dropdown COA ringan (id, kode, nama) — hanya akun DETAIL yang AKTIF"""
    from app.models.akun_perkiraan import AkunPerkiraan
    from app.models.akun_perkiraan import TingkatAkun
    return db.query(AkunPerkiraan).filter(
        AkunPerkiraan.status == "AKTIF",
        AkunPerkiraan.tingkat == TingkatAkun.DETAIL,
    ).order_by(AkunPerkiraan.kode).all()


@router.get("/barang-dropdown", response_model=list[BarangSimpleResponse])
def get_barang_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Dropdown Barang ringan (id, kode, nama)"""
    return db.query(Barang).filter(
        Barang.status == "AKTIF"
    ).order_by(Barang.nama).all()


@router.get("/pelanggan-dropdown", response_model=list[PelangganSimpleResponse])
def get_pelanggan_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Dropdown Pelanggan ringan (id, kode, nama)"""
    return db.query(Pelanggan).filter(
        Pelanggan.status == "AKTIF"
    ).order_by(Pelanggan.nama).all()


@router.get("/supplier-dropdown", response_model=list[SupplierSimpleResponse])
def get_supplier_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """Dropdown Supplier ringan (id, kode, nama)"""
    return db.query(Supplier).filter(
        Supplier.status == "AKTIF"
    ).order_by(Supplier.nama).all()


@router.get("/kas-bank-dropdown", response_model=list[KasBankAkunResponse])
def get_kas_bank_dropdown(
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user)
):
    """
    Dropdown Kas/Bank Akun yang terhubung ke COA di bawah 'Kas dan Setara Kas'.
    Mencari semua akun DETAIL yang merupakan anak/cucu dari COA tersebut.
    Menggunakan ilike (case-insensitive) supaya tidak bergantung pada huruf besar/kecil.
    """
    from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun

    # 1. Cari COA root "Kas dan Setara Kas" (bisa HEADER atau GROUP)
    #    Pakai ilike supaya case-insensitive (cocokkan "Kas dan Setara Kas" / "KAS DAN SETARA KAS" / dll)
    kas_root_id = db.query(AkunPerkiraan.id).filter(
        AkunPerkiraan.nama.ilike("%KAS%DAN%SETARA%KAS%"),
        AkunPerkiraan.tingkat.in_([TingkatAkun.HEADER, TingkatAkun.GROUP]),
    ).scalar()

    if not kas_root_id:
        return []

    # 2. Recursive CTE: cari semua descendant (anak, cucu, dst)
    base = db.query(AkunPerkiraan.id).filter(
        AkunPerkiraan.induk_id == kas_root_id
    ).cte(name="coa_children", recursive=True)

    recursive = db.query(AkunPerkiraan.id).join(
        base, AkunPerkiraan.induk_id == base.c.id
    )

    all_descendants = base.union(recursive)

    # 3. Ambil hanya akun DETAIL
    detail_ids = [
        row[0] for row in db.query(AkunPerkiraan.id).filter(
            AkunPerkiraan.id.in_(db.query(all_descendants.c.id)),
            AkunPerkiraan.tingkat == TingkatAkun.DETAIL,
        ).all()
    ]

    if not detail_ids:
        return []

    # 4. Filter KasBankAkun yang terhubung ke akun-akun DETAIL tersebut
    return db.query(KasBankAkun).filter(
        KasBankAkun.akun_perkiraan_id.in_(detail_ids),
        KasBankAkun.status == "AKTIF",
    ).order_by(KasBankAkun.nama).all()
