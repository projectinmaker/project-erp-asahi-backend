from typing import List, Optional, Tuple
from uuid import UUID
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import or_

from loguru import logger

from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank
from app.schemas.coa import COACreate, COAUpdate


def get_coa_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    header: Optional[HeaderCOA] = None,
    tingkat: Optional[TingkatAkun] = None,
    search: Optional[str] = None
) -> Tuple[List[AkunPerkiraan], int]:
    """Mengambil daftar COA beserta total datanya."""
    query = db.query(AkunPerkiraan)

    if header:
        query = query.filter(AkunPerkiraan.header == header)
    if tingkat:
        query = query.filter(AkunPerkiraan.tingkat == tingkat)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                AkunPerkiraan.kode.ilike(search_pattern),
                AkunPerkiraan.nama.ilike(search_pattern)
            )
        )

    # Hitung total SEBELUM di-slice (penting untuk pagination)
    total = query.count()

    # Slice datanya
    data = query.order_by(AkunPerkiraan.kode).offset(skip).limit(limit).all()

    return data, total


def get_coa_by_id(db: Session, coa_id: UUID) -> Optional[AkunPerkiraan]:
    """Mengambil 1 COA berdasarkan UUID."""
    return db.query(AkunPerkiraan).filter(AkunPerkiraan.id == coa_id).first()


def get_coa_by_kode(db: Session, kode: str) -> Optional[AkunPerkiraan]:
    """Mengambil 1 COA berdasarkan Kode Akun."""
    return db.query(AkunPerkiraan).filter(AkunPerkiraan.kode == kode).first()


def _get_jenis_kas_bank_for_coa(db: Session, coa_id: UUID) -> Optional[str]:
    """Cek apakah COA ini punya relasi KasBankAkun. Return 'KAS' atau 'BANK'."""
    kb = db.query(KasBankAkun).filter(KasBankAkun.akun_perkiraan_id == coa_id).first()
    if kb:
        return kb.jenis.value
    return None


def create_coa(db: Session, coa_in: COACreate) -> AkunPerkiraan:
    """Membuat COA baru.

    Jika field jenis_kas_bank diisi ('KAS' atau 'BANK'), akan auto-membuat
    KasBankAkun yang mengaitkan COA ini ke modul Kas & Bank.
    """
    # Extract jenis_kas_bank sebelum dump (bukan field model)
    jenis_kas_bank = coa_in.jenis_kas_bank

    db_obj = AkunPerkiraan(**coa_in.model_dump(exclude={"jenis_kas_bank"}))
    db.add(db_obj)
    db.flush()  # Flush dulu untuk dapat ID

    # Auto-buat KasBankAkun jika jenis_kas_bank diisi
    if jenis_kas_bank and jenis_kas_bank in ("KAS", "BANK"):
        _auto_create_kas_bank_akun(db, db_obj, jenis_kas_bank)

    db.commit()
    db.refresh(db_obj)
    logger.info(f"COA created: {db_obj.kode} - {db_obj.nama}")
    return db_obj


def _auto_create_kas_bank_akun(
    db: Session, coa: AkunPerkiraan, jenis: str
) -> Optional[KasBankAkun]:
    """Buat KasBankAkun otomatis saat COA detail Kas/Bank dibuat.

    Generate kode otomatis: BK-NNN (Bank) atau KK-NNN (Kas).
    """
    # Cek apakah sudah ada KasBankAkun untuk COA ini
    existing = db.query(KasBankAkun).filter(
        KasBankAkun.akun_perkiraan_id == coa.id
    ).first()
    if existing:
        logger.warning(f"KasBankAkun sudah ada untuk COA {coa.kode}, skip auto-create")
        return existing

    # Generate kode: BK-NNN (Bank) atau KK-NNN (Kas)
    prefix = "BK" if jenis == "BANK" else "KK"
    last_kb = (
        db.query(KasBankAkun)
        .filter(KasBankAkun.kode.like(f"{prefix}-%"))
        .order_by(KasBankAkun.kode.desc())
        .first()
    )

    if last_kb:
        try:
            last_seq = int(last_kb.kode.split("-")[1])
        except (IndexError, ValueError):
            last_seq = 0
    else:
        last_seq = 0

    next_seq = last_seq + 1
    kode_kb = f"{prefix}-{next_seq:03d}"

    kb = KasBankAkun(
        kode=kode_kb,
        nama=coa.nama,
        jenis=JenisKasBank(jenis),
        akun_perkiraan_id=coa.id,
        saldo=Decimal("0"),
        status="AKTIF",
    )
    db.add(kb)
    logger.info(f"KasBankAkun auto-created: {kode_kb} - {coa.nama} ({jenis})")
    return kb


def update_coa(db: Session, db_obj: AkunPerkiraan, obj_in: COAUpdate) -> AkunPerkiraan:
    """Update data COA yang sudah ada."""
    update_data = obj_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_obj, field, value)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj
