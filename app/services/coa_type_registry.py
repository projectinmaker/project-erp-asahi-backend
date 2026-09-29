"""Registry template tipe akun — revisi form Tambah/Edit Akun (COA).

Pola Accurate: pengguna memilih Tipe Akun (istilah bisnis), sistem menurunkan
klasifikasi laporan, kontrol subledger, dan aturan posting dari template
registry + akun induk. UI tidak menduplikasi klasifikasi di select statis.

Prinsip (dari spesifikasi revisi):
- Bedakan `type_code` (kategori UI) dari `system_account_type` (identitas
  mapping unik). Tidak semua tipe akun otomatis menjadi akun kontrol.
- Akun kontrol/sistem (AR/AP/Inventory control, Current Earnings, clearing,
  GRNI, dll.) hanya melalui mapping resmi — tidak dibuat dengan flag bebas
  dari payload user biasa.
- Parent inheritance: kelas akun dan laporan tidak boleh bertentangan dengan
  parent. Default deny bila mapping tidak valid/ambigu.
- Kode akun baru boleh manual atau generated; server tetap memvalidasi
  uniqueness (race-safe via unique constraint + retry).
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.akun_perkiraan import (
    AkunPerkiraan,
    HeaderCOA,
    SaldoNormal,
    TingkatAkun,
    HEADER_TO_ACCOUNT_CLASS,
)
from app.models.master.pengguna import Pengguna

# ==========================================
# Registry template tipe akun (user-facing)
# ==========================================

ACCOUNT_TYPE_TEMPLATES: dict = {
    'KAS_BANK': {
        'type_code': 'KAS_BANK',
        'display_name': 'Kas & Bank',
        'account_class': 'ASSET',
        'financial_statement': 'NERACA',
        'report_group': 'CURRENT_ASSET',
        'account_subclass': 'CASH_BANK',
        'saldo_normal': 'DEBIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': ['CASH_BANK'],
        'supports_root': True,
        'requires_jenis_kas_bank': True,
    },
    'PIUTANG_USAHA': {
        'type_code': 'PIUTANG_USAHA',
        'display_name': 'Piutang Usaha',
        'account_class': 'ASSET',
        'financial_statement': 'NERACA',
        'report_group': 'CURRENT_ASSET',
        'account_subclass': 'ACCOUNTS_RECEIVABLE',
        'saldo_normal': 'DEBIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': ['ACCOUNTS_RECEIVABLE'],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'PERSEDIAAN': {
        'type_code': 'PERSEDIAAN',
        'display_name': 'Persediaan',
        'account_class': 'ASSET',
        'financial_statement': 'NERACA',
        'report_group': 'CURRENT_ASSET',
        'account_subclass': None,
        'saldo_normal': 'DEBIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'ASET_LANCAR_LAINNYA': {
        'type_code': 'ASET_LANCAR_LAINNYA',
        'display_name': 'Aset Lancar Lainnya',
        'account_class': 'ASSET',
        'financial_statement': 'NERACA',
        'report_group': 'CURRENT_ASSET',
        'account_subclass': None,
        'saldo_normal': 'DEBIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'ASET_TETAP': {
        'type_code': 'ASET_TETAP',
        'display_name': 'Aset Tetap',
        'account_class': 'ASSET',
        'financial_statement': 'NERACA',
        'report_group': 'NON_CURRENT_ASSET',
        'account_subclass': 'FIXED_ASSET_COST',
        'saldo_normal': 'DEBIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': ['FIXED_ASSET_COST', 'CONTRA_ASSET'],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'HUTANG_USAHA': {
        'type_code': 'HUTANG_USAHA',
        'display_name': 'Hutang Usaha',
        'account_class': 'LIABILITY',
        'financial_statement': 'NERACA',
        'report_group': 'CURRENT_LIABILITY',
        'account_subclass': 'ACCOUNTS_PAYABLE',
        'saldo_normal': 'KREDIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': ['ACCOUNTS_PAYABLE'],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'KEWAJIBAN_LAINNYA': {
        'type_code': 'KEWAJIBAN_LAINNYA',
        'display_name': 'Kewajiban Lainnya',
        'account_class': 'LIABILITY',
        'financial_statement': 'NERACA',
        'report_group': 'CURRENT_LIABILITY',
        'account_subclass': None,
        'saldo_normal': 'KREDIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'MODAL': {
        'type_code': 'MODAL',
        'display_name': 'Modal',
        'account_class': 'EQUITY',
        'financial_statement': 'NERACA',
        'report_group': 'EQUITY',
        'account_subclass': None,
        'saldo_normal': 'KREDIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'PENDAPATAN': {
        'type_code': 'PENDAPATAN',
        'display_name': 'Pendapatan',
        'account_class': 'REVENUE',
        'financial_statement': 'LABA RUGI',
        'report_group': 'OPERATING_REVENUE',
        'account_subclass': 'OPERATING_REVENUE',
        'saldo_normal': 'KREDIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'HPP': {
        'type_code': 'HPP',
        'display_name': 'HPP',
        'account_class': 'COGS',
        'financial_statement': 'LABA RUGI',
        'report_group': 'COGS',
        'account_subclass': 'COGS',
        'saldo_normal': 'DEBIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'BEBAN': {
        'type_code': 'BEBAN',
        'display_name': 'Beban',
        'account_class': 'EXPENSE',
        'financial_statement': 'LABA RUGI',
        'report_group': 'GENERAL_ADMIN_EXPENSE',
        'account_subclass': 'OPERATING_EXPENSE',
        'saldo_normal': 'DEBIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'PENDAPATAN_LAINNYA': {
        'type_code': 'PENDAPATAN_LAINNYA',
        'display_name': 'Pendapatan Lainnya',
        'account_class': 'REVENUE',
        'financial_statement': 'LABA RUGI',
        'report_group': 'OTHER_INCOME',
        'account_subclass': 'OTHER_REVENUE',
        'saldo_normal': 'KREDIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
    'BEBAN_LAINNYA': {
        'type_code': 'BEBAN_LAINNYA',
        'display_name': 'Beban Lainnya',
        'account_class': 'EXPENSE',
        'financial_statement': 'LABA RUGI',
        'report_group': 'OTHER_EXPENSE',
        'account_subclass': None,
        'saldo_normal': 'DEBIT',
        'default_posting_level': 'DETAIL',
        'default_system_posting': True,
        'default_manual_posting': True,
        'default_control': False,
        'default_reconciliation': False,
        'subledger_type': None,
        'is_system_reserved': False,
        'allowed_parent_subclasses': [],
        'supports_root': True,
        'requires_jenis_kas_bank': False,
    },
}

# Profiler akun kontrol resmi — HANYA via mapping/permission khusus,
# tidak pernah default dari form user biasa.
CONTROL_PROFILES: dict = {
    'AR_CONTROL': {
        'display_name': 'Kontrol Piutang Usaha (AR)',
        'account_class': 'ASSET', 'saldo_normal': 'DEBIT',
        'allow_system_posting': True, 'allow_manual_posting': False,
        'is_control_account': True, 'reconciliation_required': True,
        'subledger_type': 'AR',
    },
    'AP_CONTROL': {
        'display_name': 'Kontrol Hutang Usaha (AP)',
        'account_class': 'LIABILITY', 'saldo_normal': 'KREDIT',
        'allow_system_posting': True, 'allow_manual_posting': False,
        'is_control_account': True, 'reconciliation_required': True,
        'subledger_type': 'AP',
    },
    'INVENTORY_CONTROL': {
        'display_name': 'Kontrol Persediaan',
        'account_class': 'ASSET', 'saldo_normal': 'DEBIT',
        'allow_system_posting': True, 'allow_manual_posting': False,
        'is_control_account': True, 'reconciliation_required': True,
        'subledger_type': 'INVENTORY',
    },
}

# system_account_type yang reserved — akun dengan tipe ini tidak boleh
# dibuat/diubah dari form biasa tanpa permission khusus.
SYSTEM_RESERVED_ACCOUNT_TYPES = {
    'AR_CONTROL', 'AP_CONTROL', 'BANK_CLEARING', 'CURRENT_EARNINGS',
    'RETAINED_EARNINGS', 'COGS_FINISHED_GOODS', 'INVENTORY_RAW',
    'INVENTORY_AUX', 'INVENTORY_WIP', 'INVENTORY_FINISHED',
    'LEGACY_COGS_PURCHASE', 'DIVIDEND', 'CASH_BANK', 'FIXED_ASSET',
    'ACCUM_DEPR', 'DEPRECIATION_EXPENSE', 'VAT_INPUT', 'VAT_OUTPUT',
    'SALES', 'OTHER_INCOME', 'OTHER_EXPENSE', 'SELLING_EXPENSE',
    'ADMIN_EXPENSE', 'FACTORY_OVERHEAD', 'INCOME_TAX',
    'CORPORATE_INCOME_TAX', 'OTHER_AR',
}

_CLASS_TO_HEADER = {v: k for k, v in HEADER_TO_ACCOUNT_CLASS.items()}
_HEADER_TO_SALDO = {
    HeaderCOA.AKTIVA: SaldoNormal.DEBIT,
    HeaderCOA.KEWAJIBAN: SaldoNormal.KREDIT,
    HeaderCOA.MODAL: SaldoNormal.KREDIT,
    HeaderCOA.PENDAPATAN: SaldoNormal.KREDIT,
    HeaderCOA.HPP: SaldoNormal.DEBIT,
    HeaderCOA.BEBAN: SaldoNormal.DEBIT,
}

PRIVILEGED_ROLES = {'ADMINISTRATOR', 'MANAJER_KEUANGAN'}


def is_privileged_user(user: Pengguna) -> bool:
    """True kalau user boleh mengubah flag control/system/mapping akun.

    Admin Finance (MANAJER_KEUANGAN) & Administrator. Staff tidak boleh —
    backend enforce, bukan cuma hide tab (COA-03/COA-14).
    """
    role = getattr(getattr(user, 'role', None), 'value', None) or str(getattr(user, 'role', ''))
    return role in PRIVILEGED_ROLES


def list_templates() -> List[dict]:
    """Daftar template tipe akun yang valid untuk dropdown form."""
    return list(ACCOUNT_TYPE_TEMPLATES.values())


def get_template(type_code: str) -> dict:
    template = ACCOUNT_TYPE_TEMPLATES.get((type_code or '').strip().upper())
    if template is None:
        raise ValueError(f"Tipe akun '{type_code}' tidak dikenal. Gunakan GET /coa/account-types untuk daftar yang valid.")
    return template


def _account_class_of(account: AkunPerkiraan) -> Optional[str]:
    if account.account_class:
        return account.account_class.upper()
    if account.header is not None:
        return HEADER_TO_ACCOUNT_CLASS.get(account.header)
    return None


def _is_descendant(db: Session, ancestor_id: UUID, candidate_id: UUID) -> bool:
    """True kalau candidate berada di bawah ancestor (atau sama)."""
    current_id = candidate_id
    for _ in range(50):  # guard siklus
        if current_id is None:
            return False
        if current_id == ancestor_id:
            return True
        row = db.query(AkunPerkiraan.induk_id).filter(AkunPerkiraan.id == current_id).first()
        current_id = row[0] if row else None
    return False


def eligible_parents(db: Session, type_code: str, exclude_id: Optional[UUID] = None,
                     search: Optional[str] = None, limit: int = 200) -> List[dict]:
    """Parent yang eligible untuk template tipe akun.

    Syarat: aktif, tingkat HEADER/GROUP, kelas akun kompatibel dengan template,
    bukan diri sendiri / descendant (untuk edit). Parent ditandai
    `recommended` bila subclass-nya sendiri ATAU salah satu anak langsungnya
    punya subclass yang cocok dengan `allowed_parent_subclasses` template
    (mis. tipe Kas & Bank → grup yang berisi detail CASH_BANK), lalu
    diurutkan lebih dulu.
    """
    template = get_template(type_code)
    query = db.query(AkunPerkiraan).filter(
        AkunPerkiraan.active.is_(True),
        AkunPerkiraan.tingkat.in_([TingkatAkun.HEADER, TingkatAkun.GROUP]),
        AkunPerkiraan.is_subledger.is_(False),
    )
    if exclude_id is not None:
        query = query.filter(AkunPerkiraan.id != exclude_id)
    if search:
        pattern = f"%{search}%"
        from sqlalchemy import or_
        query = query.filter(or_(AkunPerkiraan.kode.ilike(pattern), AkunPerkiraan.nama.ilike(pattern)))

    # Grup yang direkomendasikan: subclass grup sendiri atau subclass anak
    # langsungnya cocok dengan allowed_parent_subclasses template.
    preferred_ids = set()
    if template['allowed_parent_subclasses']:
        rows = db.query(
            AkunPerkiraan.id, AkunPerkiraan.induk_id, AkunPerkiraan.account_subclass
        ).filter(AkunPerkiraan.account_subclass.in_(
            template['allowed_parent_subclasses'])).all()
        for own_id, induk_id, subclass in rows:
            preferred_ids.add(own_id)
            if induk_id is not None:
                preferred_ids.add(induk_id)

    result = []
    for parent in query.order_by(AkunPerkiraan.kode).limit(limit).all():
        if exclude_id is not None and _is_descendant(db, exclude_id, parent.id):
            continue  # cegah parent siklik saat edit
        parent_class = _account_class_of(parent)
        if parent_class != template['account_class']:
            continue
        result.append({
            'id': parent.id,
            'kode': parent.kode,
            'nama': parent.nama,
            'tingkat': parent.tingkat.value,
            'account_class': parent_class,
            'account_subclass': parent.account_subclass,
            'recommended': parent.id in preferred_ids,
        })
    result.sort(key=lambda p: (not p['recommended'], p['kode']))
    return result


def _generate_next_kode(db: Session, parent: AkunPerkiraan) -> str:
    """Generate kode berikutnya di bawah parent (reuse linkage service)."""
    from app.services.coa_linkage_service import _generate_next_detail_kode
    return _generate_next_detail_kode(db, parent)


def resolve_account_defaults(db: Session, type_code: str, parent_id: Optional[UUID] = None,
                             structural_type: Optional[str] = None) -> dict:
    """Turunkan seluruh field akun dari template + parent (tanpa menyimpan).

    Return dict berisi field legacy (header, tingkat, saldo_normal) + field
    baru (account_class, financial_statement, report_group, dsb).
    """
    template = get_template(type_code)
    header = _CLASS_TO_HEADER[template['account_class']]

    if parent_id is not None:
        parent = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == parent_id).first()
        if parent is None:
            raise ValueError('Akun induk tidak ditemukan.')
        if parent.tingkat not in (TingkatAkun.HEADER, TingkatAkun.GROUP):
            raise ValueError('Akun induk harus level HEADER atau GROUP — akun DETAIL tidak boleh punya child.')
        if not parent.active:
            raise ValueError('Akun induk harus berstatus aktif.')
        parent_class = _account_class_of(parent)
        if parent_class != template['account_class']:
            raise ValueError(
                f"Kelas akun induk ({parent_class or '?'}) tidak kompatibel dengan tipe "
                f"{template['display_name']} ({template['account_class']})."
            )
        tingkat = TingkatAkun.DETAIL
    else:
        parent = None
        struct = (structural_type or 'GROUP').strip().upper()
        if struct not in ('GROUP', 'DETAIL'):
            raise ValueError("Tipe struktur harus 'GROUP' (Grup Akun) atau 'DETAIL' (Akun Transaksi).")
        tingkat = TingkatAkun.GROUP if struct == 'GROUP' else TingkatAkun.DETAIL

    defaults = {
        'type_code': template['type_code'],
        'header': header,
        'tingkat': tingkat,
        'saldo_normal': _HEADER_TO_SALDO[header],
        'account_class': template['account_class'],
        'account_subclass': template['account_subclass'],
        'financial_statement': template['financial_statement'],
        'report_group': template['report_group'],
        'allow_system_posting': template['default_system_posting'],
        'allow_manual_posting': template['default_manual_posting'],
        'is_control_account': template['default_control'],
        'subledger_type': template['subledger_type'],
        'system_account_type': None,
        'reconciliation_required': template['default_reconciliation'],
        'active': True,
        'parent': parent,
    }

    # Grup akun tidak boleh dipakai posting (COA-04); akun transaksi root
    # level DETAIL tetap boleh posting sesuai template.
    if tingkat != TingkatAkun.DETAIL:
        defaults['allow_system_posting'] = False
        defaults['allow_manual_posting'] = False
    return defaults


def preview_account(db: Session, type_code: str, is_sub: bool = True,
                    parent_id: Optional[UUID] = None, structural_type: Optional[str] = None,
                    kode: Optional[str] = None, nama: Optional[str] = None,
                    jenis_kas_bank: Optional[str] = None) -> dict:
    """Preview akun baru: kode usulan + klasifikasi turunan + warnings/errors.

    Tidak menyimpan apa pun ke DB. `errors` berisi masalah blocking —
    frontend menonaktifkan tombol Simpan; `warnings` informatif saja.
    """
    template = get_template(type_code)
    warnings: List[str] = []
    errors: List[str] = []

    if not is_sub:
        parent_id = None

    try:
        defaults = resolve_account_defaults(db, type_code, parent_id, structural_type)
    except ValueError as exc:
        defaults = None
        errors.append(str(exc))

    parent = defaults.get('parent') if defaults else None
    kode_generated = False
    proposed_kode = None
    if kode:
        proposed_kode = kode.strip()
        if db.query(AkunPerkiraan.id).filter(AkunPerkiraan.kode == proposed_kode).first():
            errors.append(f"Kode akun '{proposed_kode}' sudah terdaftar.")
    elif parent is not None:
        try:
            proposed_kode = _generate_next_kode(db, parent)
            kode_generated = True
        except ValueError as exc:
            errors.append(str(exc))
    else:
        warnings.append('Akun level root: kode akun harus diisi manual.')

    if jenis_kas_bank and jenis_kas_bank not in ('KAS', 'BANK'):
        errors.append("jenisKasBank harus 'KAS' atau 'BANK'.")
    if template['requires_jenis_kas_bank'] and parent is not None and not jenis_kas_bank:
        warnings.append('Tipe Kas & Bank sebaiknya memilih jenis (KAS/BANK) agar terhubung ke modul Kas & Bank.')

    result = {
        'type_code': template['type_code'],
        'display_name': template['display_name'],
        'kode': proposed_kode,
        'kode_generated': kode_generated,
        'nama': (nama or '').strip() or None,
        'parent_id': parent.id if parent else None,
        'parent_kode': parent.kode if parent else None,
        'parent_nama': parent.nama if parent else None,
        'warnings': warnings,
        'errors': errors,
    }
    if defaults:
        result.update({
            'tingkat': defaults['tingkat'].value,
            'header': defaults['header'].value,
            'saldo_normal': defaults['saldo_normal'].value,
            'account_class': defaults['account_class'],
            'account_subclass': defaults['account_subclass'],
            'financial_statement': defaults['financial_statement'],
            'report_group': defaults['report_group'],
            'allow_system_posting': defaults['allow_system_posting'],
            'allow_manual_posting': defaults['allow_manual_posting'],
            'is_control_account': defaults['is_control_account'],
            'subledger_type': defaults['subledger_type'],
            'system_account_type': defaults['system_account_type'],
            'reconciliation_required': defaults['reconciliation_required'],
            'active': defaults['active'],
            'jenis_kas_bank': jenis_kas_bank if jenis_kas_bank in ('KAS', 'BANK') else None,
        })
    return result


def _control_flags_requested(payload: dict) -> bool:
    """True kalau payload meminta flag control/system/subledger."""
    if payload.get('is_control_account'):
        return True
    if payload.get('system_account_type'):
        return True
    if payload.get('subledger_type'):
        return True
    return False


def validate_control_flags(payload: dict, user: Pengguna) -> List[str]:
    """Validasi flag control/system — tolak untuk user biasa (COA-03).

    Return list warning; raise ValueError bila user tidak berwenang.
    """
    if _control_flags_requested(payload) and not is_privileged_user(user):
        raise ValueError(
            'Flag akun kontrol / tipe sistem / subledger hanya boleh diatur '
            'oleh Admin Finance (MANAJER_KEUANGAN) atau Administrator melalui '
            'flow Ubah Mapping Akuntansi.'
        )
    system_type = payload.get('system_account_type')
    if system_type and system_type not in SYSTEM_RESERVED_ACCOUNT_TYPES:
        raise ValueError(f"Tipe akun sistem '{system_type}' tidak dikenal dalam registry.")
    return []


def validate_coa_configuration(db: Session, account: AkunPerkiraan, changes: dict, user: Pengguna) -> List[str]:
    """Validasi perubahan akun existing (update/PUT).

    Aturan (spec §7):
    - Akun DETAIL dengan posted journal → kode/kelas/parent/tipe kontrol
      TIDAK boleh diubah (butuh migration terotorisasi).
    - Akun control → toggle manual posting bebas ditolak; perubahan mapping
      lewat prosedur khusus.
    - Parent baru tidak boleh siklik / beda kelas / bukan HEADER-GROUP.
    - Flag control/system/subledger hanya untuk privileged user.

    Return list warning untuk response; raise ValueError bila blocking.
    """
    warnings: List[str] = []
    from app.models.detail.jurnal_detail import JurnalDetail
    used_in_journal = db.query(JurnalDetail.id).filter(
        JurnalDetail.akun_perkiraan_id == account.id).first() is not None

    structural_fields = {
        'induk_id', 'account_class', 'account_subclass', 'financial_statement',
        'report_group', 'system_account_type', 'is_control_account', 'subledger_type',
    }
    if used_in_journal:
        attempted = {f for f in structural_fields if f in changes and changes[f] is not None
                     and getattr(account, f, None) != changes[f]}
        if attempted:
            raise ValueError(
                'Akun sudah memiliki jurnal terposting; kelas/parent/tipe kontrol tidak '
                'dapat diubah langsung. Gunakan prosedur mapping terotorisasi untuk '
                'reklasifikasi (export COA, approval Finance, lalu migration terencana).'
            )
        warnings.append('Akun sudah memiliki jurnal — perubahan terbatas pada nama/status/aturan posting.')

    # Flag control/system → privileged saja (termasuk saat akun belum terpakai)
    validate_control_flags(changes, user)

    # Parent baru: validasi siklik, tingkat, kelas
    new_parent_id = changes.get('induk_id', None)
    if 'induk_id' in changes and new_parent_id is not None:
        if new_parent_id == account.id:
            raise ValueError('Akun tidak boleh menjadi induk dirinya sendiri.')
        if _is_descendant(db, account.id, new_parent_id):
            raise ValueError('Induk baru tidak boleh berada di bawah akun ini (parent siklik).')
        parent = db.query(AkunPerkiraan).filter(AkunPerkiraan.id == new_parent_id).first()
        if parent is None:
            raise ValueError('Akun induk baru tidak ditemukan.')
        if parent.tingkat not in (TingkatAkun.HEADER, TingkatAkun.GROUP):
            raise ValueError('Akun induk harus level HEADER atau GROUP.')
        target_class = changes.get('account_class') or account.account_class or _account_class_of(account)
        parent_class = _account_class_of(parent)
        if target_class and parent_class and target_class != parent_class:
            raise ValueError(
                f"Kelas akun induk ({parent_class}) tidak kompatibel dengan kelas akun ini ({target_class})."
            )

    # Control account: manual posting tidak boleh dinyalakan bebas (COA-08 guard)
    if changes.get('allow_manual_posting') and (
            changes.get('is_control_account') if 'is_control_account' in changes else account.is_control_account):
        warnings.append('Akun kontrol dengan posting manual aktif — pastikan ini memang kebijakan Finance.')

    return warnings


def build_create_payload(db: Session, payload: dict, user: Pengguna) -> dict:
    """Bangun payload create lengkap dari input minimal + template registry.

    Input (dari COACreate dengan type_code): type_code, is_sub, parent_id
    (induk_id), structural_type, kode, nama, status, jenis_kas_bank, saldo,
    tanggal. Field klasifikasi diturunkan server; payload control/system
    dari user biasa ditolak.

    Raise ValueError bila ada masalah blocking. Return dict siap dipakai
    create_coa (field legacy + new lengkap).
    """
    type_code = payload.get('type_code')
    is_sub = payload.get('is_sub')
    if is_sub is None:
        is_sub = payload.get('induk_id') is not None
    parent_id = payload.get('induk_id')

    defaults = resolve_account_defaults(db, type_code, parent_id, payload.get('structural_type'))
    parent = defaults.pop('parent')

    kode = (payload.get('kode') or '').strip()
    if not kode and parent is not None:
        kode = _generate_next_kode(db, parent)
    if not kode:
        raise ValueError('Kode akun wajib diisi (akun level root tidak punya kode otomatis).')
    if db.query(AkunPerkiraan.id).filter(AkunPerkiraan.kode == kode).first():
        raise ValueError(f"Kode akun '{kode}' sudah terdaftar.")

    result = dict(payload)
    result.pop('type_code', None)
    result.pop('is_sub', None)
    result.pop('structural_type', None)
    # Field turunan registry menimpa payload user (server-side truth)
    result.update({
        'kode': kode,
        'induk_id': parent.id if parent else None,
        'induk_kode': parent.kode if parent else None,
        'header': defaults['header'],
        'tingkat': defaults['tingkat'],
        'saldo_normal': defaults['saldo_normal'],
        'account_class': defaults['account_class'],
        'account_subclass': defaults['account_subclass'],
        'financial_statement': defaults['financial_statement'],
        'report_group': defaults['report_group'],
        'allow_system_posting': defaults['allow_system_posting'],
        'allow_manual_posting': defaults['allow_manual_posting'],
        'is_control_account': defaults['is_control_account'],
        'subledger_type': defaults['subledger_type'],
        'system_account_type': defaults['system_account_type'],
        'reconciliation_required': defaults['reconciliation_required'],
    })

    # Payload control/system dari user biasa → tolak (COA-03 / COA-20)
    validate_control_flags(result, user)
    return result
