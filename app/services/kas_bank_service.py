"""
kas_bank_service.py

Service layer untuk modul Kas & Bank.
Menghandle CRUD + auto-posting jurnal untuk:
- PembayaranKas (+ PembayaranRincian)
- PenerimaanKas (+ PenerimaanRincian)
- TransferBank
"""

from app.services.accounting_control import atomic_accounting_write, require_unposted, require_no_stock_movement
from app.services.posting_service import reverse_journal

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.akun_perkiraan import AkunPerkiraan
from app.models.transaksi.kas_bank.pembayaran import (
    PembayaranKas, StatusTransaksi,
    JENIS_TRANSAKSI_PELUNASAN_INVOICE, JENIS_TRANSAKSI_LAINNYA,
    JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI, JENIS_TRANSAKSI_KAS,
    JENIS_TRANSAKSI_PELUNASAN_PAJAK,
)
from app.models.transaksi.kas_bank.penerimaan import PenerimaanKas
from app.models.transaksi.kas_bank.transfer_bank import TransferBank
from app.models.detail.pembayaran_rincian import PembayaranRincian
from app.models.detail.penerimaan_rincian import PenerimaanRincian
from app.models.master.kas_bank_akun import KasBankAkun
from app.models.master.pengguna import Pengguna
from app.models.transaksi.payment_allocation import PaymentAllocation
from app.models.transaksi.jurnal import RefModule
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.services.setting_akun_service import (
    get_akun_id_or_raise, get_setting_akun_id_optional,
    KEY_BEBAN_TRANSFER_BANK, KEY_POTONGAN_HUTANG,
    KEY_HUTANG_PPH23, KEY_PPH23_DIBAYAR_DIMUKA,
    KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI,
    KEY_KAS_PENAMPUNG_PENERIMAAN, KEY_KAS_PENAMPUNG_PEMBAYARAN,
)
from app.utils.nomor_dokumen import get_nomor_dokumen, get_nomor_dokumen_tahunan


# ==========================================
# UPDATE #02 — JENIS TRANSAKSI KAS & BANK
# ==========================================
# Guard Transaksi Lainnya: akun piutang/hutang (kontrol AR/AP maupun
# subledger per pelanggan/supplier) hanya boleh tersentuh lewat alur
# Pelunasan Invoice (dengan alokasi invoice) — bukan lewat Transaksi
# Lainnya tanpa alokasi (koreksi miskom catatan-update-01).
AR_AP_FORBIDDEN_SYSTEM_TYPES = {"AR_CONTROL", "AP_CONTROL"}
AR_AP_FORBIDDEN_SUBCLASSES = {"ACCOUNTS_RECEIVABLE", "ACCOUNTS_PAYABLE"}


def normalize_jenis_transaksi(jenis_transaksi, is_settlement: bool = False) -> str:
    """Update #02 — resolusi jenis transaksi dokumen kas.

    - ``is_settlement=True`` (alur pelunasan /pelunasan/{piutang|hutang})
      SELALU menghasilkan PELUNASAN_INVOICE — akun piutang/hutang hanya
      boleh tersentuh lewat alokasi invoice.
    - ``None``/kosong → TRANSAKSI_LAINNYA (perilaku lama/backward compat).
    - PELUNASAN_INVOICE eksplisit lewat payload umum → ditolak dengan
      pesan mengarah ke alur Pelunasan Invoice.
    """
    if is_settlement:
        return JENIS_TRANSAKSI_PELUNASAN_INVOICE
    jenis = (jenis_transaksi or "").strip().upper() or JENIS_TRANSAKSI_LAINNYA
    if jenis == JENIS_TRANSAKSI_PELUNASAN_INVOICE:
        raise ValueError(
            "Pelunasan invoice harus dicatat lewat jenis transaksi 'Pelunasan Invoice' "
            "(pilih pelanggan/supplier → invoice outstanding → nominal → posting), "
            "bukan lewat payload penerimaan/pembayaran umum"
        )
    if jenis not in JENIS_TRANSAKSI_KAS:
        raise ValueError(
            f"Jenis transaksi tidak dikenal: {jenis_transaksi} "
            f"(pilihan: {', '.join(JENIS_TRANSAKSI_KAS)})"
        )
    return jenis


def _validate_akun_lawan_bukan_piutang_hutang(db: Session, akun_id) -> AkunPerkiraan:
    """Update #02 — Transaksi Lainnya dilarang memakai akun piutang/hutang.

    Mencakup akun kontrol AR/AP (system_account_type AR_CONTROL/AP_CONTROL
    atau account_subclass ACCOUNTS_RECEIVABLE/ACCOUNTS_PAYABLE) maupun
    subledger piutang/hutang per pelanggan/supplier (is_subledger).
    """
    akun = db.get(AkunPerkiraan, akun_id)
    if akun is None:
        raise ValueError("Akun rincian tidak ditemukan")
    system_type = (akun.system_account_type or "").upper()
    subclass = (akun.account_subclass or "").upper()
    if (system_type in AR_AP_FORBIDDEN_SYSTEM_TYPES
            or subclass in AR_AP_FORBIDDEN_SUBCLASSES
            or akun.is_subledger):
        raise ValueError(
            f"Akun {akun.kode} ({akun.nama}) adalah akun piutang/hutang. "
            "Pelunasan invoice tidak boleh dicatat lewat Transaksi Lainnya "
            "tanpa alokasi invoice — gunakan jenis transaksi 'Pelunasan Invoice' "
            "dan pilih invoice yang dilunasi."
        )
    return akun


# === AUD-11 (update #04) — guard akun hutang pajak ===
def _validate_akun_lawan_pajak(db: Session, jenis: str, akun_id) -> AkunPerkiraan:
    """Validasi akun lawan untuk jenis PELUNASAN_PAJAK & arah sebaliknya.

    - PELUNASAN_PAJAK: akun lawan WAJIB akun hutang pajak
      (system_account_type = TAX_PAYABLE, mis. 213001 PPh 21, 213002 PPh 23,
      213003 PPh 29, 213004 PPh Final).
    - TRANSAKSI_LAINNYA: akun hutang pajak DITOLAK — pembayaran pajak harus
      lewat jenis 'Pelunasan Pajak' supaya terlacak di monitoring hutang
      pajak (temuan audit 7-b: pelunasan pajak dulu hanya lewat Transaksi
      Lainnya manual tanpa alur/monitoring khusus).
    """
    akun = db.get(AkunPerkiraan, akun_id)
    if akun is None:
        raise ValueError("Akun rincian tidak ditemukan")
    system_type = (akun.system_account_type or "").upper()
    is_tax_payable = system_type == "TAX_PAYABLE"
    if jenis == JENIS_TRANSAKSI_PELUNASAN_PAJAK and not is_tax_payable:
        raise ValueError(
            f"Akun {akun.kode} ({akun.nama}) bukan akun hutang pajak. "
            "Jenis 'Pelunasan Pajak' hanya boleh membayar akun hutang pajak "
            "(system_account_type TAX_PAYABLE, mis. PPh 21/23/29/Final)."
        )
    if jenis == JENIS_TRANSAKSI_LAINNYA and is_tax_payable:
        raise ValueError(
            f"Akun {akun.kode} ({akun.nama}) adalah akun hutang pajak. "
            "Pembayaran pajak harus lewat jenis transaksi 'Pelunasan Pajak' "
            "supaya terlacak di monitoring hutang pajak."
        )
    return akun


