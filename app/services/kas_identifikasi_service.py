"""Update #02 + #03 — Identifikasi & Alokasi transaksi kas Belum Teridentifikasi.

Dokumen kas (penerimaan/pembayaran) dengan jenis_transaksi =
BELUM_TERIDENTIFIKASI diposting saat dibuat ke akun penampung sementara
(update #03: arah-spesifik — penerimaan ke KAS_PENAMPUNG_PENERIMAAN 216001
liabilitas, pembayaran ke KAS_PENAMPUNG_PEMBAYARAN 117004 aset; dokumen
update #02 lama tetap membaca 216000 dari rincian-nya). Begitu asal mutasi
diketahui, user menjalankan Identifikasi & Alokasi: nilai dipindahkan dari
akun penampung ke akun-akun yang benar — bisa beberapa baris sekaligus
maupun bertahap — melalui SATU jurnal reklasifikasi per sesi identifikasi:

    Penerimaan:  Dr Penampung (total)  |  Cr Akun tujuan (per baris alokasi)
    Pembayaran:   Dr Akun tujuan (per baris)  |  Cr Penampung (total)

Sisi Kas/Bank TIDAK dijurnal ulang — tidak ada jurnal bank ganda; saldo
bank tetap konsisten dengan mutasi aslinya.

Update #03 (catatan-update-03 — Piutang/Hutang Kontrol Sementara) menambah
DUA mode identifikasi berbasis invoice, selain mode akun COA bebas:

- ``PIUTANG`` (penerimaan): user membuka transaksi penerimaan, memilih
  pelanggan + invoice penjualan outstanding → sistem mereklasifikasi akun
  penampung ke akun piutang pelanggan (jurnal: Dr Penampung X /
  Dr PPh 23 Dibayar di Muka Σpph23 / Cr Piutang Σnilai) DAN membuat baris
  ``PaymentAllocation`` sehingga outstanding invoice langsung terbarui —
  tanpa jurnal penerimaan bank ganda.
- ``HUTANG`` (pembayaran): pilih supplier + invoice pembelian → jurnal:
  Cr Penampung X / Dr Hutang supplier Σnilai / Cr Potongan Hutang Σdiskon /
  Cr Hutang PPh 23 Σpph23 + ``PaymentAllocation`` ke purchase invoice.

  X = Σ(nilai − diskon − pph23) = nilai yang keluar dari penampung.
  Identifikasi penuh (sisa penampung dokumen = 0) mengubah
  jenis_transaksi dokumen menjadi PELUNASAN_INVOICE dan memasang
  pelanggan/supplier di header — persis perilaku pelunasan biasa.

Pembatalan sesi PIUTANG/HUTANG: ``reverse_journal`` (jurnal pembalik,
audit trail utuh) + baris PaymentAllocation sesi tsb dihapus sehingga
outstanding invoice pulih; bila dokumen sempat berubah menjadi
PELUNASAN_INVOICE, jenis-nya dikembalikan ke BELUM_TERIDENTIFIKASI.

Guard AR/AP: mode akun COA bebas (LAINNYA) MENOLAK akun piutang/hutang
(kontrol maupun subledger) — akun pihak hanya boleh tersentuh lewat alokasi
invoice supaya subledger dan General Ledger tidak pernah berbeda.

Monitoring (``get_penampung_monitoring``): saldo GL tiap akun penampung
dibandingkan dengan Σ sisa dokumen BELUM aktif (rekonsiliasi ke nol), plus
cek jurnal balance / jurnal identifikasi tidak menyentuh kas / tidak ada
outstanding negatif.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun
from app.models.master.kas_bank_akun import KasBankAkun
from app.models.master.pelanggan import Pelanggan
from app.models.master.supplier import Supplier
from app.models.transaksi.jurnal import JurnalUmum, RefModule
from app.models.transaksi.kas_bank.kas_identifikasi import (
    KasIdentifikasi, KasIdentifikasiRincian,
    JENIS_IDENTIFIKASI_LAINNYA, JENIS_IDENTIFIKASI_PIUTANG, JENIS_IDENTIFIKASI_HUTANG,
)
from app.models.transaksi.kas_bank.penerimaan import PenerimaanKas
from app.models.transaksi.kas_bank.pembayaran import (
    PembayaranKas, StatusTransaksi, JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI,
    JENIS_TRANSAKSI_PELUNASAN_INVOICE,
)
from app.models.transaksi.payment_allocation import PaymentAllocation
from app.models.detail.penerimaan_rincian import PenerimaanRincian
from app.models.detail.pembayaran_rincian import PembayaranRincian
from app.services.accounting_control import atomic_accounting_write
from app.services.posting_service import auto_posting_jurnal, reverse_journal, JurnalEntryItem
from app.services.setting_akun_service import (
    get_akun_id_or_raise, get_setting_akun_id_optional,
    KEY_POTONGAN_HUTANG, KEY_HUTANG_PPH23, KEY_PPH23_DIBAYAR_DIMUKA,
    KEY_KAS_PENAMPUNG_PENERIMAAN, KEY_KAS_PENAMPUNG_PEMBAYARAN,
    KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI,
)
from app.utils.nomor_dokumen import get_nomor_dokumen_tahunan

ZERO = Decimal("0.00")
TOLERANSI = Decimal("0.005")
JAKARTA = timezone(timedelta(hours=7))
TIPE_TRANSAKSI_IDENTIFIKASI = "IDENTIFIKASI_KAS"
TIPE_TRANSAKSI_IDENTIFIKASI_PIUTANG = "IDENTIFIKASI_PIUTANG"
TIPE_TRANSAKSI_IDENTIFIKASI_HUTANG = "IDENTIFIKASI_HUTANG"


def _local_day(value):
    if isinstance(value, datetime):
        return (value.astimezone(JAKARTA) if value.tzinfo else value).date()
    return value


def _model(jenis_doc: str):
    if jenis_doc in ("penerimaan", "penerimaan_kas"):
        return PenerimaanKas
    if jenis_doc in ("pembayaran", "pembayaran_kas"):
        return PembayaranKas
    raise ValueError("Jenis dokumen harus 'penerimaan' atau 'pembayaran'")


def _identifikasi_col(jenis_doc: str):
    from app.models.transaksi.kas_bank.kas_identifikasi import KasIdentifikasi as KI
    return KI.penerimaan_id if jenis_doc.startswith("penerimaan") else KI.pembayaran_id


def teridentifikasi_aktif(db: Session, jenis_doc: str, dokumen_id: UUID) -> Decimal:
    """Σ nilai identifikasi aktif (status != BATAL) untuk satu dokumen kas."""
    value = db.query(func.coalesce(func.sum(KasIdentifikasi.total_nilai), 0)).filter(
        _identifikasi_col(jenis_doc) == dokumen_id,
        KasIdentifikasi.status != StatusTransaksi.BATAL,
    ).scalar()
    return Decimal(str(value or 0))


def sisa_belum_teridentifikasi(db: Session, jenis_doc: str, dokumen) -> Decimal:
    total = Decimal(str(getattr(dokumen, "total_nilai", 0) or 0))
    return max(ZERO, total - teridentifikasi_aktif(db, jenis_doc, dokumen.id))


def get_identifikasi_list(db: Session, jenis_doc: str, dokumen_id: UUID):
    """Dokumen + seluruh sesi identifikasi + progres teridentifikasi/sisa."""
    model = _model(jenis_doc)
    dokumen = (
        db.query(model)
        .options(
            joinedload(getattr(model, "kas_bank")).joinedload(KasBankAkun.akun_perkiraan),
            joinedload(getattr(model, "rincian")),
            joinedload(getattr(model, "jurnal")),
        )
        .filter(model.id == dokumen_id)
        .first()
    )
    if dokumen is None:
        raise ValueError("Dokumen kas tidak ditemukan")
    rows = (
        db.query(KasIdentifikasi)
        .options(
            joinedload(KasIdentifikasi.rincian).joinedload(KasIdentifikasiRincian.akun_perkiraan),
            joinedload(KasIdentifikasi.jurnal),
            joinedload(KasIdentifikasi.creator),
        )
        .filter(_identifikasi_col(jenis_doc) == dokumen_id)
        .order_by(KasIdentifikasi.created_at.desc())
        .all()
    )
    teridentifikasi = sum(
        (Decimal(str(r.total_nilai or 0)) for r in rows if r.status != StatusTransaksi.BATAL),
        ZERO,
    )
    total = Decimal(str(dokumen.total_nilai or 0))
    return {
        "dokumen": dokumen,
        "identifikasi": rows,
        "teridentifikasi": teridentifikasi,
        "sisa": max(ZERO, total - teridentifikasi),
    }


def _get_dokumen_for_update(db: Session, jenis_doc: str, dokumen_id: UUID):
    model = _model(jenis_doc)
    dokumen = (
        db.query(model)
        .filter(model.id == dokumen_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if dokumen is None:
        raise ValueError("Dokumen kas tidak ditemukan")
    return dokumen


def _resolve_penampung_dokumen(dokumen) -> UUID:
    """Akun penampung aktual dokumen = satu-satunya baris rincian-nya.

    Dibekukan saat create (dari setting saat itu) supaya jurnal
    identifikasi selalu membalik akun yang benar-benar tersentuh jurnal
    kas awal — walau setting penampung kemudian diubah admin.
    """
    baris = list(getattr(dokumen, "rincian", None) or [])
    if len(baris) != 1:
        raise ValueError(
            "Dokumen 'Belum Teridentifikasi' tidak valid — rincian harus "
            "tepat satu baris akun penampung"
        )
    return baris[0].akun_perkiraan_id


def _guards_dokumen_identifikasi(dokumen, tanggal) -> UUID:
    """Guard umum semua mode identifikasi; return akun penampung dokumen."""
    jenis = (getattr(dokumen, "jenis_transaksi", None) or "").upper()
    if jenis != JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI:
        raise ValueError(
            "Identifikasi & Alokasi hanya untuk dokumen jenis 'Belum Teridentifikasi'"
        )
    if getattr(dokumen, "status", None) == StatusTransaksi.BATAL:
        raise ValueError("Dokumen sudah dibatalkan")
    if not getattr(dokumen, "jurnal_umum_id", None) or dokumen.status != StatusTransaksi.SELESAI:
        raise ValueError("Dokumen belum diposting — identifikasi hanya untuk dokumen posted")
    if _local_day(tanggal) < _local_day(dokumen.tanggal):
        raise ValueError("Tanggal identifikasi tidak boleh sebelum tanggal dokumen kas")
    return _resolve_penampung_dokumen(dokumen)


def _validate_akun_alokasi(db: Session, akun_id: UUID, penampung_id: UUID) -> AkunPerkiraan:
    akun = db.get(AkunPerkiraan, akun_id)
    if akun is None:
        raise ValueError("Akun alokasi tidak ditemukan")
    if akun.id == penampung_id:
        raise ValueError("Akun alokasi tidak boleh akun penampung itu sendiri")
    if akun.tingkat is None or akun.tingkat != TingkatAkun.DETAIL:
        raise ValueError(f"Akun {akun.kode} bukan akun DETAIL")
    if akun.active is False or akun.status not in (None, "AKTIF"):
        raise ValueError(f"Akun {akun.kode} ({akun.nama}) tidak aktif")
    if not akun.is_postable_for_system:
        raise ValueError(
            f"Akun {akun.kode} ({akun.nama}) tidak dapat dipakai posting sistem"
        )
    # === Update #03 — guard AR/AP untuk mode akun COA bebas ===
    # Akun piutang/hutang (kontrol AR/AP maupun subledger per pihak) hanya
    # boleh tersentuh lewat mode Identifikasi Piutang/Hutang (dengan alokasi
    # invoice) supaya subledger dan General Ledger tidak pernah berbeda
    # (catatan-update-03: "Pastikan tidak ada ... perbedaan antara subledger
    # dan General Ledger").
    from app.services.kas_bank_service import (
        AR_AP_FORBIDDEN_SYSTEM_TYPES, AR_AP_FORBIDDEN_SUBCLASSES,
    )
    system_type = (akun.system_account_type or "").upper()
    subclass = (akun.account_subclass or "").upper()
    if (system_type in AR_AP_FORBIDDEN_SYSTEM_TYPES
            or subclass in AR_AP_FORBIDDEN_SUBCLASSES
            or akun.is_subledger):
        raise ValueError(
            f"Akun {akun.kode} ({akun.nama}) adalah akun piutang/hutang. "
            "Identifikasi ke pelanggan/supplier wajib lewat mode "
            "'Identifikasi Piutang/Hutang' (pilih pihak lalu invoice yang "
            "dilunasi) — bukan alokasi akun COA bebas — supaya outstanding "
            "invoice ikut terbarui dan subledger tetap konsisten dengan GL."
        )
    return akun


def _guard_invoice_belum_dialokasikan(db: Session, dokumen, rows: list, jenis: str) -> None:
    """Update #03 — satu invoice hanya boleh sekali teralokasi per dokumen kas.

    Menghindari pelanggaran unique constraint (penerimaan_id, sales_invoice_id)
    saat identifikasi bertahap: tambahkan nilai di sesi pertama, atau batalkan
    sesi sebelumnya lebih dulu.
    """
    if jenis == "piutang":
        inv_col, doc_col = PaymentAllocation.sales_invoice_id, PaymentAllocation.penerimaan_id
    else:
        inv_col, doc_col = PaymentAllocation.purchase_invoice_id, PaymentAllocation.pembayaran_id
    ids = [r["invoice_id"] for r in rows]
    bentrok = (
        db.query(inv_col)
        .filter(doc_col == dokumen.id, inv_col.in_(ids))
        .all()
    )
    if bentrok:
        raise ValueError(
            "Invoice yang dipilih sudah punya alokasi pada dokumen ini dari "
            "sesi identifikasi sebelumnya. Batalkan sesi tersebut, atau "
            "identifikasikan invoice lain."
        )


def _snapshot_organisasi(db: Session, dokumen) -> dict:
    sumber = db.get(JurnalUmum, getattr(dokumen, "jurnal_umum_id", None))
    return (
        {f: getattr(sumber, f) for f in
         ("company_id", "branch_id", "department_id", "cost_center_id", "project_id")}
        if sumber is not None else {}
    )


def _refresh_dokumen_setelah_identifikasi(db: Session, jenis_doc: str, dokumen, pihak_id) -> None:
    """Update #03 — setelah sesi PIUTANG/HUTANG tercatat:

    - sisa penampung dokumen habis → jenis_transaksi menjadi
      PELUNASAN_INVOICE + pihak terpasang di header (perilaku sama dengan
      pelunasan biasa); dokumen tampil di list sebagai Pelunasan Invoice
      dengan alokasi invoice.
    - masih ada sisa → dokumen tetap BELUM_TERIDENTIFIKASI (teridentifikasi
      parsial; sisa tetap dipantau sampai nol).
    """
    db.flush()
    sisa = sisa_belum_teridentifikasi(db, jenis_doc, dokumen)
    if sisa <= ZERO:
        dokumen.jenis_transaksi = JENIS_TRANSAKSI_PELUNASAN_INVOICE
        if jenis_doc.startswith("penerimaan"):
            dokumen.pelanggan_id = pihak_id
        else:
            dokumen.supplier_id = pihak_id
        db.flush()
        logger.info(
            f"Dokumen {dokumen.no_bukti} teridentifikasi penuh → "
            f"PELUNASAN_INVOICE (pihak {pihak_id})"
        )


@atomic_accounting_write
def create_identifikasi(
    db: Session,
    jenis_doc: str,
    dokumen_id: UUID,
    tanggal: datetime,
    alokasi_data: list,
    catatan: Optional[str] = None,
    created_by: UUID = None,
) -> KasIdentifikasi:
    """Buat sesi Identifikasi & Alokasi (mode akun COA bebas) + jurnal reklasifikasi.

    Pola mengikuti reverse_transfer: aksi langsung tanpa workflow multi-langkah
    (audit trail = jurnal + reversal chain + created_by; reversal tersedia
    via cancel_identifikasi). Jurnal hanya menyentuh penampung ↔ akun tujuan.
    """
    dokumen = _get_dokumen_for_update(db, jenis_doc, dokumen_id)
    penampung_id = _guards_dokumen_identifikasi(dokumen, tanggal)
    if not alokasi_data or len(alokasi_data) > 100:
        raise ValueError("Isi 1 sampai 100 baris alokasi identifikasi")

    total = ZERO
    baris = []
    for r in alokasi_data:
        akun = _validate_akun_alokasi(db, r.get("akun_perkiraan_id"), penampung_id)
        nilai = Decimal(str(r.get("nilai", 0)))
        if not nilai.is_finite() or nilai <= 0 or nilai != nilai.quantize(Decimal(".01")):
            raise ValueError("Nilai alokasi harus positif, maksimal 2 desimal")
        keterangan = (r.get("keterangan") or "").strip() or None
        baris.append({"akun_perkiraan_id": akun.id, "nilai": nilai, "keterangan": keterangan})
        total += nilai
    sisa = sisa_belum_teridentifikasi(db, jenis_doc, dokumen)
    if total > sisa:
        raise ValueError(
            f"Total alokasi {total} melebihi sisa belum teridentifikasi {sisa} "
            f"dokumen {dokumen.no_bukti}"
        )

    no_bukti = get_nomor_dokumen_tahunan(
        db, KasIdentifikasi, prefix="IDN", no_column="no_bukti",
        tanggal=_local_day(tanggal),
    )

    identifikasi = KasIdentifikasi(
        no_bukti=no_bukti,
        tanggal=tanggal,
        penerimaan_id=dokumen.id if jenis_doc.startswith("penerimaan") else None,
        pembayaran_id=dokumen.id if jenis_doc.startswith("pembayaran") else None,
        jenis=JENIS_IDENTIFIKASI_LAINNYA,
        catatan=(catatan or None),
        total_nilai=total,
        status=StatusTransaksi.DRAFT,
        created_by=created_by,
    )
    db.add(identifikasi)
    db.flush()

    for r in baris:
        db.add(KasIdentifikasiRincian(
            identifikasi_id=identifikasi.id,
            akun_perkiraan_id=r["akun_perkiraan_id"],
            nilai=r["nilai"],
            keterangan=r["keterangan"],
        ))
    db.flush()

    # === Jurnal reklasifikasi — sisi Kas/Bank TIDAK disentuh ===
    label_dokumen = "Penerimaan" if jenis_doc.startswith("penerimaan") else "Pembayaran"
    if jenis_doc.startswith("penerimaan"):
        # Asal: Dr Kas / Cr Penampung → identifikasi membalik sisi penampung:
        entries = [JurnalEntryItem(
            akun_perkiraan_id=penampung_id, debit=total,
            keterangan=f"Identifikasi {no_bukti}",
        )]
        entries += [JurnalEntryItem(
            akun_perkiraan_id=r["akun_perkiraan_id"], kredit=r["nilai"],
            keterangan=r["keterangan"] or f"Alokasi {no_bukti}",
        ) for r in baris]
    else:
        # Asal: Dr Penampung / Cr Kas → identifikasi membalik sisi penampung:
        entries = [JurnalEntryItem(
            akun_perkiraan_id=penampung_id, kredit=total,
            keterangan=f"Identifikasi {no_bukti}",
        )]
        entries += [JurnalEntryItem(
            akun_perkiraan_id=r["akun_perkiraan_id"], debit=r["nilai"],
            keterangan=r["keterangan"] or f"Alokasi {no_bukti}",
        ) for r in baris]

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.MANUAL,
        ref_no=no_bukti,
        ref_id=identifikasi.id,
        entries=entries,
        keterangan=(
            f"Identifikasi & Alokasi {no_bukti} atas {label_dokumen} "
            f"{dokumen.no_bukti} (mutasi bank tidak dijurnal ulang)"
        ),
        tanggal=tanggal,
        created_by=created_by,
        tipe_transaksi=TIPE_TRANSAKSI_IDENTIFIKASI,
        organization=_snapshot_organisasi(db, dokumen),
    )
    identifikasi.jurnal_umum_id = jurnal.id
    identifikasi.status = StatusTransaksi.SELESAI
    db.flush()
    logger.info(
        f"KasIdentifikasi created: {no_bukti} | {label_dokumen} {dokumen.no_bukti} "
        f"| total={total} | {len(baris)} baris alokasi"
    )
    return identifikasi


# ==========================================
# UPDATE #03 — IDENTIFIKASI PIUTANG / HUTANG
# (reklasifikasi penampung ke akun pihak + alokasi invoice)
# ==========================================

@atomic_accounting_write
def create_identifikasi_piutang(
    db: Session,
    dokumen_id: UUID,
    pelanggan_id: UUID,
    tanggal: datetime,
    alokasi_data: list,
    catatan: Optional[str] = None,
    created_by: UUID = None,
) -> KasIdentifikasi:
    """Identifikasi penerimaan Belum Teridentifikasi ke pelanggan + invoice.

    Jurnal reklasifikasi (kas TIDAK disentuh):
        Dr Penampung X                       (X = Σ nilai − Σ pph23)
        Dr PPh 23 Dibayar di Muka Σpph23     (bila ada potongan per faktur)
        Cr Piutang pelanggan Σnilai (grup akun kontrol invoice)

    + baris PaymentAllocation per invoice → outstanding invoice terbarui.
    Bila sisa penampung dokumen habis, dokumen menjadi PELUNASAN_INVOICE.
    """
    from app.services import settlement_service

    dokumen = _get_dokumen_for_update(db, "penerimaan", dokumen_id)
    penampung_id = _guards_dokumen_identifikasi(dokumen, tanggal)

    pelanggan = db.get(Pelanggan, pelanggan_id) if pelanggan_id else None
    if pelanggan is None or (getattr(pelanggan, "status", None) or "AKTIF") != "AKTIF":
        raise ValueError("Pelanggan tidak ditemukan atau tidak aktif")

    if not alokasi_data or len(alokasi_data) > 100:
        raise ValueError("Isi 1 sampai 100 baris alokasi invoice")

    # Validasi invoice (milik pelanggan, posted-aktif, IDR, tanggal, nilai ≤
    # sisa tagihan, pph23 ≤ nilai) + akun kontrol dibekukan dari invoice.
    prep_data = [
        {"invoice_id": r.get("sales_invoice_id"), "nilai": r.get("nilai"), "pph23": r.get("pph23")}
        for r in alokasi_data
    ]
    rows, _grouped = settlement_service.prepare(db, "piutang", pelanggan_id, tanggal, prep_data)

    total_pph23 = sum((r["pph23"] for r in rows), ZERO)
    total_nilai = sum((r["nilai"] for r in rows), ZERO)
    total = total_nilai - total_pph23  # X — nilai keluar dari penampung
    if total <= ZERO:
        raise ValueError(
            "Total identifikasi harus positif — pastikan nilai alokasi lebih "
            "besar dari PPh 23 yang dipotong"
        )
    sisa = sisa_belum_teridentifikasi(db, "penerimaan", dokumen)
    if total > sisa:
        raise ValueError(
            f"Total identifikasi {total} melebihi sisa belum teridentifikasi "
            f"{sisa} dokumen {dokumen.no_bukti} — kurangi nilai alokasi atau "
            f"PPh 23, atau identifikasikan sebagian saja"
        )
    # Guard terakhir: invoice tidak boleh teralokasi ganda pada dokumen ini.
    _guard_invoice_belum_dialokasikan(db, dokumen, rows, "piutang")

    no_bukti = get_nomor_dokumen_tahunan(
        db, KasIdentifikasi, prefix="IDN", no_column="no_bukti",
        tanggal=_local_day(tanggal),
    )
    identifikasi = KasIdentifikasi(
        no_bukti=no_bukti,
        tanggal=tanggal,
        penerimaan_id=dokumen.id,
        jenis=JENIS_IDENTIFIKASI_PIUTANG,
        pelanggan_id=pelanggan_id,
        catatan=(catatan or None),
        total_nilai=total,
        status=StatusTransaksi.DRAFT,
        created_by=created_by,
    )
    db.add(identifikasi)
    db.flush()

    # Rincian = snapshot audit per invoice (akun kontrol piutang invoice).
    for r in rows:
        db.add(KasIdentifikasiRincian(
            identifikasi_id=identifikasi.id,
            akun_perkiraan_id=r["akun_perkiraan_id"],
            nilai=r["nilai"],
            pph23=r["pph23"],
            sales_invoice_id=r["invoice_id"],
            keterangan=f"Piutang {pelanggan.nama}",
        ))
    db.flush()

    # === Jurnal reklasifikasi — kas TIDAK disentuh ===
    entries = [JurnalEntryItem(
        akun_perkiraan_id=penampung_id, debit=total,
        keterangan=f"Identifikasi piutang {no_bukti}",
    )]
    if total_pph23 > ZERO:
        akun_pph23_id = get_akun_id_or_raise(db, KEY_PPH23_DIBAYAR_DIMUKA, "PPh 23 identifikasi piutang")
        entries.append(JurnalEntryItem(
            akun_perkiraan_id=akun_pph23_id, debit=total_pph23,
            keterangan=f"PPh 23 dipotong pelanggan {no_bukti}",
        ))
    # Kredit piutang pelanggan per grup akun kontrol (Σ nilai penuh).
    grup: dict = {}
    for r in rows:
        grup[r["akun_perkiraan_id"]] = grup.get(r["akun_perkiraan_id"], ZERO) + r["nilai"]
    for akun_id, nilai in grup.items():
        entries.append(JurnalEntryItem(
            akun_perkiraan_id=akun_id, kredit=nilai,
            keterangan=f"Piutang {pelanggan.nama} {no_bukti}",
        ))

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.MANUAL,
        ref_no=no_bukti,
        ref_id=identifikasi.id,
        entries=entries,
        keterangan=(
            f"Identifikasi Piutang {no_bukti} atas Penerimaan "
            f"{dokumen.no_bukti} → {pelanggan.nama} (reklasifikasi penampung; "
            f"mutasi bank tidak dijurnal ulang)"
        ),
        tanggal=tanggal,
        created_by=created_by,
        tipe_transaksi=TIPE_TRANSAKSI_IDENTIFIKASI_PIUTANG,
        organization=_snapshot_organisasi(db, dokumen),
    )
    identifikasi.jurnal_umum_id = jurnal.id
    identifikasi.status = StatusTransaksi.SELESAI

    # Alokasi invoice → outstanding langsung terbarui (ledger-based).
    for r in rows:
        db.add(PaymentAllocation(
            penerimaan_id=dokumen.id,
            sales_invoice_id=r["invoice_id"],
            nilai=r["nilai"],
            akun_perkiraan_id=r["akun_perkiraan_id"],
            pph23=r["pph23"],
            terutang=r["terutang"],
            kas_identifikasi_id=identifikasi.id,
        ))
    db.flush()

    _refresh_dokumen_setelah_identifikasi(db, "penerimaan", dokumen, pelanggan_id)
    logger.info(
        f"KasIdentifikasi PIUTANG created: {no_bukti} | Penerimaan "
        f"{dokumen.no_bukti} | pelanggan={pelanggan.nama} | penampung={total} "
        f"| pph23={total_pph23} | {len(rows)} invoice"
    )
    return identifikasi


@atomic_accounting_write
def create_identifikasi_hutang(
    db: Session,
    dokumen_id: UUID,
    supplier_id: UUID,
    tanggal: datetime,
    alokasi_data: list,
    catatan: Optional[str] = None,
    created_by: UUID = None,
) -> KasIdentifikasi:
    """Identifikasi pembayaran Belum Teridentifikasi ke supplier + invoice.

    Jurnal reklasifikasi (kas TIDAK disentuh):
        Cr Penampung X                        (X = Σ nilai − Σ diskon − Σ pph23)
        Dr Hutang supplier Σnilai (grup kontrol invoice)
        Cr Potongan Hutang Σdiskon            (bila ada diskon pelunasan)
        Cr Hutang PPh 23 Σpph23               (bila PPh 23 ditahan)

    + baris PaymentAllocation per invoice → outstanding invoice terbarui.
    Bila sisa penampung dokumen habis, dokumen menjadi PELUNASAN_INVOICE.
    """
    from app.services import settlement_service

    dokumen = _get_dokumen_for_update(db, "pembayaran", dokumen_id)
    penampung_id = _guards_dokumen_identifikasi(dokumen, tanggal)

    supplier = db.get(Supplier, supplier_id) if supplier_id else None
    if supplier is None or (getattr(supplier, "status", None) or "AKTIF") != "AKTIF":
        raise ValueError("Supplier tidak ditemukan atau tidak aktif")

    if not alokasi_data or len(alokasi_data) > 100:
        raise ValueError("Isi 1 sampai 100 baris alokasi invoice")

    prep_data = [
        {"invoice_id": r.get("purchase_invoice_id"), "nilai": r.get("nilai"),
         "diskon": r.get("diskon"), "pph23": r.get("pph23")}
        for r in alokasi_data
    ]
    rows, _grouped = settlement_service.prepare(db, "hutang", supplier_id, tanggal, prep_data)

    total_diskon = sum((r["diskon"] for r in rows), ZERO)
    total_pph23 = sum((r["pph23"] for r in rows), ZERO)
    total_nilai = sum((r["nilai"] for r in rows), ZERO)
    total = total_nilai - total_diskon - total_pph23  # X — keluar dari penampung
    if total <= ZERO:
        raise ValueError(
            "Total identifikasi harus positif — pastikan nilai alokasi lebih "
            "besar dari diskon + PPh 23"
        )
    sisa = sisa_belum_teridentifikasi(db, "pembayaran", dokumen)
    if total > sisa:
        raise ValueError(
            f"Total identifikasi {total} melebihi sisa belum teridentifikasi "
            f"{sisa} dokumen {dokumen.no_bukti} — kurangi nilai alokasi, "
            f"diskon, atau PPh 23, atau identifikasikan sebagian saja"
        )
    # Guard terakhir: invoice tidak boleh teralokasi ganda pada dokumen ini.
    _guard_invoice_belum_dialokasikan(db, dokumen, rows, "hutang")

    no_bukti = get_nomor_dokumen_tahunan(
        db, KasIdentifikasi, prefix="IDN", no_column="no_bukti",
        tanggal=_local_day(tanggal),
    )
    identifikasi = KasIdentifikasi(
        no_bukti=no_bukti,
        tanggal=tanggal,
        pembayaran_id=dokumen.id,
        jenis=JENIS_IDENTIFIKASI_HUTANG,
        supplier_id=supplier_id,
        catatan=(catatan or None),
        total_nilai=total,
        status=StatusTransaksi.DRAFT,
        created_by=created_by,
    )
    db.add(identifikasi)
    db.flush()

    for r in rows:
        db.add(KasIdentifikasiRincian(
            identifikasi_id=identifikasi.id,
            akun_perkiraan_id=r["akun_perkiraan_id"],
            nilai=r["nilai"],
            diskon=r["diskon"],
            pph23=r["pph23"],
            purchase_invoice_id=r["invoice_id"],
            keterangan=f"Hutang {supplier.nama}",
        ))
    db.flush()

    # === Jurnal reklasifikasi — kas TIDAK disentuh ===
    entries = [JurnalEntryItem(
        akun_perkiraan_id=penampung_id, kredit=total,
        keterangan=f"Identifikasi hutang {no_bukti}",
    )]
    # Debit hutang supplier per grup akun kontrol (Σ nilai penuh).
    grup: dict = {}
    for r in rows:
        grup[r["akun_perkiraan_id"]] = grup.get(r["akun_perkiraan_id"], ZERO) + r["nilai"]
    for akun_id, nilai in grup.items():
        entries.append(JurnalEntryItem(
            akun_perkiraan_id=akun_id, debit=nilai,
            keterangan=f"Hutang {supplier.nama} {no_bukti}",
        ))
    if total_diskon > ZERO:
        akun_potongan_id = get_akun_id_or_raise(db, KEY_POTONGAN_HUTANG, "diskon identifikasi hutang")
        entries.append(JurnalEntryItem(
            akun_perkiraan_id=akun_potongan_id, kredit=total_diskon,
            keterangan=f"Potongan pelunasan {no_bukti}",
        ))
    if total_pph23 > ZERO:
        akun_pph23_id = get_akun_id_or_raise(db, KEY_HUTANG_PPH23, "PPh 23 identifikasi hutang")
        entries.append(JurnalEntryItem(
            akun_perkiraan_id=akun_pph23_id, kredit=total_pph23,
            keterangan=f"PPh 23 ditahan {no_bukti}",
        ))

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.MANUAL,
        ref_no=no_bukti,
        ref_id=identifikasi.id,
        entries=entries,
        keterangan=(
            f"Identifikasi Hutang {no_bukti} atas Pembayaran "
            f"{dokumen.no_bukti} → {supplier.nama} (reklasifikasi penampung; "
            f"mutasi bank tidak dijurnal ulang)"
        ),
        tanggal=tanggal,
        created_by=created_by,
        tipe_transaksi=TIPE_TRANSAKSI_IDENTIFIKASI_HUTANG,
        organization=_snapshot_organisasi(db, dokumen),
    )
    identifikasi.jurnal_umum_id = jurnal.id
    identifikasi.status = StatusTransaksi.SELESAI

    for r in rows:
        db.add(PaymentAllocation(
            pembayaran_id=dokumen.id,
            purchase_invoice_id=r["invoice_id"],
            nilai=r["nilai"],
            akun_perkiraan_id=r["akun_perkiraan_id"],
            diskon=r["diskon"],
            pph23=r["pph23"],
            terutang=r["terutang"],
            kas_identifikasi_id=identifikasi.id,
        ))
    db.flush()

    _refresh_dokumen_setelah_identifikasi(db, "pembayaran", dokumen, supplier_id)
    logger.info(
        f"KasIdentifikasi HUTANG created: {no_bukti} | Pembayaran "
        f"{dokumen.no_bukti} | supplier={supplier.nama} | penampung={total} "
        f"| diskon={total_diskon} | pph23={total_pph23} | {len(rows)} invoice"
    )
    return identifikasi


@atomic_accounting_write
def cancel_identifikasi(db: Session, identifikasi_id: UUID, user_id: UUID = None, reason: str = None):
    """Batalkan sesi identifikasi: jurnal pembalik (reversal) + status BATAL.

    Update #03 — untuk sesi PIUTANG/HUTANG: baris PaymentAllocation sesi ini
    dihapus sehingga outstanding invoice pulih, dan bila dokumen sempat
    teridentifikasi penuh (menjadi PELUNASAN_INVOICE), jenis-nya kembali ke
    BELUM_TERIDENTIFIKASI dengan sisa yang bisa diidentifikasi ulang.

    Jurnal asli tetap POSTED (audit trail); saldo akun kembali ke keadaan
    sebelum identifikasi lewat jurnal pembalik berantai (reversal_of_id).
    """
    obj = (
        db.query(KasIdentifikasi)
        .filter(KasIdentifikasi.id == identifikasi_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if obj is None:
        raise ValueError("Identifikasi tidak ditemukan")
    if obj.status == StatusTransaksi.BATAL:
        raise ValueError("Identifikasi sudah dibatalkan")
    if obj.status != StatusTransaksi.SELESAI or not obj.jurnal_umum_id:
        raise ValueError("Identifikasi belum diposting")
    reverse_journal(
        db, obj.jurnal_umum_id, user_id or obj.created_by,
        reason or f"Pembatalan identifikasi {obj.no_bukti}",
    )
    obj.status = StatusTransaksi.BATAL
    db.flush()

    # === Update #03 — pulihkan alokasi invoice + jenis dokumen ===
    jenis_sesi = (getattr(obj, "jenis", None) or JENIS_IDENTIFIKASI_LAINNYA).upper()
    if jenis_sesi in (JENIS_IDENTIFIKASI_PIUTANG, JENIS_IDENTIFIKASI_HUTANG):
        db.query(PaymentAllocation).filter(
            PaymentAllocation.kas_identifikasi_id == obj.id
        ).delete(synchronize_session=False)
        db.flush()

        jenis_doc = "penerimaan" if obj.penerimaan_id else "pembayaran"
        dokumen = _get_dokumen_for_update(db, jenis_doc, obj.dokumen_id)
        sisa = sisa_belum_teridentifikasi(db, jenis_doc, dokumen)
        if sisa > ZERO and (
            (getattr(dokumen, "jenis_transaksi", None) or "").upper()
            == JENIS_TRANSAKSI_PELUNASAN_INVOICE
        ):
            # Dokumen sempat teridentifikasi penuh → kembalikan ke BELUM.
            dokumen.jenis_transaksi = JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI
            masih_ada_alokasi = (
                db.query(PaymentAllocation.id)
                .filter(
                    (PaymentAllocation.penerimaan_id == dokumen.id)
                    if jenis_doc == "penerimaan"
                    else (PaymentAllocation.pembayaran_id == dokumen.id)
                )
                .first() is not None
            )
            if not masih_ada_alokasi:
                if jenis_doc == "penerimaan":
                    dokumen.pelanggan_id = None
                else:
                    dokumen.supplier_id = None
            db.flush()
        logger.info(
            f"KasIdentifikasi {jenis_sesi} cancelled: {obj.no_bukti} — alokasi "
            f"invoice dipulihkan, sisa dokumen {dokumen.no_bukti} = {sisa}"
        )
    else:
        logger.info(f"KasIdentifikasi cancelled: {obj.no_bukti}")
    db.flush()
    return obj


def get_identifikasi_by_id(db: Session, identifikasi_id: UUID):
    return (
        db.query(KasIdentifikasi)
        .options(
            joinedload(KasIdentifikasi.rincian).joinedload(KasIdentifikasiRincian.akun_perkiraan),
            joinedload(KasIdentifikasi.jurnal),
            joinedload(KasIdentifikasi.creator),
        )
        .filter(KasIdentifikasi.id == identifikasi_id)
        .first()
    )


# ==========================================
# UPDATE #03 — MONITORING & REKONSILIASI PENAMPUNG
# ==========================================

def _saldo_gl_akun(db: Session, akun_ids: list) -> dict:
    """Σ (debit − kredit) jurnal POSTED per akun (pola sync_kas_bank_saldo)."""
    if not akun_ids:
        return {}
    from app.models.detail.jurnal_detail import JurnalDetail
    rows = (
        db.query(
            JurnalDetail.akun_perkiraan_id,
            func.coalesce(func.sum(JurnalDetail.debit), 0)
            - func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalUmum.status == "POSTED",
            JurnalDetail.akun_perkiraan_id.in_(akun_ids),
        )
        .group_by(JurnalDetail.akun_perkiraan_id)
        .all()
    )
    return {r[0]: Decimal(str(r[1] or 0)) for r in rows}


def get_penampung_monitoring(db: Session) -> dict:
    """Saldo tiap akun penampung + rekonsiliasi subledger vs General Ledger.

    Sumber akun yang dipantau:
    - setting KAS_PENAMPUNG_PENERIMAAN / KAS_PENAMPUNG_PEMBAYARAN /
      KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI (legacy);
    - akun aktual rincian dokumen BELUM aktif (dokumen lama bisa memakai
      akun penampung yang sudah tidak jadi setting).

    Cek rekonsiliasi (catatan-update-03):
    1. per akun: saldo GL == Σ sisa dokumen BELUM aktif yang memakai akun itu;
    2. semua jurnal POSTED balance (Σ debit == Σ kredit);
    3. jurnal identifikasi tidak pernah menyentuh akun kas/bank;
    4. tidak ada invoice dengan outstanding negatif.
    """
    akun_meta: dict = {}
    for key, arah in (
        (KEY_KAS_PENAMPUNG_PENERIMAAN, "PENERIMAAN"),
        (KEY_KAS_PENAMPUNG_PEMBAYARAN, "PEMBAYARAN"),
        (KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI, "CAMPUR"),
    ):
        akun_id = get_setting_akun_id_optional(db, key)
        if akun_id is not None:
            akun_meta.setdefault(akun_id, {"setting_key": key, "arah": arah})

    # Dokumen BELUM aktif + progres identifikasi per dokumen.
    # Sisa dihitung PER ARAH (penerimaan = saldo kredit; pembayaran = saldo
    # debit). Satu akun penampung legacy bisa dipakai dua arah (update #02):
    # saldo GL-nya = sisa_pembayaran − sisa_penerimaan (net).
    per_akun: dict = {}  # akun_id -> {dokumen, sisa_penerimaan, sisa_pembayaran}
    for model, jenis_doc in (
        (PenerimaanKas, "penerimaan"),
        (PembayaranKas, "pembayaran"),
    ):
        docs = (
            db.query(model)
            .options(joinedload(model.rincian))
            .filter(
                model.jenis_transaksi == JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI,
                model.status == StatusTransaksi.SELESAI,
            )
            .all()
        )
        if not docs:
            continue
        col = _identifikasi_col(jenis_doc)
        terid_rows = (
            db.query(col, func.coalesce(func.sum(KasIdentifikasi.total_nilai), 0))
            .filter(
                col.in_([d.id for d in docs]),
                KasIdentifikasi.status != StatusTransaksi.BATAL,
            )
            .group_by(col)
            .all()
        )
        terid = {r[0]: Decimal(str(r[1] or 0)) for r in terid_rows}
        for d in docs:
            rinc = list(getattr(d, "rincian", None) or [])
            akun_id = rinc[0].akun_perkiraan_id if rinc else None
            if akun_id is None:
                continue
            bucket = per_akun.setdefault(
                akun_id, {"dokumen": 0, "sisa_penerimaan": ZERO, "sisa_pembayaran": ZERO}
            )
            sisa = max(ZERO, Decimal(str(d.total_nilai or 0)) - terid.get(d.id, ZERO))
            bucket["dokumen"] += 1
            if jenis_doc == "penerimaan":
                bucket["sisa_penerimaan"] += sisa
            else:
                bucket["sisa_pembayaran"] += sisa
            if akun_id not in akun_meta:
                akun_meta[akun_id] = {"setting_key": None, "arah": None}

    akun_ids = list(akun_meta.keys())
    saldo_gl = _saldo_gl_akun(db, akun_ids)

    hasil_akun = []
    for akun_id in akun_ids:
        akun = db.get(AkunPerkiraan, akun_id)
        if akun is None:
            continue
        meta = akun_meta[akun_id] or {}
        bucket = per_akun.get(
            akun_id, {"dokumen": 0, "sisa_penerimaan": ZERO, "sisa_pembayaran": ZERO}
        )
        arah = meta.get("arah")
        if arah in (None, "CAMPUR"):
            arah_dok = []
            if bucket["sisa_penerimaan"] > ZERO or arah is None:
                arah_dok.append("PENERIMAAN")
            if bucket["sisa_pembayaran"] > ZERO or arah is None:
                arah_dok.append("PEMBAYARAN")
            arah = " + ".join(arah_dok) or "TIDAK ADA DOKUMEN"
        saldo = saldo_gl.get(akun_id, ZERO)
        sisa_penerimaan = bucket["sisa_penerimaan"]
        sisa_pembayaran = bucket["sisa_pembayaran"]
        # Saldo GL dibaca debit-normal. Sisa penerimaan bersaldo kredit
        # (sumber belum diketahui — kewajiban), sisa pembayaran bersaldo
        # debit (tujuan belum diketahui — aset). Akun campuran: keduanya
        # net di GL.
        saldo_harusnya = sisa_pembayaran - sisa_penerimaan
        selisih = saldo - saldo_harusnya
        hasil_akun.append({
            "akun": {"id": akun.id, "kode": akun.kode, "nama": akun.nama},
            "setting_key": meta.get("setting_key"),
            "arah": arah,
            "saldo_gl": saldo,
            "jumlah_dokumen_aktif": bucket["dokumen"],
            "sisa_penerimaan": sisa_penerimaan,
            "sisa_pembayaran": sisa_pembayaran,
            "total_sisa_dokumen": sisa_penerimaan + sisa_pembayaran,
            "selisih": selisih,
            "konsisten": abs(selisih) <= TOLERANSI,
        })

    # ---- Cek rekonsiliasi ----
    checks = []
    for info in hasil_akun:
        checks.append({
            "kode": f"SALDO_{info['akun']['kode']}",
            "label": (
                f"Saldo GL {info['akun']['kode']} ({info['arah']}) = "
                f"Σ sisa pembayaran − Σ sisa penerimaan dokumen "
                f"Belum Teridentifikasi"
            ),
            "ok": info["konsisten"],
            "detail": (
                f"saldo GL {info['saldo_gl']} vs sisa pembayaran "
                f"{info['sisa_pembayaran']} − sisa penerimaan "
                f"{info['sisa_penerimaan']} (selisih {info['selisih']})"
            ),
        })

    jurnal_tak_balance = (
        db.query(JurnalUmum.id)
        .filter(
            JurnalUmum.status == "POSTED",
            func.abs(JurnalUmum.total_debit - JurnalUmum.total_kredit) > TOLERANSI,
        )
        .count()
    )
    checks.append({
        "kode": "JURNAL_BALANCE",
        "label": "Semua jurnal POSTED seimbang (Σ debit = Σ kredit)",
        "ok": jurnal_tak_balance == 0,
        "detail": f"{jurnal_tak_balance} jurnal tidak balance",
    })

    # Jurnal identifikasi tidak boleh menyentuh akun kas/bank (tidak ada
    # jurnal bank ganda). Termasuk jurnal pembaliknya (REVERSAL dari jurnal
    # identifikasi) — dicek lewat keterkaitan ref/keterangan sederhana:
    # semua jurnal bertipe IDENTIFIKASI* diperiksa baris detail-nya.
    from app.models.detail.jurnal_detail import JurnalDetail
    kas_akun_ids = [r[0] for r in db.query(KasBankAkun.akun_perkiraan_id).all()]
    pelanggar = 0
    if kas_akun_ids:
        pelanggar = (
            db.query(JurnalDetail.id)
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                JurnalUmum.tipe_transaksi.like("IDENTIFIKASI%"),
                JurnalDetail.akun_perkiraan_id.in_(kas_akun_ids),
            )
            .count()
        )
    checks.append({
        "kode": "IDENTIFIKASI_TANPA_KAS",
        "label": "Jurnal identifikasi tidak menyentuh akun Kas/Bank (tanpa jurnal bank ganda)",
        "ok": pelanggar == 0,
        "detail": f"{pelanggar} baris jurnal identifikasi menyentuh akun kas/bank",
    })

    # Outstanding invoice tidak negatif (Σ alokasi dok aktif + retur ≤ total).
    from app.services import settlement_service
    from app.models import SalesInvoice, PurchaseInvoice, SalesRetur, PurchaseRetur
    negatif = 0
    for inv_model, ar, retur_model, retur_fk in (
        (SalesInvoice, True, SalesRetur, SalesRetur.sales_invoice_id),
        (PurchaseInvoice, False, PurchaseRetur, PurchaseRetur.purchase_invoice_id),
    ):
        inv_fk = PaymentAllocation.sales_invoice_id if ar else PaymentAllocation.purchase_invoice_id
        pay_fk = PaymentAllocation.penerimaan_id if ar else PaymentAllocation.pembayaran_id
        pay_model = PenerimaanKas if ar else PembayaranKas
        aktif_pay = settlement_service.active_documents(db, pay_model).scalar_subquery()
        rows = (
            db.query(inv_model.id, inv_model.grand_total, func.coalesce(func.sum(PaymentAllocation.nilai), 0))
            .join(PaymentAllocation, inv_fk == inv_model.id)
            .filter(pay_fk.in_(aktif_pay))
            .group_by(inv_model.id, inv_model.grand_total)
            .all()
        )
        for inv_id, grand_total, paid in rows:
            retur = (
                db.query(func.coalesce(func.sum(retur_model.grand_total), 0))
                .filter(retur_fk == inv_id, retur_model.id.in_(
                    settlement_service.active_documents(db, retur_model).scalar_subquery()))
                .scalar()
            )
            if Decimal(str(grand_total)) - Decimal(str(paid)) - Decimal(str(retur or 0)) < -TOLERANSI:
                negatif += 1
    checks.append({
        "kode": "OUTSTANDING_TIDAK_NEGATIF",
        "label": "Tidak ada invoice dengan outstanding negatif (alokasi melebihi tagihan)",
        "ok": negatif == 0,
        "detail": f"{negatif} invoice outstanding negatif",
    })

    total_sisa = sum((i["total_sisa_dokumen"] for i in hasil_akun), ZERO)
    return {
        "akun": hasil_akun,
        "total_sisa": total_sisa,
        "rekonsiliasi_ok": all(c["ok"] for c in checks),
        "checks": checks,
    }
