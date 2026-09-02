"""
persediaan_service.py

Service layer untuk modul Persediaan.
Menghandle CRUD + auto-posting jurnal untuk:
- PenyesuaianStok
- PemindahanBarang
- PermintaanBarang
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.transaksi.persediaan.penyesuaian_stok import PenyesuaianStok, TipePenyesuaian
from app.models.transaksi.persediaan.pemindahan_barang import PemindahanBarang, ProsesPemindahan
from app.models.transaksi.persediaan.permintaan_barang import PermintaanBarang, StatusPersediaan
from app.models.master.barang import Barang
from app.models.master.gudang import Gudang
from app.models.transaksi.jurnal import RefModule
from app.services.stok_service import update_stok_barang
from app.services import setting_akun_service as sa_cfg
from app.services.posting_service import JurnalEntryItem, auto_posting_jurnal
from app.utils.nomor_dokumen import get_nomor_dokumen


# ==========================================
# HELPER: Mapping kategori barang -> COA persediaan
# ==========================================

def _get_akun_persediaan_id(db: Session, barang: Barang) -> UUID:
    """Tentukan COA persediaan berdasarkan kategori barang.

    Mapping keyword pada nama kategori:
    - 'Bahan Baku' / 'BAKU' -> PERSEDIAAN_BAHAN_BAKU
    - 'WIP' / 'Dalam Proses' -> PERSEDIAAN_WIP
    - 'Barang Jadi' / 'JADI' -> PERSEDIAAN_BARANG_JADI
    - 'Bahan Pembantu' / 'PEMBANTU' -> PERSEDIAAN_BAHAN_PEMBANTU
    - lainnya -> fallback ke PERSEDIAAN_BAHAN_BAKU (dengan warning)
    """
    if not barang.kategori:
        raise ValueError(f"Barang {barang.kode} tidak memiliki kategori")

    nama_upper = barang.kategori.nama.upper()

    if "BAHAN BAKU" in nama_upper or "BAHANBAKU" in nama_upper:
        key = sa_cfg.KEY_PERSEDIAAN_BAHAN_BAKU
    elif "WIP" in nama_upper or "DALAM PROSES" in nama_upper:
        key = sa_cfg.KEY_PERSEDIAAN_WIP
    elif "BARANG JADI" in nama_upper or "JADI" in nama_upper:
        key = sa_cfg.KEY_PERSEDIAAN_BARANG_JADI
    elif "BAHAN PEMBANTU" in nama_upper or "PEMBANTU" in nama_upper:
        key = sa_cfg.KEY_PERSEDIAAN_BAHAN_PEMBANTU
    else:
        logger.warning(
            f"Kategori '{barang.kategori.nama}' tidak dikenali untuk mapping persediaan, "
            f"default ke PERSEDIAAN_BAHAN_BAKU"
        )
        key = sa_cfg.KEY_PERSEDIAAN_BAHAN_BAKU

    return sa_cfg.get_akun_id_or_raise(db, key, f"Barang {barang.kode} ({barang.kategori.nama})")


# ==========================================
# PENYESUAIAN STOK
# ==========================================

def get_penyesuaian_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    barang_id: Optional[UUID] = None,
    tipe: Optional[str] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PenyesuaianStok], int]:
    """Ambil daftar penyesuaian stok dengan filter & pagination."""
    query = db.query(PenyesuaianStok).options(
        joinedload(PenyesuaianStok.barang),
        joinedload(PenyesuaianStok.creator),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PenyesuaianStok.no_adj.ilike(pattern)
            | PenyesuaianStok.alasan.ilike(pattern)
        )
    if status:
        query = query.filter(PenyesuaianStok.status == status)
    if barang_id:
        query = query.filter(PenyesuaianStok.barang_id == barang_id)
    if tipe:
        query = query.filter(PenyesuaianStok.tipe == tipe)
    if tanggal_from:
        query = query.filter(PenyesuaianStok.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PenyesuaianStok.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PenyesuaianStok.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_penyesuaian_by_id(db: Session, adj_id: UUID) -> Optional[PenyesuaianStok]:
    """Ambil 1 penyesuaian stok berdasarkan ID."""
    return (
        db.query(PenyesuaianStok)
        .options(
            joinedload(PenyesuaianStok.barang),
            joinedload(PenyesuaianStok.creator),
            joinedload(PenyesuaianStok.jurnal),
        )
        .filter(PenyesuaianStok.id == adj_id)
        .first()
    )


def create_penyesuaian(
    db: Session,
    tanggal: datetime,
    barang_id: UUID,
    tipe: str,
    qty: int,
    biaya_satuan: Decimal = Decimal("0"),
    alasan: Optional[str] = None,
    auto_post_jurnal: bool = True,
    created_by: Optional[UUID] = None,
) -> PenyesuaianStok:
    """Buat PenyesuaianStok baru.
    - Generate no_adj otomatis (ADJ-YYYY-MM-NNN)
    - Hitung total = qty * biaya_satuan
    - Flag auto_post_jurnal disimpan; jurnal diposting saat approve
    """
    try:
        # Validasi barang
        barang = db.query(Barang).filter(Barang.id == barang_id).first()
        if not barang:
            raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

        # Validasi tipe
        tipe_enum = TipePenyesuaian(tipe)

        total = Decimal(str(qty)) * biaya_satuan

        # Generate nomor adjustment
        no_adj = get_nomor_dokumen(
            db, PenyesuaianStok, prefix="ADJ",
            no_column="no_adj", tanggal=tanggal.date()
        )

        adj = PenyesuaianStok(
            no_adj=no_adj,
            tanggal=tanggal,
            barang_id=barang_id,
            tipe=tipe_enum,
            qty=qty,
            biaya_satuan=biaya_satuan,
            total=total,
            alasan=alasan,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusPersediaan.DIAJUKAN,
            created_by=created_by,
        )
        db.add(adj)
        db.flush()

        db.commit()
        db.refresh(adj)
        logger.info(f"PenyesuaianStok created: {no_adj} | tipe={tipe} | total={total}")
        return adj

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PenyesuaianStok: {e}")
        raise


def update_penyesuaian(
    db: Session,
    db_obj: PenyesuaianStok,
 tanggal: Optional[datetime] = None,
    barang_id: Optional[UUID] = None,
    tipe: Optional[str] = None,
    qty: Optional[int] = None,
    biaya_satuan: Optional[Decimal] = None,
    alasan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> PenyesuaianStok:
    """Update data penyesuaian stok."""
    if db_obj.status in (StatusPersediaan.SELESAI, StatusPersediaan.BATAL, StatusPersediaan.DITOLAK):
        raise ValueError(f"Penyesuaian Stok dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if barang_id is not None:
        db_obj.barang_id = barang_id
    if tipe is not None:
        db_obj.tipe = TipePenyesuaian(tipe)
    if qty is not None:
        db_obj.qty = qty
    if biaya_satuan is not None:
        db_obj.biaya_satuan = biaya_satuan
    if alasan is not None:
        db_obj.alasan = alasan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    # Re-calculate total
    db_obj.total = Decimal(str(db_obj.qty)) * db_obj.biaya_satuan

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def approve_penyesuaian(db: Session, db_obj: PenyesuaianStok) -> PenyesuaianStok:
    """Setujui penyesuaian stok + update stok barang + auto-post jurnal + catat StokMutasi."""
    if db_obj.status != StatusPersediaan.DIAJUKAN:
        raise ValueError(f"Penyesuaian Stok dengan status {db_obj.status.value} tidak bisa disetujui")

    # 1. Update stok barang
    mode = "TAMBAH" if db_obj.tipe.value == "TAMBAH" else "KURANGI"
    update_stok_barang(
        db=db,
        barang_id=db_obj.barang_id,
        qty_change=db_obj.qty,
        mode=mode,
        deskripsi=f"Penyesuaian Stok {db_obj.no_adj} ({mode})",
        ref_module=RefModule.PENYESUAIAN_STOK,
        ref_no=db_obj.no_adj,
        ref_id=db_obj.id,
    )

    # 2. Auto-post jurnal (non-fatal: jika gagal, stok tetap diupdate)
    if db_obj.auto_post_jurnal and db_obj.total > 0:
        try:
            akun_persediaan_id = _get_akun_persediaan_id(db, db_obj.barang)
            akun_selisih_id = sa_cfg.get_akun_id_or_raise(
                db, sa_cfg.KEY_SELISIH_PERSEDIAAN, f"Penyesuaian {db_obj.no_adj}"
            )

            if db_obj.tipe == TipePenyesuaian.TAMBAH:
                # D: Persediaan (naik), K: Selisih Persediaan
                entries = [
                    JurnalEntryItem(
                        akun_perkiraan_id=akun_persediaan_id,
                        debit=db_obj.total,
                        keterangan=f"Penyesuaian Stok + {db_obj.barang.nama}",
                    ),
                    JurnalEntryItem(
                        akun_perkiraan_id=akun_selisih_id,
                        kredit=db_obj.total,
                        keterangan=f"Selisih Penyesuaian Stok {db_obj.no_adj}",
                    ),
                ]
            else:
                # KURANG: D: Selisih Persediaan, K: Persediaan (turun)
                entries = [
                    JurnalEntryItem(
                        akun_perkiraan_id=akun_selisih_id,
                        debit=db_obj.total,
                        keterangan=f"Selisih Penyesuaian Stok {db_obj.no_adj}",
                    ),
                    JurnalEntryItem(
                        akun_perkiraan_id=akun_persediaan_id,
                        kredit=db_obj.total,
                        keterangan=f"Penyesuaian Stok - {db_obj.barang.nama}",
                    ),
                ]

            jurnal = auto_posting_jurnal(
                db=db,
                ref_module=RefModule.PENYESUAIAN_STOK,
                ref_no=db_obj.no_adj,
                entries=entries,
                keterangan=f"Penyesuaian Stok {db_obj.no_adj} ({db_obj.tipe.value})",
                ref_id=db_obj.id,
                tanggal=db_obj.tanggal,
                created_by=db_obj.created_by,
            )
            db_obj.jurnal_umum_id = jurnal.id
            logger.info(
                f"Jurnal penyesuaian stok posted: {jurnal.no_jurnal} | "
                f"{db_obj.tipe.value} | total={db_obj.total}"
            )
        except Exception as e:
            logger.warning(f"Jurnal penyesuaian stok gagal diposting (non-fatal): {e}")

    # 3. Set status + commit
    db_obj.status = StatusPersediaan.DISETUJUI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenyesuaianStok approved: {db_obj.no_adj} | stok {mode} {db_obj.qty}")
    return db_obj


def cancel_penyesuaian(db: Session, db_obj: PenyesuaianStok) -> PenyesuaianStok:
    """Batalkan penyesuaian stok."""
    if db_obj.status in (StatusPersediaan.BATAL, StatusPersediaan.DITOLAK):
        raise ValueError("Penyesuaian Stok sudah dibatalkan/ditolak")
    db_obj.status = StatusPersediaan.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenyesuaianStok cancelled: {db_obj.no_adj}")
    return db_obj


# ==========================================
# PEMINDAHAN BARANG
# ==========================================

def get_pemindahan_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    proses: Optional[str] = None,
    dari_gudang_id: Optional[UUID] = None,
    ke_gudang_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PemindahanBarang], int]:
    """Ambil daftar pemindahan barang dengan filter & pagination."""
    query = db.query(PemindahanBarang).options(
        joinedload(PemindahanBarang.dari_gudang),
        joinedload(PemindahanBarang.ke_gudang),
        joinedload(PemindahanBarang.barang),
        joinedload(PemindahanBarang.creator),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PemindahanBarang.no_pemindahan.ilike(pattern)
            | PemindahanBarang.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PemindahanBarang.status == status)
    if proses:
        query = query.filter(PemindahanBarang.proses == proses)
    if dari_gudang_id:
        query = query.filter(PemindahanBarang.dari_gudang_id == dari_gudang_id)
    if ke_gudang_id:
        query = query.filter(PemindahanBarang.ke_gudang_id == ke_gudang_id)
    if tanggal_from:
        query = query.filter(PemindahanBarang.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PemindahanBarang.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PemindahanBarang.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_pemindahan_by_id(db: Session, pb_id: UUID) -> Optional[PemindahanBarang]:
    """Ambil 1 pemindahan barang berdasarkan ID."""
    return (
        db.query(PemindahanBarang)
        .options(
            joinedload(PemindahanBarang.dari_gudang),
            joinedload(PemindahanBarang.ke_gudang),
            joinedload(PemindahanBarang.barang),
            joinedload(PemindahanBarang.creator),
            joinedload(PemindahanBarang.jurnal),
        )
        .filter(PemindahanBarang.id == pb_id)
        .first()
    )


def create_pemindahan(
    db: Session,
    tanggal: datetime,
    proses: str,
    dari_gudang_id: UUID,
    ke_gudang_id: UUID,
    barang_id: UUID,
    qty: int,
    auto_post_jurnal: bool = False,
    keterangan: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> PemindahanBarang:
    """Buat PemindahanBarang baru.
    - Generate no_pemindahan otomatis (TRF-STK-YYYY-MM-NNN)
    - Validasi gudang asal != gudang tujuan
    - Catatan: Pemindahan antar gudang TIDAK menghasilkan jurnal karena
      total nilai persediaan tidak berubah (hanya pindah lokasi).
      StokMutasi sudah mencatat audit trail pergerakan stok.
    """
    try:
        # Validasi
        if dari_gudang_id == ke_gudang_id:
            raise ValueError("Gudang asal dan tujuan tidak boleh sama")

        dari_gudang = db.query(Gudang).filter(Gudang.id == dari_gudang_id).first()
        if not dari_gudang:
            raise ValueError(f"Gudang asal dengan ID {dari_gudang_id} tidak ditemukan")

        ke_gudang = db.query(Gudang).filter(Gudang.id == ke_gudang_id).first()
        if not ke_gudang:
            raise ValueError(f"Gudang tujuan dengan ID {ke_gudang_id} tidak ditemukan")

        barang = db.query(Barang).filter(Barang.id == barang_id).first()
        if not barang:
            raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

        proses_enum = ProsesPemindahan(proses)

        # Generate nomor pemindahan
        no_pemindahan = get_nomor_dokumen(
            db, PemindahanBarang, prefix="TRF-STK",
            no_column="no_pemindahan", tanggal=tanggal.date()
        )

        pb = PemindahanBarang(
            no_pemindahan=no_pemindahan,
            tanggal=tanggal,
            proses=proses_enum,
            dari_gudang_id=dari_gudang_id,
            ke_gudang_id=ke_gudang_id,
            barang_id=barang_id,
            qty=qty,
            auto_post_jurnal=auto_post_jurnal,
            keterangan=keterangan,
            status=StatusPersediaan.DIAJUKAN,
            created_by=created_by,
        )
        db.add(pb)
        db.flush()

        # Catatan: Pemindahan antar gudang tidak menghasilkan jurnal.
        # Alasan: total nilai persediaan tidak berubah (hanya pindah gudang),
        # dan saat ini hanya ada 1 COA persediaan per kategori (bukan per gudang).
        # D & K ke akun yang sama = entri yang meaningless.
        # StokMutasi sudah mencatat audit trail pergerakan stok.
        # Future: jika ditambahkan COA per gudang (gudang.coa_persediaan_id),
        # maka bisa diimplementasikan D-Persediaan Tujuan / K-Persediaan Asal.
        if auto_post_jurnal and qty > 0:
            logger.info(
                f"Pemindahan {no_pemindahan}: auto_post_jurnal=True tetapi jurnal tidak diposting. "
                f"Pemindahan antar gudang tidak mengubah total nilai persediaan."
            )

        db.commit()
        db.refresh(pb)
        logger.info(f"PemindahanBarang created: {no_pemindahan} | {dari_gudang.nama} -> {ke_gudang.nama} | qty={qty}")
        return pb

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PemindahanBarang: {e}")
        raise


def update_pemindahan(
    db: Session,
    db_obj: PemindahanBarang,
    tanggal: Optional[datetime] = None,
    proses: Optional[str] = None,
    dari_gudang_id: Optional[UUID] = None,
    ke_gudang_id: Optional[UUID] = None,
    barang_id: Optional[UUID] = None,
    qty: Optional[int] = None,
    auto_post_jurnal: Optional[bool] = None,
    keterangan: Optional[str] = None,
) -> PemindahanBarang:
    """Update data pemindahan barang."""
    if db_obj.status in (StatusPersediaan.SELESAI, StatusPersediaan.BATAL, StatusPersediaan.DITOLAK):
        raise ValueError(f"Pemindahan Barang dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if proses is not None:
        db_obj.proses = ProsesPemindahan(proses)
    if dari_gudang_id is not None:
        if dari_gudang_id == db_obj.ke_gudang_id:
            raise ValueError("Gudang asal dan tujuan tidak boleh sama")
        db_obj.dari_gudang_id = dari_gudang_id
    if ke_gudang_id is not None:
        if ke_gudang_id == db_obj.dari_gudang_id:
            raise ValueError("Gudang asal dan tujuan tidak boleh sama")
        db_obj.ke_gudang_id = ke_gudang_id
    if barang_id is not None:
        db_obj.barang_id = barang_id
    if qty is not None:
        db_obj.qty = qty
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal
    if keterangan is not None:
        db_obj.keterangan = keterangan

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def approve_pemindahan(db: Session, db_obj: PemindahanBarang) -> PemindahanBarang:
    """Setujui pemindahan barang + update stok (kurangi dari asal, tambah ke tujuan)."""
    if db_obj.status != StatusPersediaan.DIAJUKAN:
        raise ValueError(f"Pemindahan Barang dengan status {db_obj.status.value} tidak bisa disetujui")

    if db_obj.dari_gudang_id == db_obj.ke_gudang_id:
        raise ValueError("Gudang asal dan tujuan tidak boleh sama")

    # Kurangi stok dari gudang asal
    update_stok_barang(
        db=db,
        barang_id=db_obj.barang_id,
        qty_change=db_obj.qty,
        mode="KURANGI",
        deskripsi=f"Pemindahan keluar {db_obj.no_pemindahan}",
        ref_no=db_obj.no_pemindahan,
        ref_id=db_obj.id,
        gudang_id=db_obj.dari_gudang_id,
    )

    # Tambah stok ke gudang tujuan
    update_stok_barang(
        db=db,
        barang_id=db_obj.barang_id,
        qty_change=db_obj.qty,
        mode="TAMBAH",
        deskripsi=f"Pemindahan masuk {db_obj.no_pemindahan}",
        ref_no=db_obj.no_pemindahan,
        ref_id=db_obj.id,
        gudang_id=db_obj.ke_gudang_id,
    )

    db_obj.status = StatusPersediaan.DISETUJUI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PemindahanBarang approved: {db_obj.no_pemindahan} | qty={db_obj.qty}")
    return db_obj


def cancel_pemindahan(db: Session, db_obj: PemindahanBarang) -> PemindahanBarang:
    """Batalkan pemindahan barang."""
    if db_obj.status in (StatusPersediaan.BATAL, StatusPersediaan.DITOLAK):
        raise ValueError("Pemindahan Barang sudah dibatalkan/ditolak")
    db_obj.status = StatusPersediaan.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PemindahanBarang cancelled: {db_obj.no_pemindahan}")
    return db_obj


# ==========================================
# PERMINTAAN BARANG
# ==========================================

def get_permintaan_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    barang_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PermintaanBarang], int]:
    """Ambil daftar permintaan barang dengan filter & pagination."""
    query = db.query(PermintaanBarang).options(
        joinedload(PermintaanBarang.barang),
        joinedload(PermintaanBarang.creator),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PermintaanBarang.no_permintaan.ilike(pattern)
            | PermintaanBarang.diajukan_oleh.ilike(pattern)
            | PermintaanBarang.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PermintaanBarang.status == status)
    if barang_id:
        query = query.filter(PermintaanBarang.barang_id == barang_id)
    if tanggal_from:
        query = query.filter(PermintaanBarang.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PermintaanBarang.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PermintaanBarang.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_permintaan_by_id(db: Session, req_id: UUID) -> Optional[PermintaanBarang]:
    """Ambil 1 permintaan barang berdasarkan ID."""
    return (
        db.query(PermintaanBarang)
        .options(
            joinedload(PermintaanBarang.barang),
            joinedload(PermintaanBarang.creator),
            joinedload(PermintaanBarang.jurnal),
        )
        .filter(PermintaanBarang.id == req_id)
        .first()
    )


def create_permintaan(
    db: Session,
    tanggal: datetime,
    barang_id: UUID,
    qty: int,
    diajukan_oleh: str,
    keterangan: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> PermintaanBarang:
    """Buat PermintaanBarang baru.
    - Generate no_permintaan otomatis (REQ-YYYY-MM-NNN)
    - Tidak ada jurnal posting (permintaan hanya dokumen internal)
    """
    try:
        # Validasi barang
        barang = db.query(Barang).filter(Barang.id == barang_id).first()
        if not barang:
            raise ValueError(f"Barang dengan ID {barang_id} tidak ditemukan")

        # Generate nomor permintaan
        no_permintaan = get_nomor_dokumen(
            db, PermintaanBarang, prefix="REQ",
            no_column="no_permintaan", tanggal=tanggal.date()
        )

        req = PermintaanBarang(
            no_permintaan=no_permintaan,
            tanggal=tanggal,
            barang_id=barang_id,
            qty=qty,
            diajukan_oleh=diajukan_oleh,
            keterangan=keterangan,
            status=StatusPersediaan.DIAJUKAN,
            created_by=created_by,
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        logger.info(f"PermintaanBarang created: {no_permintaan} | {barang.nama} | qty={qty}")
        return req

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PermintaanBarang: {e}")
        raise


def update_permintaan(
    db: Session,
    db_obj: PermintaanBarang,
    tanggal: Optional[datetime] = None,
    barang_id: Optional[UUID] = None,
    qty: Optional[int] = None,
    diajukan_oleh: Optional[str] = None,
    keterangan: Optional[str] = None,
) -> PermintaanBarang:
    """Update data permintaan barang."""
    if db_obj.status in (StatusPersediaan.SELESAI, StatusPersediaan.BATAL, StatusPersediaan.DITOLAK):
        raise ValueError(f"Permintaan Barang dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if barang_id is not None:
        db_obj.barang_id = barang_id
    if qty is not None:
        db_obj.qty = qty
    if diajukan_oleh is not None:
        db_obj.diajukan_oleh = diajukan_oleh
    if keterangan is not None:
        db_obj.keterangan = keterangan

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def approve_permintaan(db: Session, db_obj: PermintaanBarang) -> PermintaanBarang:
    """Setujui permintaan barang."""
    if db_obj.status != StatusPersediaan.DIAJUKAN:
        raise ValueError(f"Permintaan Barang dengan status {db_obj.status.value} tidak bisa disetujui")
    db_obj.status = StatusPersediaan.DISETUJUI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PermintaanBarang approved: {db_obj.no_permintaan}")
    return db_obj


def cancel_permintaan(db: Session, db_obj: PermintaanBarang) -> PermintaanBarang:
    """Batalkan permintaan barang."""
    if db_obj.status in (StatusPersediaan.BATAL, StatusPersediaan.DITOLAK):
        raise ValueError("Permintaan Barang sudah dibatalkan/ditolak")
    db_obj.status = StatusPersediaan.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PermintaanBarang cancelled: {db_obj.no_permintaan}")
    return db_obj