# === AUD-11 (update #04) — guard akun hutang pajak ===
def _validate_akun_lawan_pajak(db: Session, jenis: str, akun_id) -> AkunPerkiraan:
    """Validasi akun lawan untuk jenis PELUNASAN_PAJAK & arah sebaliknya.

    - PELUNASAN_PAJAK: akun lawan WAJIB akun hutang pajak
      (system_account_type = TAX_PAYABLE, mis. 213001 PPh 21, 213002 PPh 23,
      213003 PPh 29, 213004 PPh Final).
    - TRANSAKSI_LAINNYA: akun hutang pajak DITOLAK — pembayaran pajak harus
      lewat jenis 'Pelunasan Pajak' supaya terlacak di monitoring hutang
      pajak (temuan audit 7-b #5: pelunasan pajak dulu hanya lewat Transaksi
      Lainnya manual tanpa alur/monitoring khusus).
    """
    akun = db.get(AkunPerkiraan, akun_id)
    if akun is None:
        raise ValueError("Akun rincian tidak ditemukan")
    system_type = (akun.system_account_type or "").upper()
    is_tax_payable = system_type == "TAX_PAYABLE"
    if jenis == JENIS_TRANSAKSI_PELUNASAN_PAJAK and not is_tax_payable:
        raise ValueError(
            f"Akun {akun.kode} ({akun.nama}) bukan akun hutang pajak. "
            "Jenis 'Pelunasan Pajak' hanya boleh membayar akun hutang pajak "
            "(system_account_type TAX_PAYABLE, mis. PPh 21/23/29/Final)."
        )
    if jenis == JENIS_TRANSAKSI_LAINNYA and is_tax_payable:
        raise ValueError(
            f"Akun {akun.kode} ({akun.nama}) adalah akun hutang pajak. "
            "Pembayaran pajak harus lewat jenis transaksi 'Pelunasan Pajak' "
            "supaya terlacak di monitoring hutang pajak."
        )
    return akun


def _key_penampung(jenis_doc: str) -> str:
    """Update #03 — setting akun penampung arah-spesifik.

    Penerimaan (kas masuk tidak diketahui asalnya) → liabilitas
    KAS_PENAMPUNG_PENERIMAAN (default 216001); pembayaran (kas keluar tidak
    diketahui tujuannya) → aset KAS_PENAMPUNG_PEMBAYARAN (default 117004).
    Keduanya terpisah dari akun piutang/hutang operasional sehingga neraca
    menyajikan kontrol sementara dengan benar (catatan-update-03).
    Key gabungan Update #02 (216000) tetap dipakai sebagai fallback bila
    setting arah-spesifik belum terkonfigurasi.
    """
    if jenis_doc.startswith("penerimaan"):
        return KEY_KAS_PENAMPUNG_PENERIMAAN
    return KEY_KAS_PENAMPUNG_PEMBAYARAN


def _resolve_rincian_belum_teridentifikasi(db: Session, nilai, jenis_doc: str) -> list:
    """Update #02/#03 — rincian dokumen Belum Teridentifikasi: tepat satu
    baris ke akun penampung arah-spesifik (penerimaan → liabilitas
    KAS_PENAMPUNG_PENERIMAAN; pembayaran → aset KAS_PENAMPUNG_PEMBAYARAN;
    fallback key gabungan Update #02 bila setting arah-spesifik kosong).

    User hanya memasukkan satu nilai total; akun lawan dipilih sistem dari
    konfigurasi per perusahaan (Pengaturan → Setting Akun) — bukan dari COA
    bebas — supaya mutasi tidak teridentifikasi selalu terkumpul di akun
    penampung yang bisa direkonsiliasi.
    """
    if nilai is None:
        raise ValueError("Nilai wajib diisi untuk jenis transaksi 'Belum Teridentifikasi'")
    nilai = Decimal(str(nilai))
    if not nilai.is_finite() or nilai <= 0:
        raise ValueError("Nilai harus positif untuk jenis transaksi 'Belum Teridentifikasi'")
    from app.services.setting_akun_service import get_setting_akun_id_optional  # noqa: F811
    akun_id = get_setting_akun_id_optional(db, _key_penampung(jenis_doc))
    if akun_id is None:
        akun_id = get_akun_id_or_raise(
            db, KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI, "Belum Teridentifikasi"
        )
    return [{"akun_perkiraan_id": akun_id, "nilai": nilai}]


def _patch_jenis_transaksi_dan_nilai(db: Session, db_obj, jenis_transaksi, nilai, rincian_model, fk_attr: str) -> None:
    """Update #02 — patch jenis transaksi (immutable) & nilai Belum Teridentifikasi.

    Dipakai update_penerimaan/update_pembayaran. ``jenis_transaksi`` hanya
    boleh dikirim sama dengan nilai tersimpan (guard perubahan jenis);
    ``nilai`` hanya berlaku untuk dokumen BELUM_TERIDENTIFIKASI — rebuild
    rincian menjadi 1 baris akun penampung (sumber akun mengikuti setting
    saat update; dokumen belum posted jadi jurnal belum tersentuh).
    """
    if jenis_transaksi is not None:
        jenis_baru = (jenis_transaksi or "").strip().upper()
        jenis_lama = (getattr(db_obj, "jenis_transaksi", None) or JENIS_TRANSAKSI_LAINNYA).upper()
        if jenis_baru != jenis_lama:
            raise ValueError(
                "Jenis transaksi tidak dapat diubah setelah dokumen dibuat — "
                "batalkan dokumen ini dan buat dokumen baru dengan jenis yang tepat"
            )
    if nilai is not None:
        jenis_lama = (getattr(db_obj, "jenis_transaksi", None) or JENIS_TRANSAKSI_LAINNYA)
        if jenis_lama != JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI:
            raise ValueError(
                "Field nilai hanya berlaku untuk dokumen 'Belum Teridentifikasi'; "
                "dokumen lain mengubah nilai lewat rincian (Transaksi Lainnya) "
                "atau alokasi invoice (Pelunasan Invoice)"
            )
        nilai_dec = Decimal(str(nilai))
        if not nilai_dec.is_finite() or nilai_dec <= 0:
            raise ValueError("Nilai harus positif")
        # Update #03 — akun penampung arah-spesifik (penerimaan vs pembayaran);
        # fallback key gabungan Update #02 bila setting arah-spesifik kosong.
        jenis_doc = "penerimaan" if fk_attr == "penerimaan_id" else "pembayaran"
        akun_id = get_setting_akun_id_optional(db, _key_penampung(jenis_doc))
        if akun_id is None:
            akun_id = get_akun_id_or_raise(
                db, KEY_KAS_PENAMPUNG_BELUM_TERIDENTIFIKASI, "Belum Teridentifikasi"
            )
        db_obj.rincian.clear()
        db.flush()  # lepas baris lama sebelum insert baru
        row = rincian_model(**{fk_attr: db_obj.id, "akun_perkiraan_id": akun_id, "nilai": nilai_dec})
        db_obj.rincian.append(row)
        db.flush()


