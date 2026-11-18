"""=== Update #044 — Voucher Memorial service (Gagal Tagih / penghapusan piutang) ===

Alur Penerimaan Piutang modul Kas & Bank (revisi pemilik, poin 5):

    5. Penerimaan Piutang
       ├─ Penerimaan Penuh     ──→ Voucher Penerimaan Bank/Kas  (PenerimaanKas)
       ├─ Penerimaan Sebagian  ──→ Voucher Penerimaan Bank/Kas  (PenerimaanKas)
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

=== Update #072 — Voucher Memorial HUTANG (fitur Outstanding Memorial) ===

Gagal Tagih kini berlaku juga untuk HUTANG (jenis='HUTANG'): sisa hutang
invoice pembelian dihapusbukukan non-kas atas nama supplier.

    Dr  Hutang Usaha (kontrol)      (per detail, dikelompokkan per akun)
    Cr  Pendapatan Penghapusan Hutang  (akun_beban_id / Setting Akun
        PENDAPATAN_PENGHAPUSAN_HUTANG — fallback 421004 Pendapatan Lain-lain)

Sisa hutang di settlement_service.balances ikut dipotong penghapusan aktif
jenis HUTANG (mirror piutang).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.transaksi.jurnal import RefModule
from app.models.transaksi.kas_bank.pembayaran import StatusTransaksi
from app.models.transaksi.kas_bank.voucher_memorial import (
    VoucherMemorial, VoucherMemorialDetail,
)
from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.services.accounting_control import atomic_accounting_write
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.services.setting_akun_service import (
    get_akun_id_or_raise,
    KEY_BEBAN_PENGHAPUSAN_PIUTANG,
    # === Update #072 — akun lawan (Cr) penghapusan hutang. ===
    KEY_PENDAPATAN_PENGHAPUSAN_HUTANG,
)
from app.utils.nomor_dokumen import get_nomor_dokumen_tahunan, resolve_prefix

ZERO = Decimal("0.00")

# === Update #072 — jenis dokumen: 'PIUTANG' (default, perilaku lama) atau
# 'HUTANG' (penghapusan hutang atas supplier — Outstanding Memorial). ===
JENIS_PIUTANG = "PIUTANG"
JENIS_HUTANG = "HUTANG"
JENIS_VALID = {JENIS_PIUTANG, JENIS_HUTANG}


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


# Validasi akun lawan penghapusan (paritas kaidah akun penalti settlement):
# wajib ADA, AKTIF, leaf (DETAIL tanpa anak), dan BUKAN akun kas/bank maupun
# kontrol/subledger piutang-hutang — akun lawan harus akun riil.
BEBAN_FORBIDDEN_SUBCLASSES = {"CASH_BANK", "ACCOUNTS_RECEIVABLE", "ACCOUNTS_PAYABLE"}
BEBAN_FORBIDDEN_SYSTEM_TYPES = {"AR_CONTROL", "AP_CONTROL", "BANK_CLEARING"}
BEBAN_FORBIDDEN_SUBLEDGERS = {"AR", "AP"}


def validate_beban_account(db: Session, akun_beban_id, jenis: str = JENIS_PIUTANG) -> AkunPerkiraan:
    """Akun lawan penghapusan — kaidah sama dengan akun penalti settlement
    (AKTIF, leaf, bukan kas/bank/AR/AP).

    === Update #072 — jenis HUTANG: akun ini adalah akun PENDAPATAN sisi
    Kredit (bukan beban); kaidah validasi tetap sama. ===
    """
    label = (
        "Akun pendapatan penghapusan hutang"
        if jenis == JENIS_HUTANG
        else "Akun beban penghapusan piutang"
    )
    if not akun_beban_id:
        raise ValueError(f"{label} wajib dipilih")
    akun = db.get(AkunPerkiraan, akun_beban_id)
    if akun is None:
        raise ValueError(f"{label} tidak ditemukan")
    if akun.status != "AKTIF" or akun.active is not True:
        raise ValueError(f"{label} harus berstatus AKTIF")
    has_child = db.query(AkunPerkiraan.id).filter(AkunPerkiraan.induk_id == akun.id).first() is not None
    if has_child or akun.tingkat is None or akun.tingkat != TingkatAkun.DETAIL:
        raise ValueError(f"{label} harus akun leaf (DETAIL tanpa akun anak)")
    subclass = (akun.account_subclass or "").upper()
    system_type = (akun.system_account_type or "").upper()
    subledger = (akun.subledger_type or "").upper()
    if (subclass in BEBAN_FORBIDDEN_SUBCLASSES
            or system_type in BEBAN_FORBIDDEN_SYSTEM_TYPES
            or subledger in BEBAN_FORBIDDEN_SUBLEDGERS
            or akun.is_subledger):
        raise ValueError("Akun lawan tidak boleh akun kas/bank atau kontrol piutang/hutang")
    return akun


def _prepare_writeoff(db: Session, pihak_id, tanggal, allocation_data, jenis: str = JENIS_PIUTANG):
    """Bangun baris penghapusan per invoice (mirror settlement_service.prepare).

    Validasi: pihak AKTIF, 1-100 alokasi, invoice milik pihak yang sama,
    POSTED/aktif, IDR (piutang), tanggal >= tanggal invoice, 0 < nilai <= sisa.
    Akun kontrol (piutang/hutang) dibekukan per invoice (control_account
    settlement).

    === Update #072 — jenis HUTANG: pihak = Supplier, invoice = Invoice
    Pembelian (PurchaseInvoice), akun kontrol = akun hutang invoice. ===
    """
    from app.services import settlement_service as settlement

    if jenis not in JENIS_VALID:
        raise ValueError("Jenis Voucher Memorial harus PIUTANG atau HUTANG")

    ar = jenis == JENIS_PIUTANG
    invoice_model = SalesInvoice if ar else PurchaseInvoice
    if ar:
        party = db.get(Pelanggan, pihak_id)
        if party is None or party.status != "AKTIF":
            raise ValueError("Pelanggan tidak tersedia atau tidak aktif")
    else:
        party = db.get(Supplier, pihak_id)
        if party is None or party.status != "AKTIF":
            raise ValueError("Supplier tidak tersedia atau tidak aktif")
    if not allocation_data or len(allocation_data) > 100:
        raise ValueError("Isi 1 sampai 100 alokasi invoice")
    ids = [r["invoice_id"] for r in allocation_data]
    if len(ids) != len(set(ids)):
        raise ValueError("Invoice tidak boleh berulang dalam satu Voucher Memorial")
    invoices = (
        db.query(invoice_model).filter(invoice_model.id.in_(ids))
        .order_by(invoice_model.id).populate_existing().with_for_update().all()
    )
    by_id = {obj.id: obj for obj in invoices}
    if len(invoices) != len(ids):
        raise ValueError("Invoice alokasi tidak ditemukan")
    current = settlement.balances(db, invoices)
    rows = []
    for data in allocation_data:
        obj = by_id[data["invoice_id"]]
        if ar:
            if obj.pelanggan_id != pihak_id:
                raise ValueError("Semua invoice harus milik pelanggan yang dipilih")
        else:
            if obj.supplier_id != pihak_id:
                raise ValueError("Semua invoice harus milik supplier yang dipilih")
        if not current[obj.id]["aktif"]:
            raise ValueError("Penghapusan hanya boleh untuk invoice POSTED yang belum dibatalkan")
        if ar and getattr(obj, "mata_uang", "IDR") != "IDR":
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
            "no_invoice": obj.no_invoice if ar else obj.no_form,
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
    jenis: str = JENIS_PIUTANG,
) -> VoucherMemorial:
    """Buat Voucher Memorial (Gagal Tagih) + rincian per invoice (DRAFT).

    Posting jurnal TIDAK dilakukan di sini — endpoint memanggil
    workflow_service.direct_complete('voucher_memorial', ...) sehingga
    finalisasi mengikuti konfigurasi persetujuan (Update #071).

    === Update #072 — jenis='HUTANG': penghapusan HUTANG atas supplier
    (penerimaan penuh/sebagian/gagal tagih di Outstanding Memorial). ===
    """
    try:
        if jenis not in JENIS_VALID:
            raise ValueError("Jenis Voucher Memorial harus PIUTANG atau HUTANG")
        rows = _prepare_writeoff(db, pihak_id, tanggal, allocation_data, jenis=jenis)
        # Akun lawan: dipilih user, atau default Setting Akun (auto-configure
        # via kandidat fallback COA bila belum ada).
        # - PIUTANG → akun BEBAN sisi Debit (BEBAN_PENGHAPUSAN_PIUTANG);
        # - HUTANG  → akun PENDAPATAN sisi Kredit (PENDAPATAN_PENGHAPUSAN_HUTANG).
        if akun_beban_id is not None:
            validate_beban_account(db, akun_beban_id, jenis=jenis)
        elif jenis == JENIS_HUTANG:
            akun_beban_id = get_akun_id_or_raise(
                db, KEY_PENDAPATAN_PENGHAPUSAN_HUTANG, "Gagal Tagih Hutang (Voucher Memorial)")
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
            jenis=jenis,
            pelanggan_id=pihak_id if jenis == JENIS_PIUTANG else None,
            supplier_id=pihak_id if jenis == JENIS_HUTANG else None,
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
                sales_invoice_id=r["invoice_id"] if jenis == JENIS_PIUTANG else None,
                purchase_invoice_id=r["invoice_id"] if jenis == JENIS_HUTANG else None,
                akun_piutang_id=r["akun_piutang_id"],
                nilai=r["nilai"],
                terutang=r["terutang"],
            ))

        db.commit()
        db.refresh(voucher)
        logger.info(f"VoucherMemorial created: {no_bukti} | jenis={jenis} | total={total_nilai}")
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
    # === Update #072 — filter jenis (PIUTANG/HUTANG) untuk daftar dokumen. ===
    jenis: Optional[str] = None,
) -> Tuple[List[VoucherMemorial], int]:
    """Daftar Voucher Memorial dengan filter & pagination."""
    query = db.query(VoucherMemorial).options(
        joinedload(VoucherMemorial.pelanggan),
        joinedload(VoucherMemorial.supplier),
        joinedload(VoucherMemorial.akun_beban),
        joinedload(VoucherMemorial.creator),
        joinedload(VoucherMemorial.jurnal),
        joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.sales_invoice),
        joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.purchase_invoice),
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
    if jenis:
        query = query.filter(VoucherMemorial.jenis == jenis)
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
            joinedload(VoucherMemorial.supplier),
            joinedload(VoucherMemorial.akun_beban),
            joinedload(VoucherMemorial.creator),
            joinedload(VoucherMemorial.jurnal),
            joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.sales_invoice),
            joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.purchase_invoice),
            joinedload(VoucherMemorial.rincian).joinedload(VoucherMemorialDetail.akun_piutang),
        )
        .filter(VoucherMemorial.id == voucher_id)
        .first()
    )


def post_voucher_memorial(db: Session, voucher: VoucherMemorial, created_by: UUID):
    """Posting jurnal penghapusan — pemanggil memiliki commit/rollback.

    Jurnal sistem (is_manual=False — akun kontrol hanya boleh tersentuh
    alur sistem, bukan jurnal manual pengguna):

    - PIUTANG (#044):
        Dr  Beban Penghapusan Piutang   (total)
        Cr  Piutang Usaha (kontrol)     (per invoice, dikelompokkan per akun)
    - HUTANG (#072):
        Dr  Hutang Usaha (kontrol)      (per invoice, dikelompokkan per akun)
        Cr  Pendapatan Penghapusan Hutang  (total)

    RefModule.MANUAL + tipe PENGHAPUSAN_PIUTANG / PENGHAPUSAN_HUTANG
    (guard duplikat (ref_module, ref_id, tipe_transaksi) tetap efektif).
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, voucher)

    jenis = voucher.jenis or JENIS_PIUTANG

    # Validasi ulang akun lawan + nilai vs sisa terkini (defense in depth,
    # mirror post_penerimaan → validate_payment).
    validate_beban_account(db, voucher.akun_beban_id, jenis=jenis)
    _revalidate_rows(db, voucher)

    tanggal = voucher.tanggal
    no_bukti = voucher.no_bukti
    total_nilai = Decimal(str(voucher.total_nilai or 0))

    entries: list = []
    # Kontrol (piutang/hutang) dikelompokkan per akun.
    grouped: dict = {}
    for r in voucher.rincian:
        grouped[r.akun_piutang_id] = grouped.get(r.akun_piutang_id, ZERO) + Decimal(str(r.nilai))

    if jenis == JENIS_HUTANG:
        # === Update #072 — Dr Hutang Usaha (kontrol), Cr Pendapatan. ===
        for akun_id, nilai in grouped.items():
            entries.append(
                JurnalEntryItem(
                    akun_perkiraan_id=akun_id,
                    debit=Decimal(str(nilai)),
                    keterangan=f"Penghapusan hutang (gagal tagih) {no_bukti}",
                )
            )
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=voucher.akun_beban_id,
                kredit=total_nilai,
                keterangan=f"Pendapatan penghapusan hutang {no_bukti}",
            )
        )
        keterangan_jurnal = f"Voucher Memorial — Penghapusan Hutang (Gagal Tagih) {no_bukti}"
        tipe_transaksi = "PENGHAPUSAN_HUTANG"
    else:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=voucher.akun_beban_id,
                debit=total_nilai,
                keterangan=f"Beban penghapusan piutang {no_bukti}",
            )
        )
        # Kredit: akun piutang kontrol per invoice (dikelompokkan).
        for akun_id, nilai in grouped.items():
            entries.append(
                JurnalEntryItem(
                    akun_perkiraan_id=akun_id,
                    kredit=Decimal(str(nilai)),
                    keterangan=f"Penghapusan piutang (gagal tagih) {no_bukti}",
                )
            )
        keterangan_jurnal = f"Voucher Memorial — Penghapusan Piutang (Gagal Tagih) {no_bukti}"
        tipe_transaksi = "PENGHAPUSAN_PIUTANG"

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.MANUAL,
        ref_no=no_bukti,
        entries=entries,
        keterangan=keterangan_jurnal,
        ref_id=voucher.id,
        tanggal=tanggal,
        created_by=created_by,
        tipe_transaksi=tipe_transaksi,
    )
    voucher.jurnal_umum_id = jurnal.id
    voucher.status = StatusTransaksi.SELESAI
    # === Update #046 — SO ikut SELESAI saat tagihannya selesai lewat
    # penghapusan piutang (Gagal Tagih): penghapusan penuh melunasi invoice
    # (sisa tagihan berkurang) — sinkronkan status SO terkait (idempotent,
    # tanpa commit; pemanggil/workflow yang memiliki transaksi).
    # (Hanya jenis PIUTANG — hutang tidak menyentuh SO.) ===
    if jenis == JENIS_PIUTANG:
        so_ids = {
            r.sales_invoice.sales_order_id
            for r in (voucher.rincian or [])
            if getattr(getattr(r, "sales_invoice", None), "sales_order_id", None) is not None
        }
        if so_ids:
            from app.services.penjualan_service import sync_so_completion
            sync_so_completion(db, so_ids)
    return voucher


