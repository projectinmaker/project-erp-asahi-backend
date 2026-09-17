from typing import Optional
from uuid import UUID
from decimal import Decimal
from loguru import logger
from sqlalchemy.orm import Session
from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun, SaldoNormal, HeaderCOA
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank
from app.services.setting_akun_service import get_akun_id, KEY_PIUTANG_USAHA, KEY_HUTANG_USAHA

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
    """Cari COA GROUP berdasarkan nama yang mengandung keyword.
    Prioritas: GROUP dulu, lalu HEADER.
    """
    query = db.query(AkunPerkiraan).filter(
        AkunPerkiraan.header == header,
        AkunPerkiraan.status == "AKTIF",
    )
    for kw in keywords:
        query = query.filter(AkunPerkiraan.nama.ilike(f"%{kw}%"))

    group = query.filter(AkunPerkiraan.tingkat == TingkatAkun.GROUP).first()
    if group:
        return group
    header_coa = query.filter(AkunPerkiraan.tingkat == TingkatAkun.HEADER).first()
    return header_coa


def _generate_next_detail_kode(db: Session, parent: AkunPerkiraan) -> str:
    """Generate kode detail berikutnya di bawah parent.

    Support 2 format kode yang mungkin ada di DB:
    - FLAT 9-digit: 111000000 -> 111000001, 111000002, ...
    - DOTTED: 111.000.000 -> 111.000.001, 111.000.002, ...
    Format hasil generate mengikuti format kode parent-nya (deteksi via
    ada/tidaknya '.').

    Logic:
    1. Cari semua child langsung dari parent (induk_id == parent.id)
    2. Ambil kode terbesar, increment 3 digit terakhir
    3. Jika belum ada child, mulai dari parent.kode dengan 3 digit terakhir = 001
    """
    parent_kode = parent.kode  # e.g. "111000000" atau "111.000.000"
    is_dotted = "." in parent_kode

    # Cari child terakhir di bawah parent ini
    last = (
        db.query(AkunPerkiraan)
        .filter(AkunPerkiraan.induk_id == parent.id)
        .order_by(AkunPerkiraan.kode.desc())
        .first()
    )

    if last:
        # Ambil 3 digit terakhir dari kode child (buang '.' dulu kalau ada)
        last_digits_only = last.kode.replace(".", "")
        try:
            last_seq = int(last_digits_only[-3:])
        except (IndexError, ValueError):
            last_seq = 0
        next_seq = last_seq + 1
    else:
        # Belum ada child, mulai dari 001
        next_seq = 1

    if is_dotted:
        # Ambil 2 segmen pertama dari parent (misal "111.000"), tambah segmen baru
        segments = parent_kode.split(".")
        prefix = ".".join(segments[:2])  # "111.000"
        return f"{prefix}.{next_seq:03d}"
    else:
        # Ambil 6 digit depan parent, tambahkan 3 digit sequence
        prefix_6 = parent_kode[:6]
        return f"{prefix_6}{next_seq:03d}"


def _create_detail_coa(
    db: Session,
    parent: AkunPerkiraan,
    nama: str,
    saldo_normal: SaldoNormal,
    header: HeaderCOA,
    induk_kode: str,
    saldo: Decimal = Decimal("0"),
    is_subledger: bool = False,
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
        is_subledger=is_subledger,
    )
    db.add(coa)
    db.flush()
    logger.info(f"Auto-created COA detail: {kode} - {nama} (under {parent.kode})")
    return coa


def _resolve_root_coa(
    db: Session, setting_key: str, keywords: list[str], header: HeaderCOA, label: str
) -> Optional[AkunPerkiraan]:
    """Cari COA root (Piutang/Hutang Usaha) — utamakan dari setting_akun
    (KEY_PIUTANG_USAHA/KEY_HUTANG_USAHA) supaya tetap jalan walau user ganti
    nama COA. Fallback ke pencarian by-nama (keyword) kalau setting belum
    di-configure, untuk backward-compatibility.
    """
    akun_id = get_akun_id(db, setting_key)
    if akun_id:
        coa = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == akun_id).first()
        if coa:
            return coa
        logger.warning(
            f"setting_akun {setting_key} nunjuk ke COA yang sudah tidak ada, "
            f"fallback ke pencarian by-nama"
        )

    logger.warning(
        f"setting_akun {setting_key} belum di-configure, fallback ke pencarian "
        f"by-nama untuk '{label}'. Sebaiknya configure via PUT /master/setting-akun/{setting_key}"
    )
    return _find_group_coa(db, keywords, header)


def find_piutang_root_coa(db: Session) -> Optional[AkunPerkiraan]:
    """Cari COA GROUP/HEADER root 'Piutang Usaha'. Wrapper publik dari _find_group_coa."""
    return _find_group_coa(db, _PIUTANG_KEYWORDS, _PIUTANG_HEADER)


def get_coa_detail_ids_under(db: Session, root_id: UUID) -> list[UUID]:
    """Ambil semua id COA level DETAIL yang merupakan descendant (anak/cucu/dst) dari root_id."""
    base = db.query(AkunPerkiraan.id).filter(
        AkunPerkiraan.induk_id == root_id
    ).cte(name="coa_descendants", recursive=True)

    recursive = db.query(AkunPerkiraan.id).join(
        base, AkunPerkiraan.induk_id == base.c.id
    )
    all_descendants = base.union(recursive)

    return [
        row[0] for row in db.query(AkunPerkiraan.id).filter(
            AkunPerkiraan.id.in_(db.query(all_descendants.c.id)),
            AkunPerkiraan.tingkat == TingkatAkun.DETAIL,
        ).all()
    ]


def find_hutang_root_coa(db: Session) -> Optional[AkunPerkiraan]:
    """Cari COA GROUP/HEADER root 'Hutang Usaha'. Wrapper publik dari _find_group_coa."""
    return _find_group_coa(db, _UTANG_KEYWORDS, _UTANG_HEADER)


def auto_create_piutang_coa(db: Session, pelanggan: Pelanggan) -> Optional[UUID]:
    """Auto-buat COA detail Piutang untuk Pelanggan.

    Cari GROUP/HEADER 'Piutang Usaha' -> buat DETAIL dengan nama pelanggan.
    Return: UUID of new COA, atau None jika gagal.
    """
    group = _resolve_root_coa(db, KEY_PIUTANG_USAHA, _PIUTANG_KEYWORDS, _PIUTANG_HEADER, "Piutang Usaha")
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
        is_subledger=True,
    )
    return coa.id


def auto_create_hutang_coa(db: Session, supplier: Supplier) -> Optional[UUID]:
    """Auto-buat COA detail Hutang untuk Supplier.

    Cari GROUP/HEADER 'Hutang Usaha' -> buat DETAIL dengan nama supplier.
    Return: UUID of new COA, atau None jika gagal.
    """
    group = _resolve_root_coa(db, KEY_HUTANG_USAHA, _UTANG_KEYWORDS, _UTANG_HEADER, "Hutang Usaha")
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
        is_subledger=True,
    )
    return coa.id