def _annotate_sisa_belum_teridentifikasi(db: Session, data: list, model) -> None:
    """Update #02 — atribut transient sisa/teridentifikasi utk list/detail.

    Hanya dokumen BELUM_TERIDENTIFIKASI yang dianotasi (dokumen lain tidak
    berubah). Nilai = total_nilai − Σ identifikasi aktif (status != BATAL).
    Atribut transient (bukan kolom) dibaca schema response via
    from_attributes — tidak persisten.
    """
    from app.models.transaksi.kas_bank.kas_identifikasi import KasIdentifikasi
    target = [d for d in data if getattr(d, "jenis_transaksi", None) == JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI]
    if not target:
        return
    col = KasIdentifikasi.penerimaan_id if model is PenerimaanKas else KasIdentifikasi.pembayaran_id
    rows = db.query(col, func.coalesce(func.sum(KasIdentifikasi.total_nilai), 0)).filter(
        col.in_([d.id for d in target]),
        KasIdentifikasi.status != StatusTransaksi.BATAL,
    ).group_by(col).all()
    done = {r[0]: Decimal(str(r[1] or 0)) for r in rows}
    for d in target:
        total = Decimal(str(d.total_nilai or 0))
        teridentifikasi = done.get(d.id, Decimal("0"))
        d.teridentifikasi = teridentifikasi
        d.sisa_belum_teridentifikasi = max(Decimal("0"), total - teridentifikasi)


def rincian(doc) -> list:
    """Akses aman relasi rincian dokumen kas (kosong bila belum ter-load)."""
    return list(getattr(doc, "rincian", None) or [])


def _guard_post_jenis_transaksi(db: Session, doc, rincian_rows) -> None:
    """Update #02 — validasi jenis transaksi saat posting (defense in depth).

    - TRANSAKSI_LAINNYA: akun rincian tidak boleh piutang/hutang (guard yang
      sama dengan create — juga melindungi draft lama yang dibuat sebelum
      validasi jenis transaksi ada).
    - BELUM_TERIDENTIFIKASI: rincian harus tepat satu baris akun penampung.
    - PELUNASAN_INVOICE: invariant alokasi sudah divalidasi
      settlement_service.validate_payment (dipanggil sebelum guard ini).
    """
    jenis = (getattr(doc, "jenis_transaksi", None) or JENIS_TRANSAKSI_LAINNYA)
    if jenis == JENIS_TRANSAKSI_LAINNYA:
        for r in rincian_rows:
            _validate_akun_lawan_bukan_piutang_hutang(db, r.akun_perkiraan_id)
            _validate_akun_lawan_pajak(db, jenis, r.akun_perkiraan_id)  # AUD-11
    elif jenis == JENIS_TRANSAKSI_PELUNASAN_PAJAK:
        # AUD-11 — semua akun rincian harus akun hutang pajak (TAX_PAYABLE)
        for r in rincian_rows:
            _validate_akun_lawan_pajak(db, jenis, r.akun_perkiraan_id)
    elif jenis == JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI:
        if len(rincian_rows) != 1:
            raise ValueError(
                "Dokumen 'Belum Teridentifikasi' harus punya tepat satu baris "
                "rincian akun penampung"
            )


# ==========================================
# PEMBAYARAN KAS
# ==========================================

def get_pembayaran_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    kas_bank_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
    jenis_transaksi: Optional[str] = None,
) -> Tuple[List[PembayaranKas], int]:
    """Ambil daftar pembayaran kas dengan filter & pagination.

    Update #02 — filter ``jenis_transaksi`` (PELUNASAN_INVOICE /
    TRANSAKSI_LAINNYA / BELUM_TERIDENTIFIKASI) + anotasi sisa belum
    teridentifikasi pada dokumen BELUM_TERIDENTIFIKASI.
    """
    query = db.query(PembayaranKas).options(
        joinedload(PembayaranKas.kas_bank).joinedload(KasBankAkun.akun_perkiraan),
        joinedload(PembayaranKas.creator),
        joinedload(PembayaranKas.rincian).joinedload(PembayaranRincian.akun_perkiraan),
        joinedload(PembayaranKas.jurnal),
        # Update cetak "Pembayaran Lain" — supaya list bisa membedakan dokumen
        # settlement (Bayar Pemasok) vs pembayaran biasa (Pembayaran Lain):
        # relasi supplier + alokasi di-eager-load (schema response sudah punya
        # field-nya; tanpa ini list selalu tampak "tanpa alokasi").
        joinedload(PembayaranKas.supplier),
        joinedload(PembayaranKas.alokasi),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PembayaranKas.no_bukti.ilike(pattern)
            | PembayaranKas.penerima.ilike(pattern)
            | PembayaranKas.no_nukti.ilike(pattern)
        )

    if status:
        query = query.filter(PembayaranKas.status == status)
    if kas_bank_id:
        query = query.filter(PembayaranKas.kas_bank_id == kas_bank_id)
    if tanggal_from:
        query = query.filter(PembayaranKas.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PembayaranKas.tanggal <= tanggal_to)
    # === Update #02 — filter jenis transaksi ===
    if jenis_transaksi:
        query = query.filter(PembayaranKas.jenis_transaksi == jenis_transaksi.strip().upper())

    total = query.count()
    data = query.order_by(PembayaranKas.created_at.desc()).offset(skip).limit(limit).all()
    _annotate_sisa_belum_teridentifikasi(db, data, PembayaranKas)
    return data, total


def get_pembayaran_by_id(db: Session, pembayaran_id: UUID) -> Optional[PembayaranKas]:
    """Ambil 1 pembayaran berdasarkan ID dengan rincian (Update #02: anotasi
    sisa belum teridentifikasi bila dokumen BELUM_TERIDENTIFIKASI)."""
    item = (
        db.query(PembayaranKas)
        .options(
            joinedload(PembayaranKas.kas_bank).joinedload(KasBankAkun.akun_perkiraan),
            joinedload(PembayaranKas.creator),
            joinedload(PembayaranKas.supplier),
            joinedload(PembayaranKas.rincian).joinedload(PembayaranRincian.akun_perkiraan),
            joinedload(PembayaranKas.alokasi).joinedload(PaymentAllocation.purchase_invoice),
            joinedload(PembayaranKas.jurnal),
        )
        .filter(PembayaranKas.id == pembayaran_id)
        .first()
    )
    if item is not None:
        _annotate_sisa_belum_teridentifikasi(db, [item], PembayaranKas)
    return item


