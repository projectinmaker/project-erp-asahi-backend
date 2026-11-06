"""=== Update #044 — Voucher Memorial service (Gagal Tagih / penghapusan piutang) ===

Alur Penerimaan Piutang modul Kas & Bank (revisi pemilik, poin 5):

    5. Penerimaan Piutang
       ├─ Penerimaan Penuh     ──→ Voucher Penerimaan Bank / Kas  (PenerimaanKas)
       ├─ Penerimaan Sebagian  ──→ Voucher Penerimaan Bank / Kas  (PenerimaanKas)
       └─ Gagal Tagih          ──→ Voucher Memorial (Adjustment)  (dokumen ini)

Cabang Gagal Tagih TIDAK menyetor kas: sisa tagihan invoice dihapusbukukan.
Dokumen bernomor "VM ASI/YYYY/NNN" (prefix bisa di-override lewat Preferensi
→ Penomoran, key PREFIX_VOUCHER_MEMORIAL). Posting menghasilkan jurnal
SISTEM (is_manual=False — akun kontrol piutang memang terlarang di jurnal
manual pengguna):

    Dr  Beban Penghapusan Piutang   (akun_beban_id / Setting Akun)
    Cr  Piutang Usaha (kontrol)     (per detail, dikelompokkan per akun)

Sisa tagihan invoice dihitung ulang settlement_service.balances dengan
mengurangkan penghapusan aktif — invoice yang dihapusbukukan penuh berstatus
LUNAS tanpa pernah menerima kas.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun
from app.models.master.pelanggan import Pelanggan
from app.models.transaksi.jurnal import RefModule
from app.models.transaksi.kas_bank.pembayaran import StatusTransaksi
from app.models.transaksi.kas_bank.voucher_memorial import (
    VoucherMemorial, VoucherMemorialDetail,
)
from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
from app.services.accounting_control import atomic_accounting_write
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.services.setting_akun_service import (
    get_akun_id_or_raise,
    KEY_BEBAN_PENGHAPUSAN_PIUTANG,
)
from app.utils.nomor_dokumen import get_nomor_dokumen_tahunan, resolve_prefix

ZERO = Decimal("0.00")

# Validasi akun beban (paritas kaidah akun penalti settlement): wajib ADA,
# AKTIF, leaf (DETAIL tanpa anak), dan BUKAN akun kas/bank maupun kontrol/
# subledger piutang-hutang — beban penghapusan harus akun riil di sisi Debit.
BEBAN_FORBIDDEN_SUBCLASSES = {"CASH_BANK", "ACCOUNTS_RECEIVABLE", "ACCOUNTS_PAYABLE"}
BEBAN_FORBIDDEN_SYSTEM_TYPES = {"AR_CONTROL", "AP_CONTROL", "BANK_CLEARING"}
BEBAN_FORBIDDEN_SUBLEDGERS = {"AR", "AP"}


def _prefix_nomor_voucher_memorial(db: Session) -> str:
    """Prefix nomor Voucher Memorial — "VM" (dapat di-override Preferensi)."""
    return resolve_prefix(db, "VOUCHER_MEMORIAL", "VM")


def peek_nomor_voucher_memorial(db: Session, tanggal: Optional[date] = None) -> str:
    """Preview nomor Voucher Memorial BERIKUTNYA (indikatif, tanpa create)."""
    tgl = tanggal or date.today()
    return get_nomor_dokumen_tahunan(
        db, VoucherMemorial, prefix=_prefix_nomor_voucher_memorial(db),
        no_column="no_bukti", tanggal=tgl,
    )


def validate_beban_account(db: Session, akun_beban_id) -> AkunPerkiraan:
    """Akun beban penghapusan piutang — kaidah sama dengan akun penalti
    settlement (AKTIF, leaf, bukan kas/bank/AR/AP)."""
    if not akun_beban_id:
        raise ValueError("Akun beban penghapusan piutang wajib dipilih")
    akun = db.get(AkunPerkiraan, akun_beban_id)
    if akun is None:
        raise ValueError("Akun beban penghapusan piutang tidak ditemukan")
    if akun.status != "AKTIF" or akun.active is not True:
        raise ValueError("Akun beban penghapusan piutang harus berstatus AKTIF")
    has_child = db.query(AkunPerkiraan.id).filter(AkunPerkiraan.induk_id == akun.id).first() is not None
    if has_child or akun.tingkat is None or akun.tingkat != TingkatAkun.DETAIL:
        raise ValueError("Akun beban penghapusan piutang harus akun leaf (DETAIL tanpa akun anak)")
    subclass = (akun.account_subclass or "").upper()
    system_type = (akun.system_account_type or "").upper()
    subledger = (akun.subledger_type or "").upper()
    if (subclass in BEBAN_FORBIDDEN_SUBCLASSES
            or system_type in BEBAN_FORBIDDEN_SYSTEM_TYPES
            or subledger in BEBAN_FORBIDDEN_SUBLEDGERS
            or akun.is_subledger):
        raise ValueError("Akun beban tidak boleh akun kas/bank atau kontrol piutang/hutang")
    return akun


def _prepare_writeoff(db: Session, pihak_id, tanggal, allocation_data):
    """Bangun baris penghapusan per invoice (mirror settlement_service.prepare).

    Validasi: pelanggan AKTIF, 1-100 alokasi, invoice milik pelanggan yang
    sama, POSTED/aktif, IDR, tanggal >= tanggal invoice, 0 < nilai <= sisa.
    Akun piutang kontrol dibekukan per invoice (control_account settlement).
    """
    from app.services import settlement_service as settlement

    party = db.get(Pelanggan, pihak_id)
    if party is None or party.status != "AKTIF":
        raise ValueError("Pelanggan tidak tersedia atau tidak aktif")
    if not allocation_data or len(allocation_data) > 100:
        raise ValueError("Isi 1 sampai 100 alokasi invoice")
    ids = [r["invoice_id"] for r in allocation_data]
    if len(ids) != len(set(ids)):
        raise ValueError("Invoice tidak boleh berulang dalam satu Voucher Memorial")
    invoices = (
        db.query(SalesInvoice).filter(SalesInvoice.id.in_(ids))
        .order_by(SalesInvoice.id).populate_existing().with_for_update().all()
    )
    by_id = {obj.id: obj for obj in invoices}
    if len(invoices) != len(ids):
        raise ValueError("Invoice alokasi tidak ditemukan")
    current = settlement.balances(db, invoices)
    rows = []
    for data in allocation_data:
        obj = by_id[data["invoice_id"]]
        if obj.pelanggan_id != pihak_id:
            raise ValueError("Semua invoice harus milik pelanggan yang dipilih")
        if not current[obj.id]["aktif"]:
            raise ValueError("Penghapusan hanya boleh untuk invoice POSTED yang belum dibatalkan")
        if getattr(obj, "mata_uang", "IDR") != "IDR":
            raise ValueError("Penghapusan saat ini hanya mendukung invoice IDR")
        if settlement.local_day(tanggal) < settlement.local_day(obj.tanggal):
            raise ValueError("Tanggal penghapusan tidak boleh sebelum tanggal invoice")
        nilai = settlement.positive_money(data["nilai"])
        sisa_now = current[obj.id]["sisa_tagihan"]
        if nilai > sisa_now:
            raise ValueError(f"Nilai penghapusan melebihi sisa tagihan {current[obj.id]['no_dokumen']}")
        account_id = settlement.control_account(db, obj)
        rows.append({
            "invoice_id": obj.id,
            "no_invoice": obj.no_invoice,
            "nilai": nilai,
            "terutang": current[obj.id]["sisa_tagihan"],
            "akun_piutang_id": account_id,
        })
    return rows


@atomic_accounting_write
def create_gagal_tagih(
    db: Session,
    pihak_id: UUID,
    tanggal: datetime,
    allocation_data: list,
    created_by: UUID,
    catatan: Optional[str] = None,
    akun_beban_id: Optional[UUID] = None,
) -> VoucherMemorial:
    """Buat Voucher Memorial (Gagal Tagih) + rincian per invoice (DRAFT).

    Posting jurnal TIDAK dilakukan di sini — endpoint memanggil
    workflow_service.direct_complete('voucher_memorial', ...) sehingga
    Administrator langsung final, role lain lewat maker-checker.
    """
    try:
        rows = _prepare_writeoff(db, pihak_id, tanggal, allocation_data)
        # Akun beban: dipilih user, atau default Setting Akun (auto-configure
        # via kandidat fallback COA bila belum ada).
        if akun_beban_id is not None:
            validate_beban_account(db, akun_beban_id)
        else:
            akun_beban_id = get_akun_id_or_raise(
                db, KEY_BEBAN_PENGHAPUSAN_PIUTANG, "Gagal Tagih (Voucher Memorial)")

        no_bukti = get_nomor_dokumen_tahunan(
            db, VoucherMemorial, prefix=_prefix_nomor_voucher_memorial(db),
            no_column="no_bukti", tanggal=tanggal.date() if isinstance(tanggal, datetime) else tanggal,
        )
        total_nilai = sum((r["nilai"] for r in rows), ZERO)

        voucher = VoucherMemorial(
            no_bukti=no_bukti,
            tanggal=tanggal,
            pelanggan_id=pihak_id,
            akun_beban_id=akun_beban_id,
            catatan=catatan,
            total_nilai=total_nilai,
            status=StatusTransaksi.DRAFT,
            created_by=created_by,
        )
        db.add(voucher)
        db.flush()

        for r in rows:
            db.add(VoucherMemorialDetail(
                voucher_id=voucher.id,
                sales_invoice_id=r["invoice_id"],
                akun_piutang_id=r["akun_piutang_id"],
                nilai=r["nilai"],
                terutang=r["terutang"],
            ))

        db.commit()
        db.refresh(voucher)
        logger.info(f"VoucherMemorial created: {no_bukti} | total={total_nilai}")
        return voucher

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating VoucherMemorial: {e}")
        raise


def get_voucher_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[VoucherMemorial], int]:
    """Daftar Voucher Memorial dengan filter & pagination."""
    query = db.query(VoucherMemorial).options(
        joinedload(VoucherMemorial.pelanggan),
        joinedload(VoucherMemorial.akun_beban),
        joinedload(VoucherMemorial.creator),
        joinedload(VoucherMemorial.jurnal),
        joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.sales_invoice),
        joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.akun_piutang),
    )
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            VoucherMemorial.no_bukti.ilike(pattern)
            | VoucherMemorial.catatan.ilike(pattern)
        )
    if status:
        query = query.filter(VoucherMemorial.status == status)
    if pelanggan_id:
        query = query.filter(VoucherMemorial.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(VoucherMemorial.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(VoucherMemorial.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(VoucherMemorial.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_voucher_by_id(db: Session, voucher_id: UUID) -> Optional[VoucherMemorial]:
    """Ambil 1 Voucher Memorial berdasarkan ID dengan rincian."""
    return (
        db.query(VoucherMemorial)
        .options(
            joinedload(VoucherMemorial.pelanggan),
            joinedload(VoucherMemorial.akun_beban),
            joinedload(VoucherMemorial.creator),
            joinedload(VoucherMemorial.jurnal),
            joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.sales_invoice),
            joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.akun_piutang),
        )
        .filter(VoucherMemorial.id == voucher_id)
        .first()
    )


def post_voucher_memorial(db: Session, voucher: VoucherMemorial, created_by: UUID):
    """Posting jurnal penghapusan piutang — pemanggil memiliki commit/rollback.

    Jurnal sistem (is_manual=False — akun kontrol piutang hanya boleh
    tersentuh alur sistem, bukan jurnal manual pengguna):

        Dr  Beban Penghapusan Piutang   (total)
        Cr  Piutang Usaha (kontrol)     (per invoice, dikelompokkan per akun)

    RefModule.MANUAL + tipe PENGHAPUSAN_PIUTANG (tanpa nilai enum baru —
    guard duplikat (ref_module, ref_id, tipe_transaksi) tetap efektif).
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, voucher)

    # Validasi ulang akun beban + nilai vs sisa terkini (defense in depth,
    # mirror post_penerimaan → validate_payment).
    validate_beban_account(db, voucher.akun_beban_id)
    _revalidate_rows(db, voucher)

    tanggal = voucher.tanggal
    no_bukti = voucher.no_bukti
    total_nilai = Decimal(str(voucher.total_nilai or 0))

    entries = [
        JurnalEntryItem(
            akun_perkiraan_id=voucher.akun_beban_id,
            debit=total_nilai,
            keterangan=f"Beban penghapusan piutang {no_bukti}",
        ),
    ]
    # Kredit: akun piutang kontrol per invoice (dikelompokkan).
    grouped: dict = {}
    for r in voucher.rincian:
        grouped[r.akun_piutang_id] = grouped.get(r.akun_piutang_id, ZERO) + Decimal(str(r.nilai))
    for akun_id, nilai in grouped.items():
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=akun_id,
                kredit=Decimal(str(nilai)),
                keterangan=f"Penghapusan piutang (gagal tagih) {no_bukti}",
            )
        )

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.MANUAL,
        ref_no=no_bukti,
        entries=entries,
        keterangan=f"Voucher Memorial — Penghapusan Piutang (Gagal Tagih) {no_bukti}",
        ref_id=voucher.id,
        tanggal=tanggal,
        created_by=created_by,
        tipe_transaksi="PENGHAPUSAN_PIUTANG",
    )
    voucher.jurnal_umum_id = jurnal.id
    voucher.status = StatusTransaksi.SELESAI
    return voucher


def _revalidate_rows(db: Session, voucher: VoucherMemorial) -> None:
    """Validasi ulang saat posting: nilai penghapusan masih <= sisa tagihan
    terkini (bisa berubah bila ada penerimaan lain setelah dokumen dibuat)."""
    from app.services import settlement_service as settlement
    if not voucher.rincian:
        raise ValueError("Voucher Memorial tanpa rincian invoice tidak dapat diposting")
    invoices = db.query(SalesInvoice).filter(
        SalesInvoice.id.in_([r.sales_invoice_id for r in voucher.rincian])
    ).all()
    current = settlement.balances(db, invoices)
    for r in voucher.rincian:
        sisa_now = current[r.sales_invoice_id]["sisa_tagihan"]
        if Decimal(str(r.nilai)) > sisa_now + Decimal("0.005"):
            raise ValueError(
                f"Nilai penghapusan {current[r.sales_invoice_id]['no_dokumen']} melebihi sisa "
                f"tagihan terkini {sisa_now} — mungkin sudah ada penerimaan lain. Periksa ulang dokumen."
            )
