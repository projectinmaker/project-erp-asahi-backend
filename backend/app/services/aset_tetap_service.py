"""
aset_tetap_service.py

Service layer untuk modul Aset Tetap.
Menghandle CRUD untuk AsetTetap termasuk kalkulasi penyusutan.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.transaksi.aset_tetap.aset_tetap import (
    AsetTetap, MetodePenyusutan, StatusAsetTetap,
)
from app.models.master.kategori_aset import KategoriAset
from app.models.akun_perkiraan import AkunPerkiraan


# ==========================================
# HELPER: Hitung penyusutan per bulan
# ==========================================
def _hitung_penyusutan_per_bulan(
    nilai_perolehan: Decimal,
    nilai_gisa: Decimal,
    umur_aset: int,
) -> Decimal:
    """Hitung penyusutan per bulan (garis lurus).
    penyusutan_per_bulan = (nilai_perolehan - nilai_gisa) / (umur_aset * 12)
    """
    if umur_aset <= 0:
        return Decimal("0")
    dasar = nilai_perolehan - nilai_gisa
    if dasar <= 0:
        return Decimal("0")
    total_bulan = umur_aset * 12
    return dasar / Decimal(str(total_bulan))


def _hitung_nilai_buku(
    nilai_perolehan: Decimal,
    akumulasi_penyusutan: Decimal,
) -> Decimal:
    """Hitung nilai buku = nilai_perolehan - akumulasi_penyusutan."""
    result = nilai_perolehan - akumulasi_penyusutan
    return max(result, Decimal("0"))


# ==========================================
# ASET TETAP CRUD
# ==========================================

def get_aset_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    kategori_aset_id: Optional[UUID] = None,
) -> Tuple[List[AsetTetap], int]:
    """Ambil daftar aset tetap dengan filter & pagination."""
    query = db.query(AsetTetap).options(
        joinedload(AsetTetap.kategori_aset),
        joinedload(AsetTetap.akun_aset),
        joinedload(AsetTetap.akun_akumulasi),
        joinedload(AsetTetap.akun_beban),
        joinedload(AsetTetap.creator),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            AsetTetap.kode.ilike(pattern)
            | AsetTetap.nama.ilike(pattern)
            | AsetTetap.catatan.ilike(pattern)
        )
    if status:
        query = query.filter(AsetTetap.status == status)
    if kategori_aset_id:
        query = query.filter(AsetTetap.kategori_aset_id == kategori_aset_id)

    total = query.count()
    data = query.order_by(AsetTetap.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_aset_by_id(db: Session, aset_id: UUID) -> Optional[AsetTetap]:
    """Ambil 1 aset tetap berdasarkan ID."""
    return (
        db.query(AsetTetap)
        .options(
            joinedload(AsetTetap.kategori_aset),
            joinedload(AsetTetap.akun_aset),
            joinedload(AsetTetap.akun_akumulasi),
            joinedload(AsetTetap.akun_beban),
            joinedload(AsetTetap.creator),
        )
        .filter(AsetTetap.id == aset_id)
        .first()
    )


def create_aset(
    db: Session,
    kode: str,
    nama: str,
    kategori_aset_id: UUID,
    akun_aset_id: UUID,
    akun_akumulasi_id: UUID,
    akun_beban_id: UUID,
    tanggal_mulai: datetime,
    kuantitas: int = 1,
    umur_aset: int = 0,
    metode_penyusutan: str = "GARIS_LURUS",
    nilai_gisa: Decimal = Decimal("0"),
    nilai_perolehan: Decimal = Decimal("0"),
    catatan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
) -> AsetTetap:
    """Buat AsetTetap baru.
    - Auto-hitung penyusutan_per_bulan dan nilai_buku.
    """
    try:
        # Validasi kategori aset
        kategori = db.query(KategoriAset).filter(KategoriAset.id == kategori_aset_id).first()
        if not kategori:
            raise ValueError(f"Kategori Aset dengan ID {kategori_aset_id} tidak ditemukan")

        # Validasi akun
        for akun_id, label in [(akun_aset_id, "Aset"), (akun_akumulasi_id, "Akumulasi"), (akun_beban_id, "Beban")]:
            akun = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == akun_id).first()
            if not akun:
                raise ValueError(f"Akun {label} dengan ID {akun_id} tidak ditemukan")

        # Validasi metode
        metode_enum = MetodePenyusutan(metode_penyusutan)

        # Hitung penyusutan per bulan
        penyusutan_per_bulan = _hitung_penyusutan_per_bulan(
            nilai_perolehan, nilai_gisa, umur_aset
        )

        aset = AsetTetap(
            kode=kode,
            nama=nama,
            kategori_aset_id=kategori_aset_id,
            akun_aset_id=akun_aset_id,
            akun_akumulasi_id=akun_akumulasi_id,
            akun_beban_id=akun_beban_id,
            kuantitas=kuantitas,
            umur_aset=umur_aset,
            metode_penyusutan=metode_enum,
            nilai_gisa=nilai_gisa,
            nilai_perolehan=nilai_perolehan,
            nilai_buku=nilai_perolehan,  # Awal = nilai_perolehan
            akumulasi_penyusutan=Decimal("0"),
            penyusutan_per_bulan=penyusutan_per_bulan,
            tanggal_mulai=tanggal_mulai,
            catatan=catatan,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusAsetTetap.AKTIF,
            created_by=created_by,
        )
        db.add(aset)
        db.commit()
        db.refresh(aset)
        logger.info(f"AsetTetap created: {kode} | {nama} | penyusutan/bln={penyusutan_per_bulan}")
        return aset

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating AsetTetap: {e}")
        raise


def update_aset(
    db: Session,
    db_obj: AsetTetap,
    kode: Optional[str] = None,
    nama: Optional[str] = None,
    kategori_aset_id: Optional[UUID] = None,
    akun_aset_id: Optional[UUID] = None,
    akun_akumulasi_id: Optional[UUID] = None,
    akun_beban_id: Optional[UUID] = None,
    kuantitas: Optional[int] = None,
    umur_aset: Optional[int] = None,
    metode_penyusutan: Optional[str] = None,
    nilai_gisa: Optional[Decimal] = None,
    nilai_perolehan: Optional[Decimal] = None,
    tanggal_mulai: Optional[datetime] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> AsetTetap:
    """Update data aset tetap. Re-calculate penyusutan jika parameter berubah."""
    if db_obj.status == StatusAsetTetap.DIHAPUSKAN:
        raise ValueError("Aset Tetap yang sudah dihapus tidak bisa diupdate")

    recalc = False

    if kode is not None:
        db_obj.kode = kode
    if nama is not None:
        db_obj.nama = nama
    if kategori_aset_id is not None:
        db_obj.kategori_aset_id = kategori_aset_id
    if akun_aset_id is not None:
        db_obj.akun_aset_id = akun_aset_id
    if akun_akumulasi_id is not None:
        db_obj.akun_akumulasi_id = akun_akumulasi_id
    if akun_beban_id is not None:
        db_obj.akun_beban_id = akun_beban_id
    if kuantitas is not None:
        db_obj.kuantitas = kuantitas
    if umur_aset is not None:
        db_obj.umur_aset = umur_aset
        recalc = True
    if metode_penyusutan is not None:
        db_obj.metode_penyusutan = MetodePenyusutan(metode_penyusutan)
    if nilai_gisa is not None:
        db_obj.nilai_gisa = nilai_gisa
        recalc = True
    if nilai_perolehan is not None:
        db_obj.nilai_perolehan = nilai_perolehan
        recalc = True
    if tanggal_mulai is not None:
        db_obj.tanggal_mulai = tanggal_mulai
    if catatan is not None:
        db_obj.catatan = catatan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    # Re-calculate jika nilai_perolehan, nilai_gisa, atau umur_aset berubah
    if recalc:
        db_obj.penyusutan_per_bulan = _hitung_penyusutan_per_bulan(
            db_obj.nilai_perolehan, db_obj.nilai_gisa, db_obj.umur_aset
        )
        db_obj.nilai_buku = _hitung_nilai_buku(
            db_obj.nilai_perolehan, db_obj.akumulasi_penyusutan
        )

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def hapus_aset(db: Session, db_obj: AsetTetap) -> AsetTetap:
    """Hapus aset tetap (soft delete — ubah status ke DIHAPUSKAN)."""
    if db_obj.status == StatusAsetTetap.DIHAPUSKAN:
        raise ValueError("Aset Tetap sudah dihapus")
    db_obj.status = StatusAsetTetap.DIHAPUSKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"AsetTetap deleted: {db_obj.kode} ({db_obj.nama})")
    return db_obj


def set_perbaikan(db: Session, db_obj: AsetTetap) -> AsetTetap:
    """Set aset tetap ke status DALAM_PERBAIKAN."""
    if db_obj.status == StatusAsetTetap.DIHAPUSKAN:
        raise ValueError("Aset Tetap yang sudah dihapus tidak bisa diubah ke perbaikan")
    if db_obj.status == StatusAsetTetap.DALAM_PERBAIKAN:
        raise ValueError("Aset Tetap sudah dalam status perbaikan")
    db_obj.status = StatusAsetTetap.DALAM_PERBAIKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"AsetTetap set to PERBAIKAN: {db_obj.kode}")
    return db_obj


def aktifkan_kembali(db: Session, db_obj: AsetTetap) -> AsetTetap:
    """Aktifkan kembali aset tetap (dari DALAM_PERBAIKAN ke AKTIF)."""
    if db_obj.status != StatusAsetTetap.DALAM_PERBAIKAN:
        raise ValueError(f"Hanya aset dengan status DALAM_PERBAIKAN yang bisa diaktifkan kembali (saat ini: {db_obj.status.value})")
    db_obj.status = StatusAsetTetap.AKTIF
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"AsetTetap reactivated: {db_obj.kode}")
    return db_obj
