"""
aset_tetap_service.py

Service layer untuk modul Aset Tetap.
Penyusutan di-takedown per Phase 1 (meeting 25 Agt 2026).
"""

from datetime import datetime, date
from app.services.accounting_control import atomic_accounting_write
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.transaksi.aset_tetap.aset_tetap import (
    AsetTetap, StatusAsetTetap,
)
from app.models.master.kategori_aset import KategoriAset
from app.models.akun_perkiraan import AkunPerkiraan


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


@atomic_accounting_write
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
    nilai_perolehan: Decimal = Decimal("0"),
    catatan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
) -> AsetTetap:
    """Buat AsetTetap baru."""
    try:
        # Validasi kategori aset
        kategori = db.query(KategoriAset).filter(KategoriAset.id == kategori_aset_id).first()
        if not kategori:
            raise ValueError(f"Kategori Aset dengan ID {kategori_aset_id} tidak ditemukan")

        # Validasi akun
        if len({akun_aset_id, akun_akumulasi_id, akun_beban_id}) != 3:
            raise ValueError('Akun aset, akumulasi, dan beban harus berbeda')
        for akun_id, label in [(akun_aset_id, "Aset"), (akun_akumulasi_id, "Akumulasi"), (akun_beban_id, "Beban")]:
            akun = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == akun_id).first()
            if not akun:
                raise ValueError(f"Akun {label} dengan ID {akun_id} tidak ditemukan")

        if db.query(AsetTetap).filter_by(kode=kode).first():
            raise ValueError('Kode aset sudah digunakan')
        if nilai_perolehan < 0 or kuantitas <= 0:
            raise ValueError('Nilai perolehan/kuantitas aset tidak valid')
        aset = AsetTetap(
            kode=kode,
            nama=nama,
            kategori_aset_id=kategori_aset_id,
            akun_aset_id=akun_aset_id,
            akun_akumulasi_id=akun_akumulasi_id,
            akun_beban_id=akun_beban_id,
            kuantitas=kuantitas,
            nilai_perolehan=nilai_perolehan,
            tanggal_mulai=tanggal_mulai,
            catatan=catatan,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusAsetTetap.AKTIF,
            created_by=created_by,
        )
        db.add(aset)
        db.commit()
        db.refresh(aset)
        logger.info(f"AsetTetap created: {kode} | {nama}")
        return aset

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating AsetTetap: {e}")
        raise


@atomic_accounting_write
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
    nilai_perolehan: Optional[Decimal] = None,
    tanggal_mulai: Optional[datetime] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> AsetTetap:
    """Update data aset tetap."""
    if db_obj.status == StatusAsetTetap.DIHAPUSKAN:
        raise ValueError("Aset Tetap yang sudah dihapus tidak bisa diupdate")

    from app.models.transaksi.asset_event import AssetEvent
    if db_obj.capitalized or db.query(AssetEvent).filter_by(aset_id=db_obj.id).filter(AssetEvent.status != 'BATAL').first():
        raise ValueError('Master aset terkunci setelah transaksi dibuat; batalkan transaksi draft sebelum mengedit')
    if nilai_perolehan is not None and nilai_perolehan < 0 or kuantitas is not None and kuantitas <= 0:
        raise ValueError('Nilai perolehan/kuantitas tidak valid')
    if kode is not None and db.query(AsetTetap).filter(AsetTetap.kode == kode, AsetTetap.id != db_obj.id).first():
        raise ValueError('Kode aset sudah digunakan')
    if kategori_aset_id is not None and db.get(KategoriAset, kategori_aset_id) is None:
        raise ValueError('Kategori aset tidak ditemukan')
    for account_id in (akun_aset_id, akun_akumulasi_id, akun_beban_id):
        if account_id is not None and db.get(AkunPerkiraan, account_id) is None:
            raise ValueError('Akun aset tidak ditemukan')
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
    if nilai_perolehan is not None:
        db_obj.nilai_perolehan = nilai_perolehan
    if tanggal_mulai is not None:
        db_obj.tanggal_mulai = tanggal_mulai
    if catatan is not None:
        db_obj.catatan = catatan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    if len({db_obj.akun_aset_id, db_obj.akun_akumulasi_id, db_obj.akun_beban_id}) != 3:
        raise ValueError('Akun aset, akumulasi, dan beban harus berbeda')
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def hapus_aset(db: Session, db_obj: AsetTetap) -> AsetTetap:
    """Hapus aset tetap (soft delete — ubah status ke DIHAPUSKAN)."""
    if db_obj.status == StatusAsetTetap.DIHAPUSKAN:
        raise ValueError("Aset Tetap sudah dihapus")
    if db_obj.capitalized:
        raise ValueError("Gunakan transaksi PELEPASAN untuk aset yang sudah dikapitalisasi")
    from app.models.transaksi.asset_event import AssetEvent
    if db.query(AssetEvent).filter_by(aset_id=db_obj.id).filter(AssetEvent.status != 'BATAL').first():
        raise ValueError('Batalkan transaksi aset draft terlebih dahulu')
    db_obj.status = StatusAsetTetap.DIHAPUSKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"AsetTetap deleted: {db_obj.kode} ({db_obj.nama})")
    return db_obj


@atomic_accounting_write
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


@atomic_accounting_write
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