def _revalidate_rows(db: Session, voucher: VoucherMemorial) -> None:
    """Validasi ulang saat posting: nilai penghapusan masih <= sisa tagihan
    terkini (bisa berubah bila ada penerimaan/pembayaran lain setelah dokumen
    dibuat).

    === Update #072 — jenis-aware: query model invoice sesuai jenis. ===
    """
    from app.services import settlement_service as settlement
    if not voucher.rincian:
        raise ValueError("Voucher Memorial tanpa rincian invoice tidak dapat diposting")
    jenis = voucher.jenis or JENIS_PIUTANG
    invoice_model = SalesInvoice if jenis == JENIS_PIUTANG else PurchaseInvoice
    if jenis == JENIS_PIUTANG:
        ids = [r.sales_invoice_id for r in voucher.rincian]
    else:
        ids = [r.purchase_invoice_id for r in voucher.rincian]
    invoices = db.query(invoice_model).filter(invoice_model.id.in_(ids)).all()
    current = settlement.balances(db, invoices)
    label_sisa = "sisa tagihan" if jenis == JENIS_PIUTANG else "sisa hutang"
    for r in voucher.rincian:
        inv_id = r.sales_invoice_id if jenis == JENIS_PIUTANG else r.purchase_invoice_id
        sisa_now = current[inv_id]["sisa_tagihan"]
        if Decimal(str(r.nilai)) > sisa_now + Decimal("0.005"):
            raise ValueError(
                f"Nilai penghapusan {current[inv_id]['no_dokumen']} melebihi {label_sisa} "
                f"terkini {sisa_now} — mungkin sudah ada pembayaran/penerimaan lain. Periksa ulang dokumen."
            )