@atomic_accounting_write
def create_pembayaran(
    db: Session,
    no_nukti: str,
    tanggal: datetime,
    kas_bank_id: UUID,
    rincian_data: list,
    no_cek: Optional[str] = None,
    penerima: Optional[str] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: UUID = None,
    is_settlement: bool = False,
    tanggal_cek: Optional[date] = None,
    jumlah_cek: Optional[Decimal] = None,
    mata_uang: Optional[str] = None,
    nilai_tukar: Optional[Decimal] = None,
    jenis_transaksi: Optional[str] = None,
    nilai: Optional[Decimal] = None,
) -> PembayaranKas:
    """
    Buat PembayaranKas baru beserta rincian.
    - Generate no_bukti otomatis
    - Hitung total_nilai dari sum rincian
    - Auto-post jurnal jika auto_post_jurnal=True dan status=SELESAI

    Parameter:
        is_settlement: True kalau ini adalah pelunasan AP (punya allocation
            ke purchase_invoice). Akan memakai RefModule.AP_SETTLEMENT
            (canonical) di jurnal + jenis_transaksi PELUNASAN_INVOICE.
        tanggal_cek / jumlah_cek / mata_uang / nilai_tukar:
            Update cetak "Bayar Pemasok" — info header tambahan untuk layout
            cetak (tgl cek, jumlah cek, mata uang, nilai tukar).
        jenis_transaksi / nilai: Update #02 — tiga jenis transaksi Kas & Bank.
            TRANSAKSI_LAINNYA (default; rincian akun lawan dari COA, dilarang
            akun piutang/hutang), BELUM_TERIDENTIFIKASI (hanya `nilai`;
            rincian otomatis 1 baris ke akun penampung). PELUNASAN_INVOICE
            ditolak di sini — harus lewat alur settlement.
    """
    try:
        # Phase G: validate kas_bank ACTIVE
        from app.services.cash_bank_validation import validate_active_kas_bank
        kas_bank = validate_active_kas_bank(db, kas_bank_id, "Pembayaran Kas")

        # === Update #02 — resolusi jenis transaksi + rincian ===
        jenis = normalize_jenis_transaksi(jenis_transaksi, is_settlement)
        if jenis == JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI:
            if rincian_data:
                raise ValueError(
                    "Jenis 'Belum Teridentifikasi' tidak memakai rincian akun lawan — "
                    "kirim nilai total saja; sistem otomatis posting ke akun penampung"
                )
            rincian_data = _resolve_rincian_belum_teridentifikasi(db, nilai, "pembayaran")
        elif not is_settlement:
            if not rincian_data:
                raise ValueError("Rincian akun lawan wajib diisi untuk Transaksi Lainnya/Pelunasan Pajak")
            for r in rincian_data:
                _validate_akun_lawan_bukan_piutang_hutang(db, r.get("akun_perkiraan_id"))
                # AUD-11 — guard arah akun pajak per jenis transaksi
                _validate_akun_lawan_pajak(db, jenis, r.get("akun_perkiraan_id"))

        # Generate nomor bukti (pembayaran/pengeluaran kas)
        # Update ASAHI: pola penomoran seragam "PAY ASI/YYYY/NNN" (reset tahunan).
        no_bukti = get_nomor_dokumen_tahunan(
            db, PembayaranKas, prefix="PAY",
            no_column="no_bukti", tanggal=tanggal.date()
        )

        # Hitung total dari rincian
        total_nilai = sum(Decimal(str(r.get("nilai", 0))) for r in rincian_data)

        # Update cetak "Bayar Pemasok" — normalisasi mata uang & nilai tukar
        mata_uang = (mata_uang or "IDR").strip().upper()[:8] or "IDR"
        nilai_tukar = Decimal(str(nilai_tukar)) if nilai_tukar is not None else Decimal("1")
        if nilai_tukar <= 0:
            raise ValueError("Nilai tukar harus lebih dari 0")
        if jumlah_cek is not None:
            jumlah_cek = Decimal(str(jumlah_cek))
            if jumlah_cek < 0:
                raise ValueError("Jumlah cek tidak boleh negatif")

        # Buat header
        pembayaran = PembayaranKas(
            no_bukti=no_bukti,
            tanggal=tanggal,
            kas_bank_id=kas_bank_id,
            no_nukti=no_nukti,
            no_cek=no_cek,
            tanggal_cek=tanggal_cek,
            jumlah_cek=jumlah_cek,
            mata_uang=mata_uang,
            nilai_tukar=nilai_tukar,
            penerima=penerima,
            catatan=catatan,
            total_nilai=total_nilai,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusTransaksi.SELESAI if auto_post_jurnal else StatusTransaksi.DRAFT,
            created_by=created_by,
            jenis_transaksi=jenis,  # Update #02
        )
        db.add(pembayaran)
        db.flush()  # Flush untuk dapat ID

        # Buat rincian
        for r in rincian_data:
            detail = PembayaranRincian(
                pembayaran_id=pembayaran.id,
                akun_perkiraan_id=r["akun_perkiraan_id"],
                nilai=Decimal(str(r["nilai"])),
            )
            db.add(detail)

        # Auto-post jurnal
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(pembayaran)
        if auto_post_jurnal:
            post_pembayaran(db, pembayaran, created_by, is_settlement=is_settlement)

        db.commit()
        db.refresh(pembayaran)
        logger.info(f"PembayaranKas created: {no_bukti} | total={total_nilai}")
        return pembayaran

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PembayaranKas: {e}")
        raise


@atomic_accounting_write
def update_pembayaran(
    db: Session,
    db_obj: PembayaranKas,
    tanggal: Optional[datetime] = None,
    kas_bank_id: Optional[UUID] = None,
    no_nukti: Optional[str] = None,
    no_cek: Optional[str] = None,
    penerima: Optional[str] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    tanggal_cek: Optional[date] = None,
    jumlah_cek: Optional[Decimal] = None,
    mata_uang: Optional[str] = None,
    nilai_tukar: Optional[Decimal] = None,
    jenis_transaksi: Optional[str] = None,
    nilai: Optional[Decimal] = None,
) -> PembayaranKas:
    """Update data pembayaran (hanya field yang diberikan).

    Update #02 — ``jenis_transaksi`` hanya boleh dikirim sama dengan jenis
    saat ini (tidak bisa berubah jenis; batalkan dokumen bila salah jenis).
    ``nilai`` khusus dokumen BELUM_TERIDENTIFIKASI (rebuild rincian 1 baris
    akun penampung) — dokumen lain mengubah lewat rincian/alokasi masing-masing.
    """
    require_unposted(db_obj)
    # === Update #02 — patch jenis transaksi / nilai ===
    _patch_jenis_transaksi_dan_nilai(
        db, db_obj, jenis_transaksi, nilai,
        rincian_model=PembayaranRincian, fk_attr="pembayaran_id",
    )
    if tanggal is not None:
        db_obj.tanggal = tanggal
    if kas_bank_id is not None:
        db_obj.kas_bank_id = kas_bank_id
    if no_nukti is not None:
        db_obj.no_nukti = no_nukti
    if no_cek is not None:
        db_obj.no_cek = no_cek
    # Update cetak "Bayar Pemasok" — info cek & mata uang
    if tanggal_cek is not None:
        db_obj.tanggal_cek = tanggal_cek
    if jumlah_cek is not None:
        db_obj.jumlah_cek = Decimal(str(jumlah_cek))
    if mata_uang is not None:
        db_obj.mata_uang = mata_uang.strip().upper()[:8] or "IDR"
    if nilai_tukar is not None:
        nilai_tukar = Decimal(str(nilai_tukar))
        if nilai_tukar <= 0:
            raise ValueError("Nilai tukar harus lebih dari 0")
        db_obj.nilai_tukar = nilai_tukar
    if penerima is not None:
        db_obj.penerima = penerima
    if catatan is not None:
        db_obj.catatan = catatan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_pembayaran(db: Session, db_obj: PembayaranKas, user_id: Optional[UUID] = None) -> PembayaranKas:
    """Batalkan pembayaran (status -> BATAL)."""
    if db_obj.status == StatusTransaksi.BATAL:
        raise ValueError("Pembayaran sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusTransaksi.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PembayaranKas cancelled: {db_obj.no_bukti}")
    return db_obj


# ==========================================
# PENERIMAAN KAS
# ==========================================

