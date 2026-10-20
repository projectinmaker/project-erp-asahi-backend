"""Update #02 — Identifikasi & Alokasi transaksi kas Belum Teridentifikasi.

Dokumen kas (penerimaan/pembayaran) dengan jenis_transaksi =
BELUM_TERIDENTIFIKASI diposting saat dibuat ke akun penampung sementara
(setting KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI; default COA 216000). Begitu
asal mutasi diketahui (pelanggan, supplier, pendapatan, beban, transfer,
dll), user menjalankan Identifikasi & Alokasi: nilai dipindahkan dari akun
penampung ke akun-akun yang benar — bisa beberapa baris sekaligus maupun
bertahap — melalui SATU jurnal reklasifikasi per sesi identifikasi:

    Penerimaan:  Dr Penampung (total)  |  Cr Akun tujuan (per baris alokasi)
    Pembayaran:   Dr Akun tujuan (per baris alokasi)  |  Cr Penampung (total)

Sisi Kas/Bank TIDAK dijurnal ulang — tidak ada jurnal bank ganda; saldo
bank tetap konsisten dengan mutasi aslinya.

Semua jurnal dibuat lewat posting engine yang sama dengan modul lain
(``auto_posting_jurnal``): validasi debit-kredit balance, akun DETAIL +
aktif + postable, periode tidak ditutup, sync saldo kas/bank, dan dedup
(ref_module + ref_id + tipe_transaksi IDENTIFIKASI_KAS unik per sesi).
Pembatalan identifikasi = ``reverse_journal`` (jurnal pembalik berantai
``reversal_of_id`` — audit trail utuh, jurnal asli tetap POSTED).
Akun penampung aktual diambil dari baris rincian dokumen (dibekukan saat
create) — bukan setting saat ini — supaya jurnal identifikasi selalu
membalik akun yang benar-benar tersentuh jurnal kas awal.
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
from app.models.transaksi.jurnal import JurnalUmum, RefModule
from app.models.transaksi.kas_bank.kas_identifikasi import KasIdentifikasi, KasIdentifikasiRincian
from app.models.transaksi.kas_bank.penerimaan import PenerimaanKas
from app.models.transaksi.kas_bank.pembayaran import (
    PembayaranKas, StatusTransaksi, JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI,
)
from app.services.accounting_control import atomic_accounting_write
from app.services.posting_service import auto_posting_jurnal, reverse_journal, JurnalEntryItem
from app.utils.nomor_dokumen import get_nomor_dokumen_tahunan

ZERO = Decimal("0.00")
JAKARTA = timezone(timedelta(hours=7))
TIPE_TRANSAKSI_IDENTIFIKASI = "IDENTIFIKASI_KAS"


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
    return akun


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
    """Buat sesi Identifikasi & Alokasi + jurnal reklasifikasi (langsung POSTED).

    Pola mengikuti reverse_transfer: aksi langsung tanpa workflow multi-langkah
    (audit trail = jurnal + reversal chain + created_by; reversal tersedia
    via cancel_identifikasi). Jurnal hanya menyentuh penampung ↔ akun tujuan.
    """
    dokumen = _get_dokumen_for_update(db, jenis_doc, dokumen_id)
    jenis = (getattr(dokumen, "jenis_transaksi", None) or "").upper()
    if jenis != JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI:
        raise ValueError(
            "Identifikasi & Alokasi hanya untuk dokumen jenis 'Belum Teridentifikasi'"
        )
    if getattr(dokumen, "status", None) == StatusTransaksi.BATAL:
        raise ValueError("Dokumen sudah dibatalkan")
    if not getattr(dokumen, "jurnal_umum_id", None) or dokumen.status != StatusTransaksi.SELESAI:
        raise ValueError("Dokumen belum diposting — identifikasi hanya untuk dokumen posted")
    if not alokasi_data or len(alokasi_data) > 100:
        raise ValueError("Isi 1 sampai 100 baris alokasi identifikasi")
    if _local_day(tanggal) < _local_day(dokumen.tanggal):
        raise ValueError("Tanggal identifikasi tidak boleh sebelum tanggal dokumen kas")

    penampung_id = _resolve_penampung_dokumen(dokumen)
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

    # Snapshot dimensi organisasi dari jurnal dokumen sumber (konsisten dengan
    # reverse_journal yang menyalin org dims jurnal asal).
    sumber = db.get(JurnalUmum, dokumen.jurnal_umum_id)
    organization = (
        {f: getattr(sumber, f) for f in
         ("company_id", "branch_id", "department_id", "cost_center_id", "project_id")}
        if sumber is not None else {}
    )
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
        organization=organization,
    )
    identifikasi.jurnal_umum_id = jurnal.id
    identifikasi.status = StatusTransaksi.SELESAI
    db.flush()
    logger.info(
        f"KasIdentifikasi created: {no_bukti} | {label_dokumen} {dokumen.no_bukti} "
        f"| total={total} | {len(baris)} baris alokasi"
    )
    return identifikasi


@atomic_accounting_write
def cancel_identifikasi(db: Session, identifikasi_id: UUID, user_id: UUID = None, reason: str = None):
    """Batalkan sesi identifikasi: jurnal pembalik (reversal) + status BATAL.

    Jurnal asli tetap POSTED (audit trail); saldo akun kembali ke keadaan
    sebelum identifikasi. Sisa belum teridentifikasi dokumen otomatis naik
    kembali (Σ identifikasi aktif berkurang).
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
    logger.info(f"KasIdentifikasi cancelled: {obj.no_bukti}")
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
