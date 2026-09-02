from typing import List, Optional, Tuple
from uuid import UUID
from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import or_

from loguru import logger

from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun, SaldoNormal
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank
from app.models.transaksi.jurnal import JurnalUmum, RefModule, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail
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


# ============================================================
# SALDO AWAL
# ============================================================

def get_saldo_awal(db: Session) -> dict:
    """Cek apakah saldo awal sudah pernah diset.

    Jika sudah, return jurnal SALDO_AWAL yang ada (bisa di-edit ulang).
    Jika belum, return list kosong dengan sudah_diset=False.
    """
    existing = (
        db.query(JurnalUmum)
        .filter(JurnalUmum.ref_module == RefModule.SALDO_AWAL)
        .order_by(JurnalUmum.created_at.desc())
        .first()
    )

    if not existing:
        return {
            "sudah_diset": False,
            "tanggal": None,
            "items": [],
            "total_debit": Decimal("0"),
            "total_kredit": Decimal("0"),
            "selisih": Decimal("0"),
        }

    # Ambil detail jurnal
    details = (
        db.query(
            JurnalDetail.akun_perkiraan_id,
            AkunPerkiraan.kode,
            AkunPerkiraan.nama,
            AkunPerkiraan.saldo_normal,
            JurnalDetail.debit,
            JurnalDetail.kredit,
        )
        .join(AkunPerkiraan, AkunPerkiraan.id == JurnalDetail.akun_perkiraan_id)
        .filter(JurnalDetail.jurnal_umum_id == existing.id)
        .order_by(AkunPerkiraan.kode)
        .all()
    )

    items = []
    total_debit = Decimal("0")
    total_kredit = Decimal("0")
    for d in details:
        items.append({
            "akun_perkiraan_id": d.akun_perkiraan_id,
            "kode_akun": d.kode,
            "nama_akun": d.nama,
            "saldo_normal": d.saldo_normal.value if hasattr(d.saldo_normal, "value") else str(d.saldo_normal),
            "debit": Decimal(str(d.debit)),
            "kredit": Decimal(str(d.kredit)),
        })
        total_debit += Decimal(str(d.debit))
        total_kredit += Decimal(str(d.kredit))

    return {
        "sudah_diset": True,
        "tanggal": existing.tanggal.strftime("%Y-%m-%d") if existing.tanggal else None,
        "items": items,
        "total_debit": total_debit,
        "total_kredit": total_kredit,
        "selisih": total_debit - total_kredit,
    }


def save_saldo_awal(db: Session, items: list, tanggal_str: str, user_id: UUID) -> dict:
    """Simpan saldo awal.

    1. Hapus jurnal SALDO_AWAL lama jika ada (re-set).
    2. Validasi total debit == total kredit.
    3. Buat jurnal baru via posting_service (POSTED).
    4. Update AkunPerkiraan.saldo dan .tanggal untuk setiap akun.
    """
    # 1. Hapus jurnal SALDO_AWAL lama jika ada
    old_jurnals = (
        db.query(JurnalUmum)
        .filter(JurnalUmum.ref_module == RefModule.SALDO_AWAL)
        .all()
    )
    for j in old_jurnals:
        # Hapus detail dulu
        db.query(JurnalDetail).filter(
            JurnalDetail.jurnal_umum_id == j.id
        ).delete()
        db.delete(j)
    db.flush()

    # 2. Filter items yang debit/kredit != 0
    active_items = [
        i for i in items
        if Decimal(str(i["debit"])) != Decimal("0") or Decimal(str(i["kredit"])) != Decimal("0")
    ]

    if not active_items:
        return {
            "sudah_diset": False,
            "tanggal": None,
            "items": [],
            "total_debit": Decimal("0"),
            "total_kredit": Decimal("0"),
            "selisih": Decimal("0"),
        }

    # 3. Validasi balance
    total_debit = sum(Decimal(str(i["debit"])) for i in active_items)
    total_kredit = sum(Decimal(str(i["kredit"])) for i in active_items)
    if total_debit != total_kredit:
        raise ValueError(
            f"Saldo awal tidak balance: total debit={total_debit}, total kredit={total_kredit}"
        )

    # 4. Buat jurnal via posting_service
    from app.services.posting_service import JurnalEntryItem, auto_posting_jurnal

    tanggal = datetime.strptime(tanggal_str, "%Y-%m-%d")

    entries = []
    for i in active_items:
        entries.append(JurnalEntryItem(
            akun_perkiraan_id=i["akun_perkiraan_id"],
            debit=Decimal(str(i["debit"])),
            kredit=Decimal(str(i["kredit"])),
            keterangan="Saldo awal",
        ))

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.SALDO_AWAL,
        ref_no="SA-INIT",
        entries=entries,
        keterangan="Saldo Awal Perusahaan",
        tanggal=tanggal,
        created_by=user_id,
        status=StatusJurnal.POSTED,
        no_jurnal="SA-INIT",
    )

    # 5. Update AkunPerkiraan.saldo dan .tanggal
    for i in active_items:
        akun = db.query(AkunPerkiraan).filter(
            AkunPerkiraan.id == i["akun_perkiraan_id"]
        ).first()
        if akun:
            d = Decimal(str(i["debit"]))
            k = Decimal(str(i["kredit"]))
            if akun.saldo_normal == SaldoNormal.DEBIT:
                akun.saldo = d - k
            else:
                akun.saldo = k - d
            akun.tanggal = tanggal

    db.commit()
    logger.info(f"Saldo awal diset: {len(active_items)} akun, D={total_debit} K={total_kredit}")

    return {
        "sudah_diset": True,
        "tanggal": tanggal_str,
        "items": [
            {
                "akun_perkiraan_id": i["akun_perkiraan_id"],
                "kode_akun": i["kode_akun"],
                "nama_akun": i["nama_akun"],
                "saldo_normal": i["saldo_normal"],
                "debit": Decimal(str(i["debit"])),
                "kredit": Decimal(str(i["kredit"])),
            }
            for i in active_items
        ],
        "total_debit": total_debit,
        "total_kredit": total_kredit,
        "selisih": Decimal("0"),
    }
