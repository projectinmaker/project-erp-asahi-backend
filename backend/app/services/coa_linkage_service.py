from typing import Optional
from uuid import UUID
from decimal import Decimal
from loguru import logger
from sqlalchemy.orm import Session
from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun, SaldoNormal, HeaderCOA
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank

# Mapping: keyword pencarian GROUP COA -> header enum
_PIUTANG_KEYWORDS = ["PIUTANG", "USAHA"]
_UTANG_KEYWORDS = ["HUTANG", "USAHA"]
_PIUTANG_DETAIL_KEYWORD = "PIUTANG USAHA"
_UTANG_DETAIL_KEYWORD = "HUTANG USAHA"
_PIUTANG_HEADER = HeaderCOA.AKTIVA
_UTANG_HEADER = HeaderCOA.KEWAJIBAN
_PIUTANG_SALDO_NORMAL = SaldoNormal.DEBIT
_UTANG_SALDO_NORMAL = SaldoNormal.KREDIT


def _find_group_coa(db: Session, keywords: list[str], header: HeaderCOA) -> Optional[AkunPerkiraan]:
    """Cari COA GROUP berdasarkan nama yang mengandung keyword."""
    query = db.query(AkunPerkiraan).filter(
        AkunPerkiraan.header == header,
        AkunPerkiraan.status == "AKTIF",
    )
    for kw in keywords:
        query = query.filter(AkunPerkiraan.nama.ilike(f"%{kw}%"))

    # Prioritas: GROUP dulu, lalu HEADER
    group = query.filter(AkunPerkiraan.tingkat == TingkatAkun.GROUP).first()
    if group:
        return group
    header_coa = query.filter(AkunPerkiraan.tingkat == TingkatAkun.HEADER).first()
    return header_coa


def _generate_next_detail_kode(db: Session, parent: AkunPerkiraan) -> str:
    """Generate kode detail berikutnya di bawah parent.

    Contoh: parent kode 130.000.000 -> children 130.000.001, 130.000.002, ...
    """
    parent_parts = parent.kode.split(".")
    prefix = ".".join(parent_parts[:-1])  # "130.000"

    last = (
        db.query(AkunPerkiraan)
        .filter(AkunPerkiraan.kode.like(f"{prefix}.%"))
        .order_by(AkunPerkiraan.kode.desc())
        .first()
    )

    if last:
        try:
            last_seq = int(last.kode.split(".")[-1])
        except (IndexError, ValueError):
            last_seq = 0
    else:
        last_seq = 0

    return f"{prefix}.{last_seq + 1:03d}"


def _create_detail_coa(
    db: Session,
    parent: AkunPerkiraan,
    nama: str,
    saldo_normal: SaldoNormal,
    header: HeaderCOA,
    induk_kode: str,
    saldo: Decimal = Decimal("0"),
) -> AkunPerkiraan:
    """Buat COA DETAIL di bawah parent."""
    kode = _generate_next_detail_kode(db, parent)
    coa = AkunPerkiraan(
        kode=kode,
        nama=nama,
        header=header,
        tingkat=TingkatAkun.DETAIL,
        induk_id=parent.id,
        induk_kode=induk_kode,
        saldo_normal=saldo_normal,
        saldo=saldo,
        status="AKTIF",
    )
    db.add(coa)
    db.flush()
    logger.info(f"Auto-created COA detail: {kode} - {nama} (under {parent.kode})")
    return coa


def auto_create_piutang_coa(db: Session, pelanggan: Pelanggan) -> Optional[UUID]:
    """Auto-buat COA detail Piutang untuk Pelanggan.

    Cari GROUP/HEADER 'Piutang Usaha' -> buat DETAIL dengan nama pelanggan.
    Return: UUID of new COA, atau None jika gagal.
    """
    group = _find_group_coa(db, _PIUTANG_KEYWORDS, _PIUTANG_HEADER)
    if not group:
        logger.warning("COA 'Piutang Usaha' tidak ditemukan, skip auto-create piutang")
        return None

    # Cek apakah sudah ada COA detail untuk pelanggan ini
    existing = (
        db.query(AkunPerkiraan)
        .filter(
            AkunPerkiraan.induk_id == group.id,
            AkunPerkiraan.nama.ilike(f"%{pelanggan.nama}%"),
        )
        .first()
    )
    if existing:
        logger.info(f"COA piutang untuk {pelanggan.nama} sudah ada: {existing.kode}")
        return existing.id

    coa = _create_detail_coa(
        db=db,
        parent=group,
        nama=f"Piutang - {pelanggan.nama}",
        saldo_normal=_PIUTANG_SALDO_NORMAL,
        header=_PIUTANG_HEADER,
        induk_kode=group.kode,
    )
    return coa.id


def auto_create_hutang_coa(db: Session, supplier: Supplier) -> Optional[UUID]:
    """Auto-buat COA detail Hutang untuk Supplier.

    Cari GROUP/HEADER 'Hutang Usaha' -> buat DETAIL dengan nama supplier.
    Return: UUID of new COA, atau None jika gagal.
    """
    group = _find_group_coa(db, _UTANG_KEYWORDS, _UTANG_HEADER)
    if not group:
        logger.warning("COA 'Hutang Usaha' tidak ditemukan, skip auto-create hutang")
        return None

    existing = (
        db.query(AkunPerkiraan)
        .filter(
            AkunPerkiraan.induk_id == group.id,
            AkunPerkiraan.nama.ilike(f"%{supplier.nama}%"),
        )
        .first()
    )
    if existing:
        logger.info(f"COA hutang untuk {supplier.nama} sudah ada: {existing.kode}")
        return existing.id

    coa = _create_detail_coa(
        db=db,
        parent=group,
        nama=f"Hutang - {supplier.nama}",
        saldo_normal=_UTANG_SALDO_NORMAL,
        header=_UTANG_HEADER,
        induk_kode=group.kode,
    )
    return coa.id