def get_penerimaan_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    kas_bank_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
    jenis_transaksi: Optional[str] = None,
) -> Tuple[List[PenerimaanKas], int]:
    """Ambil daftar penerimaan kas dengan filter & pagination.

    Update #02 — filter ``jenis_transaksi`` + anotasi sisa belum
    teridentifikasi pada dokumen BELUM_TERIDENTIFIKASI.
    """
    query = db.query(PenerimaanKas).options(
        joinedload(PenerimaanKas.kas_bank).joinedload(KasBankAkun.akun_perkiraan),
        joinedload(PenerimaanKas.creator),
        joinedload(PenerimaanKas.rincian).joinedload(PenerimaanRincian.akun_perkiraan),
        joinedload(PenerimaanKas.jurnal),
        # Update #01 — paritas dengan pembayaran: list Kas & Bank harus bisa
        # membedakan penerimaan settlement (pelunasan piutang) vs umum —
        # relasi pelanggan + alokasi di-eager-load.
        joinedload(PenerimaanKas.pelanggan),
        joinedload(PenerimaanKas.alokasi),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PenerimaanKas.no_bukti.ilike(pattern)
            | PenerimaanKas.pemberi.ilike(pattern)
            | PenerimaanKas.no_nukti.ilike(pattern)
        )

    if status:
        query = query.filter(PenerimaanKas.status == status)
    if kas_bank_id:
        query = query.filter(PenerimaanKas.kas_bank_id == kas_bank_id)
    if tanggal_from:
        query = query.filter(PenerimaanKas.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PenerimaanKas.tanggal <= tanggal_to)
    # === Update #02 — filter jenis transaksi ===
    if jenis_transaksi:
        query = query.filter(PenerimaanKas.jenis_transaksi == jenis_transaksi.strip().upper())

    total = query.count()
    data = query.order_by(PenerimaanKas.created_at.desc()).offset(skip).limit(limit).all()
    _annotate_sisa_belum_teridentifikasi(db, data, PenerimaanKas)
    return data, total


def get_penerimaan_by_id(db: Session, penerimaan_id: UUID) -> Optional[PenerimaanKas]:
    """Ambil 1 penerimaan berdasarkan ID dengan rincian (Update #02: anotasi
    sisa belum teridentifikasi bila dokumen BELUM_TERIDENTIFIKASI)."""
    item = (
        db.query(PenerimaanKas)
        .options(
            joinedload(PenerimaanKas.kas_bank).joinedload(KasBankAkun.akun_perkiraan),
            joinedload(PenerimaanKas.creator),
            joinedload(PenerimaanKas.pelanggan),
            joinedload(PenerimaanKas.rincian).joinedload(PenerimaanRincian.akun_perkiraan),
            joinedload(PenerimaanKas.alokasi).joinedload(PaymentAllocation.sales_invoice),
            joinedload(PenerimaanKas.jurnal),
        )
        .filter(PenerimaanKas.id == penerimaan_id)
        .first()
    )
    if item is not None:
        _annotate_sisa_belum_teridentifikasi(db, [item], PenerimaanKas)
    return item


@atomic_accounting_write
def create_penerimaan(
    db: Session,
    no_nukti: str,
    tanggal: datetime,
    kas_bank_id: UUID,
    rincian_data: list,
    no_cek: Optional[str] = None,
    pemberi: Optional[str] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: UUID = None,
    is_settlement: bool = False,
    jenis_transaksi: Optional[str] = None,
    nilai: Optional[Decimal] = None,
) -> PenerimaanKas:
    """Buat PenerimaanKas baru beserta rincian + auto-post jurnal.

    Parameter:
        is_settlement: True kalau ini adalah pelunasan AR (punya allocation
            ke sales_invoice). Akan memakai RefModule.AR_SETTLEMENT
            (canonical) di jurnal + jenis_transaksi PELUNASAN_INVOICE.
        jenis_transaksi / nilai: Update #02 — tiga jenis transaksi Kas & Bank
            (TRANSAKSI_LAINNYA default / BELUM_TERIDENTIFIKASI dengan `nilai`
            tunggal ke akun penampung / PELUNASAN_INVOICE lewat alur
            settlement — lihat normalize_jenis_transaksi).
    """
    try:
        # Phase G: validate kas_bank ACTIVE
        from app.services.cash_bank_validation import validate_active_kas_bank
        kas_bank = validate_active_kas_bank(db, kas_bank_id, "Penerimaan Kas")

        # === Update #02 — resolusi jenis transaksi + rincian ===
        jenis = normalize_jenis_transaksi(jenis_transaksi, is_settlement)
        if jenis == JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI:
            if rincian_data:
                raise ValueError(
                    "Jenis 'Belum Teridentifikasi' tidak memakai rincian akun lawan — "
                    "kirim nilai total saja; sistem otomatis posting ke akun penampung"
                )
            rincian_data = _resolve_rincian_belum_teridentifikasi(db, nilai, "penerimaan")
        elif not is_settlement:
            if not rincian_data:
                raise ValueError("Rincian akun lawan wajib diisi untuk Transaksi Lainnya")
            for r in rincian_data:
                _validate_akun_lawan_bukan_piutang_hutang(db, r.get("akun_perkiraan_id"))

        # Update ASAHI: pola penomoran seragam "REC ASI/YYYY/NNN" (reset tahunan).
        no_bukti = get_nomor_dokumen_tahunan(
            db, PenerimaanKas, prefix="REC",
            no_column="no_bukti", tanggal=tanggal.date()
        )

        total_nilai = sum(Decimal(str(r.get("nilai", 0))) for r in rincian_data)

        penerimaan = PenerimaanKas(
            no_bukti=no_bukti,
            tanggal=tanggal,
            kas_bank_id=kas_bank_id,
            no_nukti=no_nukti,
            no_cek=no_cek,
            pemberi=pemberi,
            catatan=catatan,
            total_nilai=total_nilai,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusTransaksi.SELESAI if auto_post_jurnal else StatusTransaksi.DRAFT,
            created_by=created_by,
            jenis_transaksi=jenis,  # Update #02
        )
        db.add(penerimaan)
        db.flush()

        for r in rincian_data:
            detail = PenerimaanRincian(
                penerimaan_id=penerimaan.id,
                akun_perkiraan_id=r["akun_perkiraan_id"],
                nilai=Decimal(str(r["nilai"])),
            )
            db.add(detail)

        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(penerimaan)
        if auto_post_jurnal:
            post_penerimaan(db, penerimaan, created_by, is_settlement=is_settlement)

        db.commit()
        db.refresh(penerimaan)
        logger.info(f"PenerimaanKas created: {no_bukti} | total={total_nilai}")
        return penerimaan

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PenerimaanKas: {e}")
        raise


@atomic_accounting_write
def update_penerimaan(
    db: Session,
    db_obj: PenerimaanKas,
    tanggal: Optional[datetime] = None,
    kas_bank_id: Optional[UUID] = None,
    no_nukti: Optional[str] = None,
    no_cek: Optional[str] = None,
    pemberi: Optional[str] = None,
    catatan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    jenis_transaksi: Optional[str] = None,
    nilai: Optional[Decimal] = None,
) -> PenerimaanKas:
    """Update data penerimaan.

    Update #02 — ``jenis_transaksi`` immutable (guard), ``nilai`` khusus
    dokumen BELUM_TERIDENTIFIKASI (rebuild rincian penampung).
    """
    require_unposted(db_obj)
    # === Update #02 — patch jenis transaksi / nilai ===
    _patch_jenis_transaksi_dan_nilai(
        db, db_obj, jenis_transaksi, nilai,
        rincian_model=PenerimaanRincian, fk_attr="penerimaan_id",
    )
    if tanggal is not None:
        db_obj.tanggal = tanggal
    if kas_bank_id is not None:
        db_obj.kas_bank_id = kas_bank_id
    if no_nukti is not None:
        db_obj.no_nukti = no_nukti
    if no_cek is not None:
        db_obj.no_cek = no_cek
    if pemberi is not None:
        db_obj.pemberi = pemberi
    if catatan is not None:
        db_obj.catatan = catatan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_penerimaan(db: Session, db_obj: PenerimaanKas, user_id: Optional[UUID] = None) -> PenerimaanKas:
    """Batalkan penerimaan."""
    if db_obj.status == StatusTransaksi.BATAL:
        raise ValueError("Penerimaan sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusTransaksi.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenerimaanKas cancelled: {db_obj.no_bukti}")
    return db_obj


