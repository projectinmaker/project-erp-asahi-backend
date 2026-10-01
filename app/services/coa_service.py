from typing import List, Optional, Tuple
from uuid import UUID
from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import or_

from loguru import logger

from app.models.akun_perkiraan import (
    AkunPerkiraan,
    HeaderCOA,
    TingkatAkun,
    SaldoNormal,
    HEADER_TO_ACCOUNT_CLASS,
    NODE_TYPE_TO_TINGKAT,
)
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank
from app.models.master.setting_akun import SettingAkun
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.transaksi.jurnal import JurnalUmum, RefModule, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail
from app.schemas.coa import COACreate, COAUpdate


# ==========================================
# Helper: derive account_class from header (backward compat)
# ==========================================
def _derive_account_class_from_header(header: HeaderCOA) -> str:
    """Auto-derive account_class (ASSET/LIABILITY/...) dari header enum lama
    (AKTIVA/KEWAJIBAN/...). Dipakai kalau COACreate.account_class None."""
    return HEADER_TO_ACCOUNT_CLASS.get(header, "ASSET")


# ==========================================
# Helper: sync active <-> status (canonical <-> legacy)
# ==========================================
def _sync_active_status(coa: AkunPerkiraan) -> None:
    """Pastikan status (AKTIF/NONAKTIF) dan active (True/False) konsisten.

    Aturan: `active` adalah canonical. Jika `active=True` -> status='AKTIF'.
    Jika `active=False` -> status='NONAKTIF'.
    Dipanggil setelah set field di create/update.
    """
    if coa.active:
        coa.status = "AKTIF"
    else:
        coa.status = "NONAKTIF"