# ==========================================
# TRANSFER BANK
# ==========================================

def get_transfer_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[TransferBank], int]:
    """Ambil daftar transfer bank dengan filter & pagination."""
    query = db.query(TransferBank).options(
        joinedload(TransferBank.dari_kas_bank).joinedload(KasBankAkun.akun_perkiraan),
        joinedload(TransferBank.ke_kas_bank).joinedload(KasBankAkun.akun_perkiraan),
        joinedload(TransferBank.creator),
        joinedload(TransferBank.jurnal),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(TransferBank.no_transfer.ilike(pattern))

    if status:
        query = query.filter(TransferBank.status == status)
    if tanggal_from:
        query = query.filter(TransferBank.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(TransferBank.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(TransferBank.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_transfer_by_id(db: Session, transfer_id: UUID) -> Optional[TransferBank]:
    """Ambil 1 transfer berdasarkan ID."""
    return (
        db.query(TransferBank)
        .options(
            joinedload(TransferBank.dari_kas_bank).joinedload(KasBankAkun.akun_perkiraan),
            joinedload(TransferBank.ke_kas_bank).joinedload(KasBankAkun.akun_perkiraan),
            joinedload(TransferBank.creator),
            joinedload(TransferBank.jurnal),
        )
        .filter(TransferBank.id == transfer_id)
        .first()
    )


@atomic_accounting_write
def create_transfer(
    db: Session,
    tanggal: datetime,
    dari_kas_bank_id: UUID,
    ke_kas_bank_id: UUID,
    nilai_transfer: Decimal,
    biaya_transfer: Decimal = Decimal("0"),
    informasi: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: UUID = None,
) -> TransferBank:
    """Buat TransferBank baru + auto-post jurnal.

    Phase G fix (Catatan Cash/Bank §3 item 3,4,10):
    - Integrate validate_active_kas_bank for both dari & ke
    - Integrate validate_transfer_banks_different
    - Integrate validate_transfer_amount
    """
    from app.services.cash_bank_validation import (
        validate_active_kas_bank,
        validate_transfer_banks_different,
        validate_transfer_amount,
    )

    try:
        # Phase G: validate banks different
        validate_transfer_banks_different(dari_kas_bank_id, ke_kas_bank_id, "Transfer Bank")

        # Phase G: validate both banks ACTIVE
        dari_kb = validate_active_kas_bank(db, dari_kas_bank_id, "Transfer Bank (asal)")
        ke_kb = validate_active_kas_bank(db, ke_kas_bank_id, "Transfer Bank (tujuan)")

        # Phase G: validate amount > 0 and fee >= 0
        validate_transfer_amount(nilai_transfer, biaya_transfer, "Transfer Bank")

        # Update ASAHI: pola penomoran seragam "TRF ASI/YYYY/NNN" (reset tahunan).
        no_transfer = get_nomor_dokumen_tahunan(
            db, TransferBank, prefix="TRF",
            no_column="no_transfer", tanggal=tanggal.date()
        )

        transfer = TransferBank(
            no_transfer=no_transfer,
            tanggal=tanggal,
            dari_kas_bank_id=dari_kas_bank_id,
            ke_kas_bank_id=ke_kas_bank_id,
            nilai_transfer=nilai_transfer,
            biaya_transfer=biaya_transfer,
            informasi=informasi,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusTransaksi.SELESAI if auto_post_jurnal else StatusTransaksi.DRAFT,
            created_by=created_by,
        )
        db.add(transfer)
        db.flush()

        # Auto-post jurnal
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(transfer)
        if auto_post_jurnal:
            post_transfer(db, transfer, created_by)

        db.commit()
        db.refresh(transfer)
        logger.info(f"TransferBank created: {no_transfer} | nilai={nilai_transfer}")
        return transfer

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating TransferBank: {e}")
        raise


@atomic_accounting_write
def update_transfer(
    db: Session,
    db_obj: TransferBank,
    tanggal: Optional[datetime] = None,
    dari_kas_bank_id: Optional[UUID] = None,
    ke_kas_bank_id: Optional[UUID] = None,
    nilai_transfer: Optional[Decimal] = None,
    biaya_transfer: Optional[Decimal] = None,
    informasi: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> TransferBank:
    """Update data transfer."""
    require_unposted(db_obj)
    if tanggal is not None:
        db_obj.tanggal = tanggal
    if dari_kas_bank_id is not None:
        if dari_kas_bank_id == db_obj.ke_kas_bank_id:
            raise ValueError("Kas/Bank asal dan tujuan tidak boleh sama")
        db_obj.dari_kas_bank_id = dari_kas_bank_id
    if ke_kas_bank_id is not None:
        if ke_kas_bank_id == db_obj.dari_kas_bank_id:
            raise ValueError("Kas/Bank asal dan tujuan tidak boleh sama")
        db_obj.ke_kas_bank_id = ke_kas_bank_id
    if nilai_transfer is not None:
        db_obj.nilai_transfer = nilai_transfer
    if biaya_transfer is not None:
        db_obj.biaya_transfer = biaya_transfer
    if informasi is not None:
        db_obj.informasi = informasi
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_transfer(db: Session, db_obj: TransferBank, user_id: Optional[UUID] = None) -> TransferBank:
    """Batalkan transfer."""
    if db_obj.status == StatusTransaksi.BATAL:
        raise ValueError("Transfer sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusTransaksi.BATAL
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"TransferBank cancelled: {db_obj.no_transfer}")
    return db_obj

def post_pembayaran(db: Session, pembayaran, created_by, is_settlement: bool = False):
    """Post the existing document; caller owns commit/rollback and workflow checks.

    Parameter:
        is_settlement: True kalau pembayaran ini adalah pelunasan AP (punya
            allocation ke purchase_invoice). Akan memakai RefModule.AP_SETTLEMENT
            (canonical) supaya jurnal bisa dibedakan dari generic payment
            (expense / advance / dll). Default False (RefModule.PEMBAYARAN
            legacy) untuk backward compat.

    RefModule logic sesuai Master Roadmap §8 & §21:
        - Generic payment (expense, advance, dll) → RefModule.PEMBAYARAN (legacy)
        - AP settlement (pelunasan hutang ke supplier) → RefModule.AP_SETTLEMENT
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, pembayaran)
    from app.services.settlement_service import validate_payment
    validate_payment(db, pembayaran)
    # === Update #02 — guard jenis transaksi saat posting (defense in depth) ===
    _guard_post_jenis_transaksi(db, pembayaran, rincian(pembayaran))
    tanggal = pembayaran.tanggal
    kas_bank = db.get(KasBankAkun, pembayaran.kas_bank_id)
    rincian_data = [{"akun_perkiraan_id": r.akun_perkiraan_id, "nilai": r.nilai} for r in pembayaran.rincian]
    no_bukti = pembayaran.no_bukti
    total_nilai = pembayaran.total_nilai
    # Update #5 — penalti pelunasan hutang: baris rincian akun penalti
    # otomatis menjadi Dr akun penalti (beban) di samping Dr akun kontrol
    # hutang per alokasi; Kas/Bank dikredit sebesar total (Σ alokasi + penalti)
    # sehingga total debit = total kredit tetap seimbang.
    penalti = Decimal(str(getattr(pembayaran, "penalti", 0) or 0))
    akun_penalti_id = getattr(pembayaran, "akun_penalti_id", None)
    # Update cetak "Bayar Pemasok" — Σ diskon pelunasan per faktur. Rincian
    # tetap Dr hutang sebesar Σ nilai PENUH, sedangkan Kas/Bank dikredit
    # total_nilai (Σ nilai − Σ diskon + penalti); selisihnya dikredit ke
    # akun Potongan Hutang (setting POTONGAN_HUTANG) supaya jurnal balance:
    #   Dr Hutang Σ nilai | Dr Penalti | Cr Kas total_nilai | Cr Potongan Σ diskon
    total_diskon = sum((Decimal(str(getattr(a, "diskon", 0) or 0)) for a in (pembayaran.alokasi or [])), Decimal("0"))
    if total_diskon > 0:
        akun_potongan_id = get_akun_id_or_raise(db, KEY_POTONGAN_HUTANG, "potongan hutang pelunasan")
    else:
        akun_potongan_id = None
    # Update #01 — PPh 23 ditahan dari pembayaran ke supplier (per faktur).
    # Kas keluar = total_nilai (net PPh 23); PPh 23 dikredit ke akun Hutang
    # PPh 23 (setting HUTANG_PPH23) supaya Dr hutang tetap sebesar nilai
    # penuh dan jurnal tetap balance.
    total_pph23 = sum((Decimal(str(getattr(a, "pph23", 0) or 0)) for a in (pembayaran.alokasi or [])), Decimal("0"))
    if total_pph23 > 0:
        akun_pph23_id = get_akun_id_or_raise(db, KEY_HUTANG_PPH23, "PPh 23 pelunasan hutang")
    else:
        akun_pph23_id = None
    entries = [
        # Kredit: Kas/Bank
        JurnalEntryItem(
            akun_perkiraan_id=kas_bank.akun_perkiraan_id,
            kredit=total_nilai,
            keterangan=f"Pembayaran {no_bukti}",
        ),
    ]
    # Kredit: Potongan Hutang (hanya bila ada diskon pelunasan)
    if akun_potongan_id is not None:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=akun_potongan_id,
                kredit=total_diskon,
                keterangan=f"Potongan pelunasan {no_bukti}",
            )
        )
    # Kredit: Hutang PPh 23 (Update #01 — hanya bila ada PPh 23 ditahan)
    if akun_pph23_id is not None:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=akun_pph23_id,
                kredit=total_pph23,
                keterangan=f"PPh 23 ditahan {no_bukti}",
            )
        )
    # Debit: Akun-akun dari rincian (grup akun kontrol alokasi + akun penalti)
    for r in rincian_data:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=r["akun_perkiraan_id"],
                debit=Decimal(str(r["nilai"])),
                keterangan=(
                    f"Penalti pelunasan {no_bukti}"
                    if penalti > 0 and akun_penalti_id and r["akun_perkiraan_id"] == akun_penalti_id
                    else None
                ),
            )
        )

    # RefModule: AP_SETTLEMENT kalau punya allocation, PEMBAYARAN kalau generic.
    ref_module = RefModule.AP_SETTLEMENT if is_settlement else RefModule.PEMBAYARAN
    keterangan = (
        f"Pelunasan Hutang (AP Settlement) {no_bukti}"
        if is_settlement
        else f"Pembayaran Kas {no_bukti}"
    )
    # === Update #02 — penanda jenis pada keterangan jurnal ===
    jenis_transaksi = (getattr(pembayaran, "jenis_transaksi", None) or JENIS_TRANSAKSI_LAINNYA)
    if jenis_transaksi == JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI:
        keterangan += " (Belum Teridentifikasi — akun penampung)"
    if penalti > 0:
        keterangan += f" (termasuk penalti {penalti})"
    if total_diskon > 0:
        keterangan += f" (termasuk diskon {total_diskon})"
    if total_pph23 > 0:
        keterangan += f" (termasuk PPh 23 ditahan {total_pph23})"

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=ref_module,
        ref_no=no_bukti,
        entries=entries,
        keterangan=keterangan,
        ref_id=pembayaran.id,
        tanggal=tanggal,
        created_by=created_by,
    )
    pembayaran.jurnal_umum_id = jurnal.id
    pembayaran.status = StatusTransaksi.SELESAI
    return pembayaran


def post_penerimaan(db: Session, penerimaan, created_by, is_settlement: bool = False):
    """Post the existing document; caller owns commit/rollback and workflow checks.

    Parameter:
        is_settlement: True kalau penerimaan ini adalah pelunasan AR (punya
            allocation ke sales_invoice). Akan memakai RefModule.AR_SETTLEMENT
            (canonical) supaya jurnal bisa dibedakan dari generic receipt
            (other income / refund / dll). Default False (RefModule.PENERIMAAN
            legacy) untuk backward compat.

    RefModule logic sesuai Master Roadmap §8 & §15:
        - Generic receipt (other income, refund customer, dll) → RefModule.PENERIMAAN (legacy)
        - AR settlement (pelunasan piutang dari pelanggan) → RefModule.AR_SETTLEMENT
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, penerimaan)
    from app.services.settlement_service import validate_payment
    validate_payment(db, penerimaan)
    # === Update #02 — guard jenis transaksi saat posting (defense in depth) ===
    _guard_post_jenis_transaksi(db, penerimaan, rincian(penerimaan))
    tanggal = penerimaan.tanggal
    kas_bank = db.get(KasBankAkun, penerimaan.kas_bank_id)
    rincian_data = [{"akun_perkiraan_id": r.akun_perkiraan_id, "nilai": r.nilai} for r in penerimaan.rincian]
    no_bukti = penerimaan.no_bukti
    total_nilai = penerimaan.total_nilai
    # Update #5 — penalti pelunasan piutang: baris rincian akun penalti
    # otomatis menjadi Cr akun penalti (pendapatan) di samping Cr akun kontrol
    # piutang per alokasi; Kas/Bank didebit sebesar total (Σ alokasi + penalti)
    # sehingga total debit = total kredit tetap seimbang.
    penalti = Decimal(str(getattr(penerimaan, "penalti", 0) or 0))
    akun_penalti_id = getattr(penerimaan, "akun_penalti_id", None)
    entries = [
        # Debit: Kas/Bank
        JurnalEntryItem(
            akun_perkiraan_id=kas_bank.akun_perkiraan_id,
            debit=total_nilai,
            keterangan=f"Penerimaan {no_bukti}",
        ),
    ]
    # Update #01 — PPh 23 dipotong pelanggan saat pelunasan piutang (per
    # faktur): kas masuk = total_nilai (net PPh 23); PPh 23 didebit ke akun
    # PPh 23 Dibayar di Muka (setting PPH23_DIBAYAR_DIMUKA) supaya Cr
    # piutang tetap sebesar nilai penuh dan jurnal tetap balance.
    total_pph23 = sum((Decimal(str(getattr(a, "pph23", 0) or 0)) for a in (penerimaan.alokasi or [])), Decimal("0"))
    if total_pph23 > 0:
        akun_pph23_id = get_akun_id_or_raise(db, KEY_PPH23_DIBAYAR_DIMUKA, "PPh 23 potongan pelanggan")
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=akun_pph23_id,
                debit=total_pph23,
                keterangan=f"PPh 23 dipotong pelanggan {no_bukti}",
            )
        )
    # Kredit: Akun-akun dari rincian (grup akun kontrol alokasi + akun penalti)
    for r in rincian_data:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=r["akun_perkiraan_id"],
                kredit=Decimal(str(r["nilai"])),
                keterangan=(
                    f"Penalti pelunasan {no_bukti}"
                    if penalti > 0 and akun_penalti_id and r["akun_perkiraan_id"] == akun_penalti_id
                    else None
                ),
            )
        )

    # RefModule: AR_SETTLEMENT kalau punya allocation, PENERIMAAN kalau generic.
    ref_module = RefModule.AR_SETTLEMENT if is_settlement else RefModule.PENERIMAAN
    keterangan = (
        f"Pelunasan Piutang (AR Settlement) {no_bukti}"
        if is_settlement
        else f"Penerimaan Kas {no_bukti}"
    )
    # === Update #02 — penanda jenis pada keterangan jurnal ===
    jenis_transaksi = (getattr(penerimaan, "jenis_transaksi", None) or JENIS_TRANSAKSI_LAINNYA)
    if jenis_transaksi == JENIS_TRANSAKSI_BELUM_TERIDENTIFIKASI:
        keterangan += " (Belum Teridentifikasi — akun penampung)"
    if penalti > 0:
        keterangan += f" (termasuk penalti {penalti})"
    if total_pph23 > 0:
        keterangan += f" (termasuk PPh 23 dipotong pelanggan {total_pph23})"

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=ref_module,
        ref_no=no_bukti,
        entries=entries,
        keterangan=keterangan,
        ref_id=penerimaan.id,
        tanggal=tanggal,
        created_by=created_by,
    )
    penerimaan.jurnal_umum_id = jurnal.id
    penerimaan.status = StatusTransaksi.SELESAI
    return penerimaan


def post_transfer(db: Session, transfer, created_by):
    """Post the existing document; caller owns commit/rollback and workflow checks."""
    from app.services.document_totals import validate_postable
    validate_postable(db, transfer)
    tanggal = transfer.tanggal
    dari_kb = db.get(KasBankAkun, transfer.dari_kas_bank_id)
    ke_kb = db.get(KasBankAkun, transfer.ke_kas_bank_id)
    no_transfer = transfer.no_transfer
    nilai_transfer = transfer.nilai_transfer
    biaya_transfer = transfer.biaya_transfer
    entries = [
        # Debit: Kas/Bank Tujuan
        JurnalEntryItem(
            akun_perkiraan_id=ke_kb.akun_perkiraan_id,
            debit=nilai_transfer,
            keterangan=f"Transfer ke {ke_kb.nama}",
        ),
        # Kredit: Kas/Bank Asal
        JurnalEntryItem(
            akun_perkiraan_id=dari_kb.akun_perkiraan_id,
            kredit=nilai_transfer,
            keterangan=f"Transfer dari {dari_kb.nama}",
        ),
    ]

    # Jika ada biaya transfer
    if biaya_transfer and biaya_transfer > 0:
        # D: Beban Transfer Bank (dari setting_akun) — K: Kas/Bank Asal (berkurang)
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_BEBAN_TRANSFER_BANK, context=f"Transfer {no_transfer}"
                ),
                debit=biaya_transfer,
                keterangan=f"Biaya transfer {no_transfer}",
            )
        )
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=dari_kb.akun_perkiraan_id,
                kredit=biaya_transfer,
                keterangan=f"Biaya transfer {no_transfer}",
            )
        )

    jurnal = auto_posting_jurnal(
        db=db,
        ref_module=RefModule.BANK_TRANSFER,
        ref_no=no_transfer,
        entries=entries,
        keterangan=f"Transfer Bank {no_transfer}",
        ref_id=transfer.id,
        tanggal=tanggal,
        created_by=created_by,
    )
    transfer.jurnal_umum_id = jurnal.id
    transfer.status = StatusTransaksi.SELESAI
    return transfer