def get_coa_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    header: Optional[HeaderCOA] = None,
    tingkat: Optional[TingkatAkun] = None,
    search: Optional[str] = None,
    include_subledger: bool = False,
    is_control_account: Optional[bool] = None,
    subledger_type: Optional[str] = None,
    active_only: Optional[bool] = None,
    account_class: Optional[str] = None,
    allow_manual_posting: Optional[bool] = None,
) -> Tuple[List[AkunPerkiraan], int]:
    """Mengambil daftar COA beserta total datanya.

    Filter baru (ASAHI COA Revisi v2):
    - is_control_account: filter akun control account (AR/AP/INVENTORY)
    - subledger_type: filter berdasarkan jenis subledger (AR/AP/INVENTORY)
    - active_only: True = hanya active, False = hanya inactive, None = semua
    - account_class: filter ASSET/LIABILITY/EQUITY/REVENUE/COGS/EXPENSE
    - allow_manual_posting: filter akun yang bisa dipakai jurnal manual
    """
    query = db.query(AkunPerkiraan)

    if not include_subledger:
        query = query.filter(AkunPerkiraan.is_subledger == False)  # noqa: E712

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

    # === New filters ===
    if is_control_account is not None:
        query = query.filter(AkunPerkiraan.is_control_account == is_control_account)
    if subledger_type:
        query = query.filter(AkunPerkiraan.subledger_type == subledger_type.upper())
    if active_only is not None:
        query = query.filter(AkunPerkiraan.active == active_only)
    if account_class:
        query = query.filter(AkunPerkiraan.account_class == account_class.upper())
    if allow_manual_posting is not None:
        query = query.filter(AkunPerkiraan.allow_manual_posting == allow_manual_posting)

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

    Field baru (ASAHI COA Revisi v2):
    - account_class: kalau None, di-derive dari `header`.
    - allow_system_posting / allow_manual_posting: default True kalau None.
    - is_control_account / reconciliation_required: default False kalau None.
    - active: default True kalau None, sync ke `status`.
    """
    # Extract jenis_kas_bank sebelum dump (bukan field model).
    # type_code/is_sub/structural_type hanya input resolver registry — bukan
    # field model (sudah di-resolve endpoint jadi field legacy + new).
    jenis_kas_bank = coa_in.jenis_kas_bank

    # Default untuk field baru kalau None
    create_data = coa_in.model_dump(
        exclude={"jenis_kas_bank", "type_code", "is_sub", "structural_type"})

    if create_data.get("account_class") is None:
        create_data["account_class"] = _derive_account_class_from_header(coa_in.header)

    # Set defaults untuk posting control & active
    if create_data.get("allow_system_posting") is None:
        create_data["allow_system_posting"] = True
    if create_data.get("allow_manual_posting") is None:
        create_data["allow_manual_posting"] = True
    if create_data.get("is_control_account") is None:
        create_data["is_control_account"] = False
    if create_data.get("reconciliation_required") is None:
        create_data["reconciliation_required"] = False
    if create_data.get("active") is None:
        create_data["active"] = True

    db_obj = AkunPerkiraan(**create_data)
    _sync_active_status(db_obj)

    db.add(db_obj)
    db.flush()  # Flush dulu untuk dapat ID

    # Auto-buat KasBankAkun jika jenis_kas_bank diisi
    if jenis_kas_bank and jenis_kas_bank in ("KAS", "BANK"):
        _auto_create_kas_bank_akun(db, db_obj, jenis_kas_bank)

    db.commit()
    db.refresh(db_obj)
    logger.info(f"COA created: {db_obj.kode} - {db_obj.nama} (class={db_obj.account_class})")
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
    """Update data COA yang sudah ada.

    Sinkronisasi otomatis active <-> status: jika salah satu di-update,
    field pasangan ikut di-sync supaya konsisten.
    """
    update_data = obj_in.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(db_obj, field, value)

    # Sync active <-> status setelah apply semua update
    if "active" in update_data or "status" in update_data:
        _sync_active_status(db_obj)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


# ============================================================
# DELETE
# ============================================================

def delete_coa(db: Session, coa: AkunPerkiraan) -> None:
    """Hapus Akun Perkiraan (hard delete).

    Validasi urutan (raise ValueError dengan pesan jelas kalau gagal):
    1. Sudah punya transaksi jurnal (jurnal_detail)
    2. HEADER/GROUP yang masih punya sub-akun (induk_id)
    3. Subledger yang terhubung ke pelanggan/supplier
    4. Dipakai di setting_akun
    5. Dipakai di kas_bank_akun

    Untuk akuntansi, hard-delete hanya aman untuk akun yang belum pernah
    dipakai transaksi. Kalau sudah dipakai / masih ada dependency, akun
    sebaiknya dinonaktifkan (active=False) lewat PUT, bukan dihapus.
    """
    from app.models.master.barang import Barang
    if db.query(Barang.id).filter(Barang.akun_persediaan_id == coa.id).first():
        raise ValueError('Akun terhubung sebagai akun Persediaan barang; lepaskan mapping sebelum menghapus akun')
    if db.query(JurnalDetail).filter(JurnalDetail.akun_perkiraan_id == coa.id).first():
        raise ValueError(
            "Akun ini sudah punya transaksi jurnal, tidak bisa dihapus. "
            "Nonaktifkan akun ini (ubah active=False / status=NONAKTIF) jika sudah tidak dipakai."
        )

    if db.query(AkunPerkiraan).filter(AkunPerkiraan.induk_id == coa.id).first():
        raise ValueError(
            "Akun ini masih punya sub-akun di bawahnya. Hapus atau pindahkan "
            "sub-akun tersebut dulu sebelum menghapus akun induk ini."
        )

    if db.query(Pelanggan).filter(Pelanggan.akun_piutang_id == coa.id).first():
        raise ValueError(
            "Akun ini terhubung sebagai akun piutang pelanggan. Lepaskan "
            "keterkaitannya dari data pelanggan terlebih dahulu."
        )

    if db.query(Supplier).filter(Supplier.akun_hutang_id == coa.id).first():
        raise ValueError(
            "Akun ini terhubung sebagai akun hutang supplier. Lepaskan "
            "keterkaitannya dari data supplier terlebih dahulu."
        )

    if db.query(SettingAkun).filter(SettingAkun.akun_perkiraan_id == coa.id).first():
        raise ValueError(
            "Akun ini sedang dipakai di Setting Akun (mapping akun default). "
            "Ganti setting-nya ke akun lain dulu sebelum menghapus."
        )

    if db.query(KasBankAkun).filter(KasBankAkun.akun_perkiraan_id == coa.id).first():
        raise ValueError(
            "Akun ini terhubung ke Kas & Bank. Hapus/lepaskan koneksi Kas & "
            "Bank-nya dulu sebelum menghapus akun ini."
        )

    db.delete(coa)
    db.commit()


# ============================================================
# NEXT KODE (auto-generate kode akun detail di bawah induk)
# ============================================================

def get_next_kode(db: Session, induk_id: UUID) -> str:
    """Generate kode akun berikutnya di bawah induk_id.

    Reuse _generate_next_detail_kode dari coa_linkage_service.py (single
    source of truth — juga dipakai internal oleh auto_create_piutang_coa/
    auto_create_hutang_coa), supaya logic-nya nggak duplikat dan nggak bisa
    kebablasan beda di kemudian hari.
    """
    from app.services.coa_linkage_service import _generate_next_detail_kode

    parent = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == induk_id).first()
    if not parent:
        raise ValueError("Akun induk tidak ditemukan.")

    if parent.tingkat not in (TingkatAkun.HEADER, TingkatAkun.GROUP):
        raise ValueError(
            "Akun induk harus level HEADER atau GROUP, tidak bisa membuat "
            "sub-akun di bawah akun level DETAIL."
        )

    return _generate_next_detail_kode(db, parent)


# ============================================================
# SALDO AWAL
# ============================================================

def get_saldo_awal(db: Session) -> dict:
    """Ambil saldo awal yang sudah diset (satu nilai per akun).

    Jika sudah, return detail jurnal SALDO_AWAL yang aktif (bisa di-edit ulang).
    Jika belum, return list kosong dengan sudah_diset=False.

    Baris jurnal akun penampung "Selisih Saldo Awal" DIKECUALIKAN dari items
    (dikelola sistem); total_debit/total_kredit pun dihitung tanpa penampung
    sehingga `selisih` = nilai yang diserap penampung.
    """
    from app.services.coa_category import OPENING_BALANCE_DIFF_SYSTEM_TYPE
    from app.services.reporting_ledger import local_datetime
    existing = _active_opening_journals(db).order_by(JurnalUmum.created_at.desc(), JurnalUmum.id).first()

    if not existing:
        return {
            "sudah_diset": False,
            "tanggal": None,
            "items": [],
            "total_debit": Decimal("0"),
            "total_kredit": Decimal("0"),
            "selisih": Decimal("0"),
        }

    # Ambil detail jurnal (tanpa akun penampung)
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
        .filter(
            JurnalDetail.jurnal_umum_id == existing.id,
            or_(
                AkunPerkiraan.system_account_type.is_(None),
                AkunPerkiraan.system_account_type != OPENING_BALANCE_DIFF_SYSTEM_TYPE,
            ),
        )
        .order_by(AkunPerkiraan.kode)
        .all()
    )

    items = []
    total_debit = Decimal("0")
    total_kredit = Decimal("0")
    for d in details:
        debit = Decimal(str(d.debit))
        kredit = Decimal(str(d.kredit))
        items.append({
            "akun_perkiraan_id": d.akun_perkiraan_id,
            "kode_akun": d.kode,
            "nama_akun": d.nama,
            "saldo_normal": d.saldo_normal.value if hasattr(d.saldo_normal, "value") else str(d.saldo_normal),
            "debit": debit,
            "kredit": kredit,
            # Satu nilai bersih (sisi mengikuti saldo normal akun)
            "nilai": debit if debit > 0 else kredit,
        })
        total_debit += debit
        total_kredit += kredit

    return {
        "sudah_diset": True,
        "tanggal": local_datetime(existing.tanggal).date().isoformat() if existing.tanggal else None,
        "items": items,
        "total_debit": total_debit,
        "total_kredit": total_kredit,
        "selisih": total_debit - total_kredit,
    }


def _active_opening_journals(db):
    from sqlalchemy.orm import aliased
    from sqlalchemy import select
    reversal = aliased(JurnalUmum)
    reversed_ids = select(reversal.reversal_of_id).where(
        reversal.reversal_of_id.is_not(None), reversal.status == StatusJurnal.POSTED)
    return db.query(JurnalUmum).filter(JurnalUmum.ref_module == RefModule.SALDO_AWAL,
        JurnalUmum.status == StatusJurnal.POSTED, ~JurnalUmum.id.in_(reversed_ids))


def _get_or_create_penampung(db: Session) -> AkunPerkiraan:
    """Akun penampung selisih saldo awal ("Selisih Saldo Awal", Modal).

    Normalnya sudah dibuat oleh migrasi w5x6y7z8a9b0; fungsi ini fallback
    get-or-create bila migrasi belum dijalankan di environment tertentu.
    """
    from app.services.coa_category import OPENING_BALANCE_DIFF_SYSTEM_TYPE
    account = (
        db.query(AkunPerkiraan)
        .filter(AkunPerkiraan.system_account_type == OPENING_BALANCE_DIFF_SYSTEM_TYPE)
        .first()
    )
    if account:
        return account
    induk = db.query(AkunPerkiraan).filter(AkunPerkiraan.kode == "300000").first()
    # Cari kode 340000 yang masih bebas (fallback 340001, 340002, ...)
    kode = "340000"
    counter = 0
    while db.query(AkunPerkiraan.id).filter(AkunPerkiraan.kode == kode).first():
        counter += 1
        kode = f"34000{counter}"
    account = AkunPerkiraan(
        kode=kode,
        nama="Selisih Saldo Awal",
        header=HeaderCOA.MODAL,
        tingkat=TingkatAkun.DETAIL,
        induk_id=induk.id if induk else None,
        induk_kode=induk.kode if induk else None,
        saldo_normal=SaldoNormal.KREDIT,
        saldo=Decimal("0"),
        status="AKTIF",
        account_class="EQUITY",
        financial_statement="NERACA",
        report_group="EQUITY",
        system_account_type=OPENING_BALANCE_DIFF_SYSTEM_TYPE,
        allow_system_posting=True,
        allow_manual_posting=False,
        is_control_account=False,
        reconciliation_required=False,
        active=True,
    )
    db.add(account)
    db.flush()
    return account


from app.services.accounting_control import atomic_accounting_write


@atomic_accounting_write
def _replace_opening(db, entries, tanggal, user_id):
    """Keep original journals; replace their effective balance atomically.

    entries: baris jurnal saldo awal LENGKAP (item + penampung) yang sudah
    balance — bukan lagi pasangan akun lawan manual.
    """
    from app.services.posting_service import auto_posting_jurnal, reverse_journal, validate_entries
    if entries:
        validate_entries(db, entries)
    originals = _active_opening_journals(db).order_by(JurnalUmum.id).all()
    affected = {row.akun_perkiraan_id for journal in originals for row in journal.details}
    for journal in originals:
        reverse_journal(db, journal.id, user_id, "Penggantian saldo awal")
    for identifier in affected:
        account = db.get(AkunPerkiraan, identifier)
        account.saldo = Decimal("0")
        account.tanggal = tanggal
    journal = None
    if entries:
        journal = auto_posting_jurnal(db, RefModule.SALDO_AWAL, "SA-INIT", entries,
            keterangan="Saldo Awal Perusahaan", tanggal=tanggal, created_by=user_id)
        for entry in entries:
            account = db.get(AkunPerkiraan, entry.akun_perkiraan_id)
            account.saldo = (entry.debit-entry.kredit) * (1 if account.saldo_normal == SaldoNormal.DEBIT else -1)
            account.tanggal = tanggal
    return journal


def save_saldo_awal(db: Session, items: list, tanggal_str: str, user_id: UUID) -> dict:
    """Replace global opening balances (satu nilai per akun).

    Perubahan (catatan update):
    - Setiap item cukup SATU nilai (`nilai`); sisi debit/kredit otomatis
      mengikuti saldo normal akun (Kas dan Bank/Aset Lancar Lainnya/HPP/
      Beban/Beban Lainnya = DEBIT; Kewajiban Lainnya/Modal/Pendapatan/
      Pendapatan Lainnya = KREDIT).
    - Hanya akun pada 9 kategori yang didukung (lihat coa_category.py).
    - Jurnal di-balance otomatis oleh akun penampung "Selisih Saldo Awal"
      di Modal — user tidak lagi memilih akun lawan.
    """
    from app.services.coa_category import (
        OPENING_BALANCE_DIFF_SYSTEM_TYPE,
        classify_account,
        get_category_info,
        is_saldo_awal_eligible,
    )
    from app.services.posting_service import JurnalEntryItem
    from app.services.reporting_ledger import local_datetime

    tanggal = local_datetime(datetime.strptime(tanggal_str, "%Y-%m-%d"))

    seen = set()
    entries: list = []
    for item in items:
        identifier = item["akun_perkiraan_id"]
        if identifier in seen:
            raise ValueError("Akun saldo awal tidak boleh berulang")
        seen.add(identifier)
        nilai = Decimal(str(item.get("nilai") or 0))
        if not nilai.is_finite() or nilai < 0:
            raise ValueError("Saldo awal harus angka valid dan tidak negatif")
        if not nilai:
            continue  # nilai nol = akun tanpa saldo awal (hapus bila pernah ada)
        account = db.get(AkunPerkiraan, identifier)
        if account is None:
            raise ValueError("Akun perkiraan tidak ditemukan")
        if account.system_account_type == OPENING_BALANCE_DIFF_SYSTEM_TYPE:
            continue  # penampung dikelola sistem — abaikan dari input user
        # === Validasi kelayakan kategori (9 kategori didukung) ===
        if account.tingkat != TingkatAkun.DETAIL:
            raise ValueError(
                f"Akun {account.kode} ({account.nama}) bukan akun DETAIL — "
                "tidak bisa diberi saldo awal."
            )
        category = classify_account(account)
        if not is_saldo_awal_eligible(category):
            info = get_category_info(category) or {}
            hint = info.get("hint") or "Kategori akun ini tidak didukung saldo awal langsung."
            raise ValueError(
                f"Akun {account.kode} ({account.nama}) tidak didukung saldo awal: {hint}"
            )
        if account.saldo_normal == SaldoNormal.DEBIT:
            entries.append(JurnalEntryItem(identifier, nilai, Decimal("0"), "Saldo awal"))
        else:
            entries.append(JurnalEntryItem(identifier, Decimal("0"), nilai, "Saldo awal"))

    # === Auto-balance via akun penampung "Selisih Saldo Awal" (Modal) ===
    if entries:
        diff = sum(e.debit for e in entries) - sum(e.kredit for e in entries)
        if diff != 0:
            penampung = _get_or_create_penampung(db)
            if diff > 0:
                entries.append(JurnalEntryItem(
                    penampung.id, Decimal("0"), diff, "Penampung selisih saldo awal"))
            else:
                entries.append(JurnalEntryItem(
                    penampung.id, -diff, Decimal("0"), "Penampung selisih saldo awal"))

    _replace_opening(db, entries, tanggal, user_id)
    return get_saldo_awal(db)