# ==========================================
# AUD-11 (update #04) — MONITORING HUTANG PAJAK
# ==========================================
def get_pajak_monitoring(db: Session) -> dict:
    """Rekap hutang pajak (akun TAX_PAYABLE) + riwayat pelunasan pajak.

    - ``akunPajak``: semua akun aktif system_account_type=TAX_PAYABLE
      (default COA v2: 213001 PPh 21, 213002 PPh 23, 213003 PPh 29,
      213004 PPh Final) + saldo GL (POSTED) + arah normal (hutang = kredit −
      debit; positif berarti masih harus dibayar).
    - ``riwayatPelunasan``: dokumen PembayaranKas jenis PELUNASAN_PAJAK
      (terbaru dulu) — bukti pelunasan hutang pajak ke kas/bank.
    """
    from app.models.detail.jurnal_detail import JurnalDetail
    from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal

    accounts = (
        db.query(AkunPerkiraan)
        .filter(
            AkunPerkiraan.system_account_type == "TAX_PAYABLE",
            AkunPerkiraan.active.is_(True),
        )
        .order_by(AkunPerkiraan.kode)
        .all()
    )
    balances = {}
    if accounts:
        from app.services.organization_service import apply_scope
        saldo_q = (
            db.query(
                JurnalDetail.akun_perkiraan_id,
                func.coalesce(func.sum(JurnalDetail.debit), 0)
                - func.coalesce(func.sum(JurnalDetail.kredit), 0),
            )
            .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
            .filter(
                JurnalDetail.akun_perkiraan_id.in_([a.id for a in accounts]),
                JurnalUmum.status == StatusJurnal.POSTED,
            )
        )
        # AUD-17 (update #05): scope laporan per perusahaan (dari ?companyId)
        saldo_q = apply_scope(db, saldo_q, JurnalUmum)
        balances = dict(saldo_q.group_by(JurnalDetail.akun_perkiraan_id).all())

    akun_pajak = []
    total_hutang = Decimal("0")
    total_dibayar_dimuka = Decimal("0")
    for akun in accounts:
        gl_debit_kredit = Decimal(str(balances.get(akun.id) or 0))
        # hutang pajak normal bersaldo KREDIT: saldo_gl (D-K) negatif.
        saldo_normal = -gl_debit_kredit  # positif = hutang
        if saldo_normal > 0:
            total_hutang += saldo_normal
        elif saldo_normal < 0:
            total_dibayar_dimuka += -saldo_normal
        akun_pajak.append({
            "id": akun.id, "kode": akun.kode, "nama": akun.nama,
            "saldo_gl": gl_debit_kredit,
            "hutang": saldo_normal if saldo_normal > 0 else Decimal("0"),
            "dibayar_lebih": -saldo_normal if saldo_normal < 0 else Decimal("0"),
        })

    riwayat = (
        db.query(PembayaranKas)
        .options(joinedload(PembayaranKas.kas_bank))
        .filter(PembayaranKas.jenis_transaksi == JENIS_TRANSAKSI_PELUNASAN_PAJAK)
        .order_by(PembayaranKas.tanggal.desc(), PembayaranKas.created_at.desc())
        .limit(50)
        .all()
    )
    riwayat_pelunasan = [{
        "id": r.id, "no_bukti": r.no_bukti, "no_nukti": r.no_nukti,
        "tanggal": r.tanggal, "total_nilai": r.total_nilai,
        "catatan": r.catatan,
        "kas_bank": {"id": r.kas_bank.id, "kode": r.kas_bank.kode,
                      "nama": r.kas_bank.nama} if r.kas_bank else None,
        "status": getattr(r.status, "value", r.status),
        "jurnal_umum_id": r.jurnal_umum_id,
        "akun": [{"id": det.akun_perkiraan_id, "nilai": det.nilai}
                 for det in r.rincian],
    } for r in riwayat]

    return {
        "akun_pajak": akun_pajak,
        "total_hutang": total_hutang,
        "total_dibayar_lebih": total_dibayar_dimuka,
        "riwayat_pelunasan": riwayat_pelunasan,
    }
