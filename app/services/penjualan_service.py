"""
penjualan_service.py

Service layer untuk modul Penjualan.
Menghandle CRUD + auto-posting jurnal untuk:
- SalesOrder (+ SalesOrderDetail + TransaksiBiaya)
- SalesInvoice (+ SalesInvoiceDetail + TransaksiBiaya)
- SalesRetur (+ SalesReturDetail)
- PengirimanBarang (+ PengirimanBarangDetail)
"""

from app.services.accounting_control import atomic_accounting_write, require_unposted, require_no_stock_movement
from app.services.posting_service import reverse_journal

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID
import re

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.transaksi.penjualan.sales_order import SalesOrder, StatusPenjualan
from app.models.transaksi.penjualan.sales_order_termin import SalesOrderTermin
from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
from app.models.transaksi.penjualan.sales_retur import SalesRetur
from app.models.transaksi.penjualan.pengiriman_barang import PengirimanBarang
from app.models.detail.sales_order_detail import SalesOrderDetail
from app.models.detail.sales_invoice_detail import SalesInvoiceDetail
from app.models.detail.sales_retur_detail import SalesReturDetail
from app.models.detail.pengiriman_barang_detail import PengirimanBarangDetail
from app.models.transaksi.transaksi_biaya import TransaksiBiaya
from app.models.transaksi.payment_allocation import PaymentAllocation
from app.models.detail.penerimaan_rincian import PenerimaanRincian
from app.models.master.pelanggan import Pelanggan
from app.models.master.barang import Barang  # Update #5: eager-load satuan barang utk detail response
from app.models.transaksi.jurnal import RefModule
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.services.stok_service import update_stok_barang
from app.services.decimal_utils import safe_decimal, safe_int
from app.services.setting_akun_service import (
    get_akun_id_or_raise,
    KEY_PENDAPATAN_PENJUALAN,
    KEY_PPN_KELUARAN,
    KEY_RETUR_PENJUALAN,
    KEY_PENDAPATAN_ANGKUT,
    KEY_PERSEDIAAN_BARANG_JADI,
    # === Update ASAHI — PPh23 opsional pada Invoice Penjualan ===
    KEY_PPH23_DIBAYAR_DIMUKA,
    # === Update #031 — akun lawan default penerimaan Termin SO ===
    KEY_UANG_MUKA_PENJUALAN,
)
from app.utils.nomor_dokumen import get_nomor_dokumen, get_nomor_dokumen_tahunan


# ==========================================
# HELPER: Hitung total dari detail
# ==========================================
def _hitung_total_detail(details_data: list) -> Decimal:
    """Hitung sub_total dari list detail (harga * qty - diskon)."""
    return sum(Decimal(str(d.get("sub_total", d.get("harga", 0)) * Decimal(str(d.get("qty", 0))))) for d in details_data)


def _hitung_total_biaya(biaya_data: list) -> Decimal:
    """Hitung total biaya tambahan."""
    return sum((safe_decimal(b.get("jumlah")) for b in biaya_data), Decimal("0"))


def _hitung_grand_total(
    sub_total: Decimal,
    total_diskon: Decimal,
    ppn_pct: Decimal,
    total_biaya_tambahan: Decimal,
    # === Update ASAHI — PPh23/PPN opsional (Sales Invoice) ===
    ppn_applicable: bool = True,
    pph23_pct: Optional[Decimal] = None,
    pph23_applicable: bool = False,
) -> Tuple[Decimal, Decimal, Decimal]:
    """Hitung total_ppn, total_pph23, dan grand_total.

    ppn = (sub_total - total_diskon) * ppn_pct / 100   (bila ppn_applicable)
    pph23 = (sub_total - total_diskon) * pph23_pct / 100 (bila pph23_applicable)
    grand_total = sub_total - total_diskon + ppn + total_biaya_tambahan - pph23
    """
    dasar_pajak = sub_total - total_diskon
    total_ppn = dasar_pajak * (ppn_pct if ppn_applicable else Decimal("0")) / Decimal("100")
    total_pph23 = (
        dasar_pajak * pph23_pct / Decimal("100")
        if (pph23_applicable and pph23_pct is not None)
        else Decimal("0")
    )
    grand_total = dasar_pajak + total_ppn + total_biaya_tambahan - total_pph23
    return total_ppn, total_pph23, grand_total


def _create_biaya_tambahan(db: Session, model_obj, biaya_data: list, fk_field: str):
    """Buat TransaksiBiaya rows untuk SO atau SINV."""
    for b in biaya_data:
        tb = TransaksiBiaya(
            nama=b["nama"],
            jumlah=Decimal(str(b["jumlah"])),
            **{fk_field: model_obj.id},
        )
        db.add(tb)


# ==========================================
# SALES ORDER
# ==========================================

def get_sales_order_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[SalesOrder], int]:
    """Ambil daftar sales order dengan filter & pagination."""
    query = db.query(SalesOrder).options(
        joinedload(SalesOrder.pelanggan),
        joinedload(SalesOrder.syarat_bayar),
        joinedload(SalesOrder.creator),
        # Update #032 — info penawaran sumber (kolom "Dari Penawaran")
        joinedload(SalesOrder.penawaran),
        joinedload(SalesOrder.details).joinedload(SalesOrderDetail.barang),
        # Update #7 (B-05): eager-load satuan baris — FE membaca satuan?.nama
        joinedload(SalesOrder.details).joinedload(SalesOrderDetail.satuan),
        joinedload(SalesOrder.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            SalesOrder.no_pesanan.ilike(pattern)
            | SalesOrder.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(SalesOrder.status == status)
    if pelanggan_id:
        query = query.filter(SalesOrder.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(SalesOrder.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(SalesOrder.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(SalesOrder.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_sales_order_by_id(db: Session, so_id: UUID) -> Optional[SalesOrder]:
    """Ambil 1 sales order berdasarkan ID dengan detail."""
    return (
        db.query(SalesOrder)
        .options(
            joinedload(SalesOrder.pelanggan),
            joinedload(SalesOrder.syarat_bayar),
            joinedload(SalesOrder.creator),
            joinedload(SalesOrder.jurnal),
            # Update #032 — info penawaran sumber (kolom "Dari Penawaran")
            joinedload(SalesOrder.penawaran),
            joinedload(SalesOrder.details).joinedload(SalesOrderDetail.barang),
            # Update #7 (B-05): eager-load satuan baris — FE membaca satuan?.nama
            joinedload(SalesOrder.details).joinedload(SalesOrderDetail.satuan),
            joinedload(SalesOrder.biaya_tambahan),
        )
        .filter(SalesOrder.id == so_id)
        .first()
    )


@atomic_accounting_write
def create_sales_order(
    db: Session,
    tanggal: datetime,
    pelanggan_id: UUID,
    details_data: list,
    biaya_data: Optional[list] = None,
    syarat_bayar_id: Optional[UUID] = None,
    customer_code: Optional[str] = None,
    tanggal_pengiriman: Optional[datetime] = None,
    alamat_pengiriman: Optional[str] = None,
    diskon_global: Optional[Decimal] = Decimal("0"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: Optional[UUID] = None,
    customer_po_number: Optional[str] = None,
    customer_po_date: Optional[date] = None,
    currency: str = "IDR",
    # === Update #031 — Pembayaran Termin (baris nominal, mis. 3x pembayaran) ===
    pembayaran_termin: Optional[list] = None,
    # === Update #032 — link ke Penawaran sumber (form SO "Pilih Penawaran"
    # / tombol konversi; repeat order: satu penawaran → banyak SO) ===
    penawaran_id: Optional[UUID] = None,
) -> SalesOrder:
    """Buat SalesOrder baru beserta detail + biaya tambahan.
    - Generate no_pesanan otomatis
    - Hitung sub_total, total_diskon, grand_total dari detail
      (Update #042: TANPA PPN — pajak hanya di invoice; grand total =
      subtotal − diskon + biaya)
    - Order tidak melakukan posting jurnal; pencatatan dilakukan pada invoice.

    Update ASAHI (revisi SO): field ekspedisi/penjual dihapus; `customer_code`
    (singkatan bebas nama perusahaan customer) menjadi segmen nomor pesanan:
        terisi  → "SO ASI/TBI/2026/001"
        kosong  → "SO ASI/-/2026/001"

    Update #031 — pembayaran_termin: daftar nominal termin (tiap baris nominal
    saja, urutan input); termin kosong/tidak dikirim = SO tanpa termin (tetap
    sah — pembayaran SO tidak tersedia, pakai alur invoice).

    Update #035 — TERMIN BEBAS: total seluruh termin TIDAK lagi diwajibkan
    sama dengan grand_total (nominal ditentukan bebas oleh user). Total <
    grand_total → sisa mengikuti alur Invoice; total > grand_total →
    diperbolehkan (mis. uang muka melebihi pesanan — form menampilkan
    catatan, keputusan di tangan user). Validasi nominal > 0 tetap.

    Update #032 — penawaran_id: link ke penawaran sumber (form SO "Pilih
    Penawaran"; repeat order — satu penawaran boleh dipakai banyak SO).

    Update #033 — pemakaian penawaran sumber menaikkan statusnya DRAFT/DIPROSES
    → SELESAI (konsisten dengan convert_to_so; jalur form sebelumnya membiarkan
    penawaran tetap DRAFT — inkonsisten dengan badge "dipakai N× SO").
    """
    try:
        biaya_data = biaya_data or []

        # Validasi & normalisasi singkatan customer (segmen nomor SO)
        customer_code = (customer_code or "").strip() or None
        if customer_code:
            if "/" in customer_code:
                raise ValueError("Singkatan customer tidak boleh mengandung karakter '/'")
            if len(customer_code) > 20:
                raise ValueError("Singkatan customer maksimal 20 karakter")

        # Validasi pelanggan
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # === Update #032 — validasi baris: barang master ATAU item bebas ===
        # Baris wajib punya barang_id ATAU item_nama (item bebas ditarik dari
        # penawaran / diketik manual — tidak ada di master barang; tidak
        # menyentuh gudang. Tidak bisa dikirim, bisa difakturkan).
        for i, d in enumerate(details_data, start=1):
            has_barang = d.get("barang_id") is not None
            item_nama = (d.get("item_nama") or "").strip()
            if not has_barang and not item_nama:
                raise ValueError(
                    f"Baris {i}: pilih barang dari master ATAU isi nama item bebas"
                )
            if has_barang:
                d["item_nama"] = None
                d["item_satuan"] = None
            else:
                d["item_nama"] = item_nama
                d["item_satuan"] = (d.get("item_satuan") or "").strip() or None

        # === Update #032 — validasi penawaran sumber (bila dikirim) ===
        # Guard: penawaran harus ada, pelanggan cocok, dan berstatus DRAFT /
        # SELESAI (SELESAI = repeat order; DIREVISI/DIBATALKAN ditolak).
        # Update #033 — penawaran sumber kini BISA dikirim dari form SO
        # ("Pilih Penawaran" / tombol Buat SO di daftar penawaran, yang kini
        # membuka form terisi — bukan lagi membuat SO langsung).
        _penawaran_sumber = None
        if penawaran_id is not None:
            from app.models.transaksi.penjualan.penawaran import Penawaran
            _penawaran_sumber = db.query(Penawaran).filter(Penawaran.id == penawaran_id).first()
            if _penawaran_sumber is None:
                raise ValueError(f"Penawaran dengan ID {penawaran_id} tidak ditemukan")
            if _penawaran_sumber.pelanggan_id != pelanggan_id:
                raise ValueError(
                    "Penawaran sumber milik pelanggan yang berbeda — pilih "
                    "penawaran milik pelanggan ini atau hapus pilihannya."
                )
            if _penawaran_sumber.status not in ("DRAFT", "DIPROSES", "SELESAI"):
                raise ValueError(
                    f"Penawaran {_penawaran_sumber.no_penawaran} berstatus {_penawaran_sumber.status} "
                    "— tidak bisa dipakai membuat Sales Order"
                )

        # Hitung sub_total dan total_diskon dari detail
        sub_total = Decimal("0")
        total_diskon = Decimal("0")
        for d in details_data:
            harga = safe_decimal(d.get("harga"))
            qty = safe_int(d.get("qty"))
            diskon = safe_decimal(d.get("diskon"))
            line_total = harga * qty
            diskon_nilai = line_total * diskon / Decimal("100")
            sub_total += line_total
            total_diskon += diskon_nilai
            d["sub_total"] = line_total - diskon_nilai  # Update sub_total per line

        total_biaya_tambahan = _hitung_total_biaya(biaya_data)
        # === Update #042 — SO TANPA PPN: pajak hanya dihitung/diterapkan di
        # INVOICE. Grand total SO = subtotal − diskon + biaya (tanpa PPN).
        # Nilai awal ini hanya placeholder — refresh_totals (dibawah)
        # menghitung ulang dengan diskon global. ===
        grand_total = sub_total - total_diskon + total_biaya_tambahan

        # Generate nomor pesanan
        # Update ASAHI: format baru "SO ASI/YYYY/NNN" — reset tahunan,
        # sequence terpisah dari PO (jawaban user, catatan update cetak SO/PO).
        # Update ASAHI (revisi SO): segmen singkatan customer ditambahkan —
        # "SO ASI/TBI/2026/001" (terisi) atau "SO ASI/-/2026/001" (kosong).
        # Selalu pass customer_code (string, bisa "") agar format baru aktif.
        # === Update ASAHI #019 — prefix & kode perusahaan via Preferensi ===
        # (doc_type SALES_ORDER; fallback "SO" — perilaku lama bila belum diubah).
        no_pesanan = get_nomor_dokumen_tahunan(
            db, SalesOrder, prefix="SO",
            no_column="no_pesanan", tanggal=tanggal.date(),
            customer_code=customer_code or "",
            doc_type="SALES_ORDER",
        )

        # Buat header
        so = SalesOrder(
            no_pesanan=no_pesanan,
            tanggal=tanggal,
            pelanggan_id=pelanggan_id,
            syarat_bayar_id=syarat_bayar_id,
            customer_code=customer_code,
            tanggal_pengiriman=tanggal_pengiriman,
            alamat_pengiriman=alamat_pengiriman,
            diskon_global=diskon_global,
            sub_total=sub_total,
            total_diskon=total_diskon,
            total_biaya_tambahan=total_biaya_tambahan,
            grand_total=grand_total,
            auto_post_jurnal=False,
            status=StatusPenjualan.DRAFT,
            keterangan=keterangan,
            created_by=created_by,
            customer_po_number=customer_po_number,
            customer_po_date=customer_po_date,
            currency=currency,
            # Update #032 — link penawaran sumber (repeat order)
            penawaran_id=penawaran_id,
        )
        db.add(so)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = SalesOrderDetail(
                sales_order_id=so.id,
                # Update #032 — barang_id bisa None (baris item bebas dari
                # penawaran; nama + satuan bebas tersimpan di baris ini).
                barang_id=d.get("barang_id"),
                item_nama=d.get("item_nama"),
                item_satuan=d.get("item_satuan"),
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=Decimal(str(d["sub_total"])),
                # Update #7 (B-05): persist satuan per baris (schema Phase B sudah
                # punya field; sebelumnya diabaikan → kolom DB selalu NULL dan FE
                # menampilkan dialog warning "satuan belum tersimpan").
                satuan_id=d.get("satuan_id"),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, so, biaya_data, "sales_order_id")

        # Fix diskon global (temuan minor): total SO sebelumnya mengabaikan
        # diskon_global — field hanya tersimpan tanpa memengaruhi total.
        # refresh_totals menghitung ulang sub_total/total_diskon/
        # grand_total dengan diskon baris + diskon global (Update #042:
        # TANPA PPN — pajak hanya di invoice).
        # flush dulu supaya relationship .details terisi dari pending inserts
        # (pola yang sama dengan create_sales_invoice).
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(so)

        # === Update #031 — Pembayaran Termin (baris nominal) ===
        # Tiap baris nominal saja; urutan baris = urutan input user.
        # Update #035 — TERMIN BEBAS: total TIDAK diwajibkan = grand_total
        # (lihat _create_so_termins); nominal ditentukan user.
        if pembayaran_termin:
            _create_so_termins(db, so, pembayaran_termin)

        # === Update #033 — penawaran sumber dipakai → status SELESAI ===
        # Konsisten dengan convert_to_so: pemakaian PERTAMA menaikkan DRAFT/
        # DIPROSES → SELESAI (penawaran tetap bisa dipilih lagi — repeat order;
        # edit selanjutnya harus lewat jalur Revisi agar SO lama tak berubah
        # diam-diam). Sebelum #033 hanya jalur tombol konversi yang menaikkan
        # status; jalur form ("Pilih Penawaran") meninggalkan penawaran tetap
        # DRAFT — inkonsisten dengan daftar (badge "dipakai N× SO").
        if _penawaran_sumber is not None and _penawaran_sumber.status in ("DRAFT", "DIPROSES"):
            _penawaran_sumber.status = "SELESAI"
            db.add(_penawaran_sumber)

        # Order tidak mengakui pendapatan/piutang atau pembelian/utang.
        db.commit()
        db.refresh(so)
        logger.info(f"SalesOrder created: {no_pesanan} | grand_total={grand_total}")
        return so

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating SalesOrder: {e}")
        raise


# ==========================================
# Update #031 — TERMIN PEMBAYARAN SALES ORDER
# ==========================================


def _create_so_termins(db: Session, so: SalesOrder, pembayaran_termin: list) -> None:
    """Validasi + buat baris termin pembayaran SO (dipanggil create_sales_order).

    - Tiap baris nominal saja (angka > 0).
    - Urutan baris mengikuti urutan input (urutan 1..n).

    Update #035 — TERMIN BEBAS: total seluruh termin TIDAK lagi
    diwajibkan sama dengan grand_total SO (dulu ValueError bila selisih
    > 0,05 — kini nominal ditentukan bebas oleh user):
    - total < grand_total → sisa pembayaran mengikuti alur Invoice;
    - total > grand_total → diperbolehkan (mis. uang muka melebihi
      nilai pesanan) — form menampilkan catatan, keputusan di user.
    Validasi nominal > 0 TETAP (baris nol/tidak bermakna ditolak).
    """

    nominals: list[Decimal] = []
    for t in pembayaran_termin:
        nilai = safe_decimal(t)
        if nilai <= 0:
            raise ValueError("Nominal termin pembayaran harus lebih dari 0")
        nominals.append(nilai)

    for urutan, nilai in enumerate(nominals, start=1):
        db.add(SalesOrderTermin(
            sales_order_id=so.id,
            urutan=urutan,
            nominal=nilai,
        ))
    db.flush()


def get_sales_order_termins(db: Session, so_id: UUID) -> list[SalesOrderTermin]:
    """Daftar termin SO (urut) beserta status bayar + no bukti penerimaannya."""

    return (
        db.query(SalesOrderTermin)
        .options(joinedload(SalesOrderTermin.penerimaan))
        .filter(SalesOrderTermin.sales_order_id == so_id)
        .order_by(SalesOrderTermin.urutan.asc())
        .all()
    )


def get_open_termin_sos(db: Session, search: str | None = None, limit: int = 100):
    """SO yang masih punya termin BELUM dibayar — pilihan di form Penerimaan.

    Satu SO bisa muncul berkali-kali untuk pembayaran (tiap penerimaan membayar
    SATU termin). Filter opsional: no pesanan / nama pelanggan.
    """

    query = (
        db.query(SalesOrder, SalesOrderTermin)
        .join(SalesOrderTermin, SalesOrderTermin.sales_order_id == SalesOrder.id)
        .options(joinedload(SalesOrder.pelanggan))
        .filter(
            SalesOrderTermin.penerimaan_kas_id.is_(None),
            SalesOrder.status != StatusPenjualan.DIBATALKAN,
        )
        .order_by(SalesOrder.created_at.desc(), SalesOrderTermin.urutan.asc())
    )
    if search:
        pattern = f"%{search.strip()}%"
        query = query.outerjoin(Pelanggan, Pelanggan.id == SalesOrder.pelanggan_id).filter(
            SalesOrder.no_pesanan.ilike(pattern) | Pelanggan.nama.ilike(pattern)
        )
    rows = query.limit(max(1, min(limit, 500))).all()

    # Gabungkan per SO: daftar termin belum dibayar + ringkasan.
    hasil: dict[UUID, dict] = {}
    for so, termin in rows:
        entry = hasil.setdefault(
            so.id,
            {
                "sales_order_id": so.id,
                "no_pesanan": so.no_pesanan,
                "pelanggan_id": so.pelanggan_id,
                "pelanggan_nama": so.pelanggan.nama if so.pelanggan else None,
                "grand_total": so.grand_total,
                "termin_belum_dibayar": [],
                "sisa_termin": Decimal("0"),
            },
        )
        entry["termin_belum_dibayar"].append(
            {"id": termin.id, "urutan": termin.urutan, "nominal": termin.nominal}
        )
        entry["sisa_termin"] += termin.nominal or Decimal("0")

    # === Update #036 — tandai SO yang sudah punya invoice AKTIF (POSTED) ===
    # Pembayaran termin berikutnya untuk SO ini otomatis diterapkan ke akun
    # Piutang invoice (bukan Uang Muka) — form Penerimaan menampilkan info
    # ini supaya user tahu ke mana pembayaran akan dicatat.
    so_ids = list(hasil.keys())
    difakturkan: set = set()
    if so_ids:
        from app.services.settlement_service import active_documents

        rows_si = (
            db.query(SalesInvoice.sales_order_id)
            .filter(
                SalesInvoice.sales_order_id.in_(so_ids),
                SalesInvoice.id.in_(active_documents(db, SalesInvoice).scalar_subquery()),
            )
            .distinct()
            .all()
        )
        difakturkan = {r[0] for r in rows_si if r[0] is not None}
    for entry in hasil.values():
        entry["sudah_difakturkan"] = entry["sales_order_id"] in difakturkan

    return list(hasil.values())


# ==========================================
# HELPER: Update #036 — DP termin SO ↔ invoice
# ==========================================
def _penerimaan_termin_aktif_ids(db: Session, so_id: UUID) -> list:
    """ID penerimaan termin SO yang AKTIF (jurnal POSTED, belum dibatalkan).

    DP hanya dihitung dari penerimaan yang benar-benar menggerakkan kas —
    penerimaan draft (menunggu approval) / sudah dibatalkan tidak dianggap
    (konsisten dengan perhitungan sisa tagihan pelunasan:
    settlement_service.active_documents).
    """
    from app.models.transaksi.kas_bank.penerimaan import PenerimaanKas
    from app.services.settlement_service import active_documents

    rows = (
        db.query(SalesOrderTermin.penerimaan_kas_id)
        .filter(
            SalesOrderTermin.sales_order_id == so_id,
            SalesOrderTermin.penerimaan_kas_id.isnot(None),
        )
        .all()
    )
    ids = [r[0] for r in rows if r[0] is not None]
    if not ids:
        return []
    aktif = {
        row[0]
        for row in active_documents(db, PenerimaanKas)
        .filter(PenerimaanKas.id.in_(ids))
        .all()
    }
    return [i for i in ids if i in aktif]


def _dp_termin_per_akun(db: Session, so_id: UUID) -> list:
    """DP termin SO yang sudah dibayar (aktif), dikelompokkan per akun lawan.

    Tiap penerimaan termin punya SATU baris rincian akun lawan (default
    Uang Muka Penjualan — bisa diganti per transaksi), jadi agregasi rincian
    memberikan komposisi akun DP yang tepat untuk jurnal aplikasi.
    Return [(akun_perkiraan_id, nilai)] urut akun pertama kali dipakai.
    """
    ids = _penerimaan_termin_aktif_ids(db, so_id)
    if not ids:
        return []
    rincian = (
        db.query(PenerimaanRincian)
        .filter(PenerimaanRincian.penerimaan_id.in_(ids))
        .order_by(PenerimaanRincian.created_at.asc())
        .all()
    )
    per_akun: dict = {}
    urutan_akun: list = []
    for r in rincian:
        key = r.akun_perkiraan_id
        if key not in per_akun:
            per_akun[key] = Decimal("0")
            urutan_akun.append(key)
        per_akun[key] += safe_decimal(r.nilai)
    return [(k, per_akun[k]) for k in urutan_akun if per_akun[k] > 0]


def _dp_termin_tersedia(
    db: Session, so_id: UUID, exclude_invoice_id: Optional[UUID] = None
) -> Decimal:
    """DP termin yang BELUM diterapkan ke invoice aktif manapun dari SO ini.

    = Σ pembayaran termin aktif − Σ dp_teraplikasi invoice AKTIF lain dari
    SO yang sama (invoice yang sedang diposting dikecualikan). Menjaga
    sebagian invoicing: satu DP tidak pernah diaplikasikan dua kali.
    SO tanpa termin / tanpa pembayaran → 0 (perilaku pra-#036).
    """
    from app.services.settlement_service import active_documents

    total = sum((nilai for _akun, nilai in _dp_termin_per_akun(db, so_id)), Decimal("0"))
    if total <= 0:
        return Decimal("0")

    query = db.query(func.coalesce(func.sum(SalesInvoice.dp_teraplikasi), 0)).filter(
        SalesInvoice.sales_order_id == so_id,
        SalesInvoice.id.in_(active_documents(db, SalesInvoice).scalar_subquery()),
    )
    if exclude_invoice_id is not None:
        query = query.filter(SalesInvoice.id != exclude_invoice_id)
    terpakai = safe_decimal(query.scalar())
    return max(Decimal("0"), total - terpakai)


def _invoice_aktif_untuk_termin(db: Session, so: SalesOrder):
    """Invoice AKTIF (POSTED) milik SO dengan sisa tagihan > 0 — target
    pembayaran termin yang datang SETELAH invoice diposting (Update #036).

    Dipilih invoice tertua yang masih bersisa; sisa dihitung dengan
    settlement_service.balances (grand_total − bayar − retur − dp_teraplikasi).
    Return (invoice, sisa_tagihan); (None, 0) bila tidak ada yang cocok.
    """
    from app.services.settlement_service import active_documents, balances

    invoices = (
        db.query(SalesInvoice)
        .filter(
            SalesInvoice.sales_order_id == so.id,
            SalesInvoice.id.in_(active_documents(db, SalesInvoice).scalar_subquery()),
        )
        .order_by(SalesInvoice.tanggal.asc(), SalesInvoice.created_at.asc())
        .all()
    )
    if not invoices:
        return None, Decimal("0")
    saldo = balances(db, invoices)
    for inv in invoices:
        info = saldo.get(inv.id) or {}
        sisa = safe_decimal(info.get("sisa_tagihan", 0))
        if sisa > 0:
            return inv, sisa
    return None, Decimal("0")


def release_termin_links(db: Session, penerimaan_id: UUID) -> int:
    """Lepas link termin milik penerimaan ini (dipanggil alur pembatalan).

    Mirror pembersihan alokasi pelunasan (AUD2-E1): termin adalah data turunan;
    saat penerimaan TERMIN_SO dibatalkan (jurnal pembalik) / dihapus draft,
    termin kembali BELUM dibayar sehingga bisa dibayar ulang. Jejak audit tetap
    utuh lewat jurnal. Return jumlah baris termin yang dilepas.
    """

    return (
        db.query(SalesOrderTermin)
        .filter(SalesOrderTermin.penerimaan_kas_id == penerimaan_id)
        .update(
            {SalesOrderTermin.penerimaan_kas_id: None, SalesOrderTermin.dibayar_pada: None},
            synchronize_session=False,
        )
    )


def terima_pembayaran_termin(
    db: Session,
    so_id: UUID,
    termin_id: UUID,
    kas_bank_id: UUID,
    tanggal: datetime,
    created_by: UUID,
    akun_perkiraan_id: Optional[UUID] = None,
    no_nukti: Optional[str] = None,
    catatan: Optional[str] = None,
):
    """Terima pembayaran SATU termin SO — satu SO bisa dipanggil berkali-kali.

    Alur (Update #031):
    - Validasi SO tidak DIBATALKAN, termin milik SO tsb dan BELUM dibayar.
    - Buat PenerimaanKas jenis TERMIN_SO (prefix nomor REC ASI/YYYY/NNN)
      dengan SATU baris rincian akun lawan sebesar nominal termin; pelanggan
      SO tercatat sebagai pemberi supaya list Kas & Bank menampilkan pihaknya.
    - Link termin → penerimaan (status DIBAYAR).
    - Jurnal diposting lewat workflow penerimaan_kas oleh ENDPOINT (admin:
      langsung final; user biasa: menunggu approval — pola Penerimaan lain).

    Update #036 — akun lawan default kini KONTEKSTUAL (sisa tagihan SO
    berttermin di alur invoice):
    - SO BELUM difakturkan → Uang Muka Penjualan (Setting Akun
      UANG_MUKA_PENJUALAN) — DP klasik; nanti diterapkan otomatis ke
      Piutang saat invoice SO diposting (post_sales_invoice).
    - SO SUDAH punya invoice AKTIF yang masih bersisa → akun Piutang
      invoice: pembayaran langsung melunasi piutang (jurnal D Kas/Bank,
      K Piutang) + tercatat sebagai alokasi pembayaran invoice sehingga
      sisa tagihannya ikut turun di Pelunasan/aging. Termin yang melebihi
      sisa piutang (termin bebas > GT) → kelebihannya ke Uang Muka.
    - Pilihan akun eksplisit (akun_perkiraan_id) SELALU dihormati.

    Return: (penerimaan, termin).
    """
    from app.services.kas_bank_service import create_penerimaan
    from app.models.transaksi.kas_bank.pembayaran import JENIS_TRANSAKSI_TERMIN_SO

    so = get_sales_order_by_id(db, so_id)
    if so is None:
        raise ValueError(f"Sales Order dengan ID {so_id} tidak ditemukan")
    if so.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Sales Order sudah dibatalkan — termin tidak bisa dibayar")

    termin = (
        db.query(SalesOrderTermin)
        .filter(
            SalesOrderTermin.id == termin_id,
            SalesOrderTermin.sales_order_id == so_id,
        )
        .first()
    )
    if termin is None:
        raise ValueError("Termin tidak ditemukan pada Sales Order ini")
    if termin.penerimaan_kas_id is not None:
        raise ValueError(
            f"Termin {termin.urutan} sudah dibayar (penerimaan "
            f"{termin.penerimaan.no_bukti if termin.penerimaan else '-'}). "
            "Batalkan penerimaan tersebut dulu bila ingin dibayar ulang."
        )

    # === Update #036 — akun lawan default kontekstual ===
    jumlah_termin = len(so.termin_pembayaran) if so.termin_pembayaran else 1
    keterangan_rincian = f"Termin {termin.urutan}/{jumlah_termin} SO {so.no_pesanan}"
    pemberi = so.pelanggan.nama if so.pelanggan else None
    nominal = safe_decimal(termin.nominal)

    target_invoice, sisa_target = None, Decimal("0")
    if akun_perkiraan_id is None:
        target_invoice, sisa_target = _invoice_aktif_untuk_termin(db, so)

    rincian_rows: list = []
    alokasi_nilai = Decimal("0")
    akun_piutang_id = None
    if target_invoice is not None and sisa_target > 0:
        akun_piutang_id = target_invoice.akun_kontrol_id or (
            so.pelanggan.akun_piutang_id if so.pelanggan else None
        )
    if akun_piutang_id is not None:
        # Pembayaran termin SETELAH invoice diposting → langsung ke akun
        # Piutang invoice (melunasi piutang + alokasi pembayaran invoice).
        alokasi_nilai = min(nominal, sisa_target)
        kelebihan = nominal - alokasi_nilai
        rincian_rows.append(
            {
                "akun_perkiraan_id": str(akun_piutang_id),
                "nilai": alokasi_nilai,
                "keterangan": (
                    f"Termin {termin.urutan}/{jumlah_termin} SO {so.no_pesanan} "
                    f"— diterapkan ke {target_invoice.no_invoice}"
                ),
            }
        )
        if kelebihan > 0:
            # Termin bebas melebihi sisa piutang → kelebihan tetap dicatat
            # sebagai uang muka (kewajiban — bisa direfund/di-tagih balik).
            akun_um = get_akun_id_or_raise(
                db, KEY_UANG_MUKA_PENJUALAN, "Uang Muka Penjualan (termin SO)"
            )
            rincian_rows.append(
                {
                    "akun_perkiraan_id": str(akun_um),
                    "nilai": kelebihan,
                    "keterangan": (
                        f"Termin {termin.urutan}/{jumlah_termin} SO {so.no_pesanan} "
                        "— kelebihan ke Uang Muka"
                    ),
                }
            )
    else:
        # SO belum difakturkan (atau akun dipilih eksplisit) — perilaku lama.
        akun_lawan_id = akun_perkiraan_id
        if akun_lawan_id is None:
            akun_lawan_id = get_akun_id_or_raise(
                db, KEY_UANG_MUKA_PENJUALAN, "Uang Muka Penjualan (termin SO)"
            )
        rincian_rows.append(
            {
                "akun_perkiraan_id": str(akun_lawan_id),
                "nilai": nominal,
                "keterangan": keterangan_rincian,
            }
        )

    penerimaan = create_penerimaan(
        db=db,
        no_nukti=no_nukti,
        tanggal=tanggal,
        kas_bank_id=kas_bank_id,
        rincian_data=rincian_rows,
        no_cek=None,
        pemberi=pemberi,
        catatan=(catatan or "").strip() or keterangan_rincian,
        auto_post_jurnal=False,
        created_by=created_by,
        jenis_transaksi=JENIS_TRANSAKSI_TERMIN_SO,
    )

    # Catat pelanggan SO di penerimaan (list Kas & Bank menampilkan pihak)
    penerimaan.pelanggan_id = so.pelanggan_id

    # Link termin → penerimaan (status DIBAYAR)
    termin.penerimaan_kas_id = penerimaan.id
    from datetime import UTC as _UTC
    termin.dibayar_pada = datetime.now(_UTC)

    # === Update #036 — alokasi pembayaran invoice untuk termin yang dibayar
    # SETELAH invoice diposting: sisa tagihan invoice turun sebesar bagian
    # yang masuk akun Piutang (setara pelunasan tunai). Pembatalan penerimaan
    # otomatis mengecualikannya — balances() hanya menghitung alokasi dari
    # penerimaan AKTIF (AUD2-E1), dan release_termin_links melepas termin.
    if alokasi_nilai > 0 and target_invoice is not None and akun_piutang_id is not None:
        penerimaan.alokasi.append(
            PaymentAllocation(
                sales_invoice_id=target_invoice.id,
                nilai=alokasi_nilai,
                akun_perkiraan_id=akun_piutang_id,
                terutang=sisa_target,
            )
        )

    db.add(penerimaan)
    db.add(termin)
    db.commit()
    db.refresh(penerimaan)
    db.refresh(termin)

    logger.info(
        f"Penerimaan termin SO {so.no_pesanan} termin {termin.urutan}/{jumlah_termin} "
        f"→ {penerimaan.no_bukti} ({termin.nominal})"
    )
    return penerimaan, termin


@atomic_accounting_write
def update_sales_order(
    db: Session,
    db_obj: SalesOrder,
    tanggal: Optional[datetime] = None,
    pelanggan_id: Optional[UUID] = None,
    syarat_bayar_id: Optional[UUID] = None,
    tanggal_pengiriman: Optional[datetime] = None,
    alamat_pengiriman: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
) -> SalesOrder:
    """Update data sales order (hanya field header, tidak re-calculate detail).

    Update ASAHI (revisi SO): ekspedisi/penjual dihapus; customer_code tidak
    dapat diubah — bagian dari nomor dokumen (immutable setelah create).

    Update #042 — PPN dihapus dari SO (param ppn dihapus; kolom model di-drop);
    SO TERKUNCI setelah ada invoice (guard bawah).
    """
    require_unposted(db_obj)
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Sales Order dengan status {db_obj.status.value} tidak bisa diupdate")

    # === Update #042 — SO TERKUNCI setelah ada invoice: revisi nilai pesanan
    # tidak boleh dilakukan bila sudah dibuatkan invoice (status apapun,
    # termasuk DIBATALKAN — jejak tagihan harus konsisten). Batalkan invoice
    # (jalur cancel yang kini cascade) bila perlu revisi SO. ===
    if db.query(SalesInvoice.id).filter(SalesInvoice.sales_order_id == db_obj.id).first() is not None:
        raise ValueError(
            "SO sudah memiliki invoice — terkunci (Update #042); "
            "batalkan invoice bila perlu revisi"
        )

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if pelanggan_id is not None:
        db_obj.pelanggan_id = pelanggan_id
    if syarat_bayar_id is not None:
        db_obj.syarat_bayar_id = syarat_bayar_id
    if tanggal_pengiriman is not None:
        db_obj.tanggal_pengiriman = tanggal_pengiriman
    if alamat_pengiriman is not None:
        db_obj.alamat_pengiriman = alamat_pengiriman
    if diskon_global is not None:
        db_obj.diskon_global = diskon_global
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    # Fix diskon global: hitung ulang total bila diskon_global diubah —
    # sebelumnya total lama (stale) tetap tersimpan meski persen berubah.
    if diskon_global is not None:
        from app.services.document_totals import refresh_totals
        refresh_totals(db_obj)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_sales_order(db: Session, db_obj: SalesOrder, user_id: Optional[UUID] = None) -> SalesOrder:
    """Batalkan sales order."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Sales Order sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"SalesOrder cancelled: {db_obj.no_pesanan}")
    return db_obj


# ==========================================
# SALES INVOICE
# ==========================================

# === Update #042 — DP/Pelunasan & sisa tagihan NILAI SO ===
def _invoice_billed(inv: SalesInvoice) -> Decimal:
    """Nilai-tagih invoice terhadap SO (#042): dp_pelunasan bila > 0,
    selain itu sub_total (kompatibilitas invoice lama tanpa dp_pelunasan,
    termasuk invoice #041 yang sub_total-nya sudah Σ dp per baris).

    === Update #068 — BIAYA TAMBAHAN invoice ikut dihitung sebagai
    nilai-tagih terhadap SO. Latar: biaya tambahan SO kini ikut
    difakturkan sebagai baris biaya invoice (prefill form Invoice, endpoint
    /sales-order/{id}/sisa #068). Tanpa dihitung di sini, SO yang punya
    biaya tambahan tidak pernah mencapai "tertagih penuh" (Σ nilai-tagih
    selalu kurang sebesar total biaya) → status SO nyangkut di DIPROSES
    selamanya meski seluruh alur (kirim → faktur → bayar) selesai.
    Biaya invoice TIDAK masuk dp_pelunasan (dari `grand_total invoice
    mode DP = dp + PPN + biaya − PPh23`, memasukkan biaya ke dp akan
    menagih dobel). Invoice standalone (tanpa SO) tidak memakai fungsi ini. ===
    """
    dp = safe_decimal(getattr(inv, "dp_pelunasan", 0) or 0)
    dasar = dp if dp > 0 else safe_decimal(getattr(inv, "sub_total", 0) or 0)
    biaya = safe_decimal(getattr(inv, "total_biaya_tambahan", 0) or 0)
    return dasar + biaya


def _so_sisa_nilai(
    db: Session, so_id: UUID, exclude_invoice_id: Optional[UUID] = None
) -> Decimal:
    """Sisa tagihan NILAI SO (#042) = max(0, SO.grand_total − Σ nilai-tagih
    invoice non-DIBATALKAN milik SO).

    Dipakai: guard create/update invoice (D — Σ nilai-tagih ≤ grand total SO),
    default dp_pelunasan (C), keterangan otomatis (E), endpoint
    /sales-order/{id}/sisa (J — field sisa_nilai), dan property ORM
    SalesInvoice.so_sisa_nilai (response soSisaNilai).
    """
    so = db.get(SalesOrder, so_id)
    grand_total = safe_decimal(getattr(so, "grand_total", 0) or 0) if so is not None else Decimal("0")
    query = db.query(SalesInvoice).filter(
        SalesInvoice.sales_order_id == so_id,
        SalesInvoice.status != StatusPenjualan.DIBATALKAN,
    )
    if exclude_invoice_id is not None:
        query = query.filter(SalesInvoice.id != exclude_invoice_id)
    billed = sum((_invoice_billed(inv) for inv in query.all()), Decimal("0"))
    return max(Decimal("0"), grand_total - billed)


def _so_fully_delivered(db: Session, so: SalesOrder) -> bool:
    """=== Update #070 — kelengkapan PENGIRIMAN SO (baris barang). ===

    Setiap baris BARANG SO (barang_id tidak NULL, qty > 0) sudah terkirim
    penuh (qty terkirim ≥ qty pesan) lewat PengirimanBarang berstatus
    SELESAI — over-delivery OK, mirror _refresh_fulfillment_status.
    Baris ITEM BEBAS (barang_id NULL — jasa/custom, #032) TIDAK dihitung:
    memang tidak bisa dikirim, cukup difakturkan. SO tanpa baris barang
    sama sekali → True (vacuous — tertagih penuh saja cukup).
    """
    goods_lines = [
        d for d in (so.details or [])
        if d.barang_id is not None and int(d.qty or 0) > 0
    ]
    if not goods_lines:
        return True
    delivered = dict(
        db.query(
            PengirimanBarangDetail.sales_order_detail_id,
            func.coalesce(func.sum(PengirimanBarangDetail.qty), 0),
        )
        .join(PengirimanBarang, PengirimanBarangDetail.pengiriman_id == PengirimanBarang.id)
        .filter(
            PengirimanBarang.sales_order_id == so.id,
            PengirimanBarang.status == StatusPenjualan.SELESAI,
            PengirimanBarangDetail.sales_order_detail_id.in_(
                [d.id for d in goods_lines]
            ),
        )
        .group_by(PengirimanBarangDetail.sales_order_detail_id)
        .all()
    )
    return all(
        int(delivered.get(d.id, 0) or 0) >= int(d.qty or 0)
        for d in goods_lines
    )


def sync_so_completion(db: Session, so_ids) -> None:
    """=== Update #046 → #070 — SO ikut SELESAI saat pekerjaannya tuntas. ===

    Dipanggil setelah titik-titik yang mengubah kelengkapan SO (invoice
    final/dibatalkan, pengiriman selesai/dibatalkan, posting penerimaan
    pelunasan / Voucher Memorial gagal tagih). Aturan BARU (#070 —
    keputusan user: status SO = penanda penyelesaian OPERASIONAL, TIDAK
    LAGI MENUNGGU PELUNASAN; status bayar dilacak di invoice & modul ln):

    - SO DIPROSES dinaikkan ke SELESAI bila KEDUA syarat terpenuhi:
      (1) seluruh nilai SO sudah ditagihkan — Σ nilai-tagih invoice
          non-batal (DP/pelunasan + biaya tambahan) ≥ grand total SO
          (toleransi 0,005) dan ada minimal satu invoice; dan
      (2) seluruh BARANG sudah terkirim penuh (_so_fully_delivered;
          baris item bebas dikecualikan).
    - Sebaliknya, SO yang sudah SELESAI dikembalikan ke DIPROSES bila
      syarat tidak lagi terpenuhi (invoice dibatalkan / pengiriman
      dibatalkan) — selama SO belum DIBATALKAN.

    (#046 dahulu juga mensyaratkan SEMUA invoice LUNAS/LEBIH_BAYAR —
    dicabut di #070; lihat README-UPDATE-070.)

    Idempotent; tidak melakukan commit (pemanggil memiliki transaksi —
    dekorator atomic_accounting_write / alur workflow yang commit).
    """
    for so_id in {s for s in so_ids if s}:
        so = db.get(SalesOrder, so_id)
        if so is None:
            continue
        current = getattr(so.status, "value", so.status)
        if current == StatusPenjualan.DIBATALKAN.value:
            continue
        invoices = [
            inv for inv in db.query(SalesInvoice)
            .filter(SalesInvoice.sales_order_id == so_id,
                    SalesInvoice.status != StatusPenjualan.DIBATALKAN)
            .all()
        ]
        billed = sum((_invoice_billed(inv) for inv in invoices), Decimal("0"))
        fully_billed = (
            billed > 0
            and safe_decimal(so.grand_total or 0) - billed <= Decimal("0.005")
        )
        complete = fully_billed and _so_fully_delivered(db, so)
        if complete:
            # Hanya DIPROSES yang dinaikkan (DRAFT belum difinalisasi —
            # tidak lompat ke SELESAI; SELESAI tetap).
            if current == StatusPenjualan.DIPROSES.value:
                so.status = StatusPenjualan.SELESAI
                db.add(so)
                logger.info(
                    f"Update #070 — SO {so.no_pesanan} → SELESAI "
                    f"(tertagih penuh & seluruh barang terkirim)"
                )
        elif current == StatusPenjualan.SELESAI.value:
            so.status = StatusPenjualan.DIPROSES
            db.add(so)
            logger.info(
                f"Update #070 — SO {so.no_pesanan} SELESAI → DIPROSES "
                f"(tagihan/pengiriman tidak lagi lengkap)"
            )


# === Update #043 — pola keterangan otomatis invoice ter-link SO (lama & baru) ===
# Bentuk lama (#042, dobel "SO"): "DP atas SO SO ASI/…" / "Pelunasan atas SO SO ASI/… — LUNAS".
# Bentuk baru (#043):      "DP atas SO ASI/…" / "Pelunasan atas SO ASI/… — LUNAS".
# Dipakai untuk MENGUPAS segmen otomatis dari nilai keterangan tersimpan saat
# invoice diedit (update_sales_invoice) supaya teks user tidak ikut tergandakan.
_AUTO_KET_INVOICE_RE = re.compile(
    r"^(?:DP|Pelunasan)\s+atas\s+(?:SO\s+)?(?:(?!—).)*(?:\s*—\s*LUNAS)?"
)


def _auto_keterangan_invoice(no_pesanan: str, sisa_sebelum: Decimal, dp_final: Decimal) -> str:
    """Keterangan otomatis invoice ter-link SO (#042 → #043).

    - sisa_SO_sebelum − dp_pelunasan ≤ 0.005 → "Pelunasan atas {no_pesanan} — LUNAS"
    - selain itu                          → "DP atas {no_pesanan}"

    Update #043 — fix dobel "SO": ``no_pesanan`` sudah berprefix "SO"
    (format "SO ASI/KODE/2026/001"), jadi string tidak lagi menambahkan kata
    "SO" sendiri (dulu menghasilkan "DP atas SO SO ASI/…").
    """
    if (sisa_sebelum - dp_final) <= Decimal("0.005"):
        return f"Pelunasan atas {no_pesanan} — LUNAS"
    return f"DP atas {no_pesanan}"


def _strip_auto_keterangan_invoice(keterangan: Optional[str]) -> str:
    """Buang segmen keterangan otomatis invoice ter-link SO dari sebuah teks;
    kembalikan sisa teks user setelah pemisah " — " (kosong bila tidak ada).

    Mengenali BENTUK LAMA #042 ("DP atas SO SO ASI/…", "Pelunasan atas SO SO
    ASI/… — LUNAS") maupun bentuk baru #043 ("DP atas SO ASI/…", "Pelunasan
    atas SO ASI/… — LUNAS") — nomor SO dianggap segmen tanpa pemisah "—",
    suffix "— LUNAS" ikut dibuang. Dipakai update_sales_invoice sebelum
    menggabung ulang teks user di belakang auto_ket baru (#043).
    """
    s = (keterangan or "").strip()
    if not s:
        return ""
    m = _AUTO_KET_INVOICE_RE.match(s)
    if not m:
        return s
    sisa = s[m.end():].strip()
    if sisa.startswith("—"):
        sisa = sisa.lstrip("—").strip()
    return sisa


def get_sales_invoice_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[SalesInvoice], int]:
    """Ambil daftar sales invoice dengan filter & pagination."""
    query = db.query(SalesInvoice).options(
        joinedload(SalesInvoice.pelanggan),
        joinedload(SalesInvoice.syarat_bayar),
        joinedload(SalesInvoice.sales_order),
        joinedload(SalesInvoice.creator),
        joinedload(SalesInvoice.details).joinedload(SalesInvoiceDetail.barang).joinedload(Barang.satuan),
        joinedload(SalesInvoice.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            SalesInvoice.no_invoice.ilike(pattern)
            | SalesInvoice.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(SalesInvoice.status == status)
    if pelanggan_id:
        query = query.filter(SalesInvoice.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(SalesInvoice.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(SalesInvoice.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(SalesInvoice.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_sales_invoice_by_id(db: Session, inv_id: UUID) -> Optional[SalesInvoice]:
    """Ambil 1 sales invoice berdasarkan ID dengan detail."""
    return (
        db.query(SalesInvoice)
        .options(
            joinedload(SalesInvoice.pelanggan),
            joinedload(SalesInvoice.syarat_bayar),
            joinedload(SalesInvoice.sales_order),
            joinedload(SalesInvoice.creator),
            joinedload(SalesInvoice.jurnal),
            joinedload(SalesInvoice.details).joinedload(SalesInvoiceDetail.barang).joinedload(Barang.satuan),
            joinedload(SalesInvoice.biaya_tambahan),
        )
        .filter(SalesInvoice.id == inv_id)
        .first()
    )


@atomic_accounting_write
def create_sales_invoice(
    db: Session,
    tanggal: datetime,
    pelanggan_id: UUID,
    details_data: list,
    biaya_data: Optional[list] = None,
    syarat_bayar_id: Optional[UUID] = None,
    sales_order_id: Optional[UUID] = None,
    ekspedisi: Optional[str] = None,
    tanggal_pengiriman: Optional[datetime] = None,
    alamat_pengiriman: Optional[str] = None,
    mata_uang: str = "IDR",
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    # === Update ASAHI — pilihan PPh23/PPN saat input SI ===
    ppn_applicable: bool = True,
    pph23_applicable: bool = False,
    pph23: Decimal = Decimal("2"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: Optional[UUID] = None,
    tanggal_jatuh_tempo=None,
    # === Update #042 — DP/Pelunasan (field header): "nilai yang ditagih
    # invoice ini". Ter-link SO: None = auto-isi sisa tagihan SO; WAJIB > 0
    # dan ≤ sisa. Standalone: None/0 = perilaku lama; bila diisi harus
    # 0 < x ≤ Σ gross baris (harga × qty). > 0 → refresh_totals
    # mendistribusikan proporsional ke baris. ===
    dp_pelunasan: Optional[Decimal] = None,
) -> SalesInvoice:
    """Buat SalesInvoice baru beserta detail + biaya tambahan.

    Update #042 — DP/Pelunasan header (dp_pelunasan):
    - Invoice ter-link SO: nilai yang ditagih = dp_pelunasan (default sisa
      tagihan SO bila tidak dikirim); guard Σ nilai-tagih invoice SO ≤
      grand_total SO (D); keterangan otomatis "DP atas SO …" / "Pelunasan
      atas SO … — LUNAS" (E); baris didistribusikan proporsional (C).
    - Standalone tanpa SO: dp_pelunasan 0/None → perilaku lama.
    """
    try:
        order = None
        if sales_order_id:
            order = db.get(SalesOrder, sales_order_id)
            if order is None or order.pelanggan_id != pelanggan_id:
                raise ValueError("Sales Order tidak tersedia atau pelanggan tidak cocok")
            if order.status == StatusPenjualan.DIBATALKAN:
                raise ValueError("Sales Order sudah dibatalkan")
            if order.jurnal_umum_id:
                from app.models.transaksi.jurnal import JurnalUmum
                if not db.query(JurnalUmum.id).filter(JurnalUmum.reversal_of_id == order.jurnal_umum_id).first():
                    raise ValueError("Sales Order lama masih memiliki jurnal. Rekonsiliasi jurnal order sebelum membuat invoice.")
        biaya_data = biaya_data or []

        # === Update #032 — validasi baris invoice: barang master ATAU item
        # bebas (jasa/custom — barang_id None + item_nama wajib). ===
        for i, d in enumerate(details_data, start=1):
            has_barang = d.get("barang_id") is not None
            item_nama = (d.get("item_nama") or "").strip()
            if not has_barang and not item_nama:
                raise ValueError(
                    f"Baris invoice {i}: pilih barang dari master ATAU isi nama "
                    "item bebas (jasa/custom)"
                )
            if has_barang:
                d["item_nama"] = None
                d["item_satuan"] = None
            else:
                d["item_nama"] = item_nama
                d["item_satuan"] = (d.get("item_satuan") or "").strip() or None

        # Validasi pelanggan
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # Hitung sub_total dan total_diskon dari detail
        sub_total = Decimal("0")
        total_diskon = Decimal("0")
        for d in details_data:
            harga = safe_decimal(d.get("harga"))
            qty = safe_int(d.get("qty"))
            diskon = safe_decimal(d.get("diskon"))
            line_total = harga * qty
            diskon_nilai = line_total * diskon / Decimal("100")
            sub_total += line_total
            total_diskon += diskon_nilai
            d["sub_total"] = line_total - diskon_nilai

        total_biaya_tambahan = _hitung_total_biaya(biaya_data)
        total_ppn, total_pph23, grand_total = _hitung_grand_total(
            sub_total, total_diskon, ppn, total_biaya_tambahan,
            ppn_applicable=ppn_applicable,
            pph23_pct=pph23,
            pph23_applicable=pph23_applicable,
        )

        # ==============================================================
        # === Update #042 — resolusi & validasi dp_pelunasan (C)/(D)/(E) ===
        # ==============================================================
        dp_final = Decimal("0")
        if order is not None:
            # Invoice ter-link SO: WAJIB > 0 dan ≤ sisa tagihan SO; default
            # (tidak dikirim / None) = sisa SO (auto-terisi). Guard (D):
            # Σ nilai-tagih invoice SO tidak boleh melebihi grand_total SO.
            sisa_sebelum = _so_sisa_nilai(db, order.id)
            if dp_pelunasan is None or safe_decimal(dp_pelunasan) <= 0:
                dp_final = sisa_sebelum
            else:
                dp_final = safe_decimal(dp_pelunasan)
            if dp_final <= 0:
                raise ValueError(
                    "Nilai DP/Pelunasan invoice wajib lebih dari 0 — sisa tagihan "
                    f"SO {order.no_pesanan} sudah habis (sisa {sisa_sebelum})"
                )
            if dp_final > sisa_sebelum + Decimal("0.005"):
                raise ValueError(
                    f"Nilai DP/Pelunasan ({dp_final}) melebihi sisa tagihan SO "
                    f"{order.no_pesanan} (sisa {sisa_sebelum})"
                )
            # Keterangan otomatis (E): sisa_SO_sebelum − dp_pelunasan ≤ 0.005
            # → "Pelunasan atas {no_pesanan} — LUNAS"; selain itu → "DP atas
            # {no_pesanan}". Update #043 — no_pesanan sudah berprefix "SO",
            # helper tidak lagi menambahkan kata "SO" (dulu dobel "SO SO").
            # Keterangan user (bila diisi) ditambahkan di belakang.
            # === Update #068 — Σ biaya tambahan invoice ikut dipakai sebagai
            # "nilai yang ditagih tahap ini" (biaya SO ditarik ke baris biaya
            # invoice; nilai-tagih = dp + Σ biaya — paritas _invoice_billed)
            # supaya label "Pelunasan … — LUNAS" tetap akurat untuk SO
            # ber-biaya yang difakturkan habis sekali jalan. ===
            biaya_tarik = sum(
                (safe_decimal(b.get("jumlah")) for b in (biaya_data or [])),
                Decimal("0"),
            )
            auto_ket = _auto_keterangan_invoice(
                order.no_pesanan, sisa_sebelum, dp_final + biaya_tarik
            )
            keterangan = (
                f"{auto_ket} — {keterangan}" if (keterangan or "").strip() else auto_ket
            )
        elif dp_pelunasan is not None and safe_decimal(dp_pelunasan) > 0:
            # Standalone (tanpa SO): boleh 0/None (perilaku lama); bila diisi
            # harus 0 < x ≤ Σ gross baris (harga × qty).
            dp_final = safe_decimal(dp_pelunasan)
            gross_total = sum(
                (safe_decimal(d.get("harga")) * safe_int(d.get("qty")) for d in details_data),
                Decimal("0"),
            )
            if dp_final > gross_total + Decimal("0.005"):
                raise ValueError(
                    f"Nilai DP/Pelunasan ({dp_final}) melebihi total nilai baris "
                    f"invoice ({gross_total})"
                )

        # Generate nomor invoice
        # Update ASAHI: pola penomoran seragam "INV ASI/YYYY/NNN" (reset tahunan).
        # === Update ASAHI #019 — prefix & kode perusahaan via Preferensi ===
        # (doc_type SALES_INVOICE; fallback "INV" — perilaku lama bila belum diubah).
        no_invoice = get_nomor_dokumen_tahunan(
            db, SalesInvoice, prefix="INV",
            no_column="no_invoice", tanggal=tanggal.date(),
            doc_type="SALES_INVOICE",
        )

        # Buat header
        inv = SalesInvoice(
            no_invoice=no_invoice,
            tanggal=tanggal,
            pelanggan_id=pelanggan_id,
            syarat_bayar_id=syarat_bayar_id,
            sales_order_id=sales_order_id,
            ekspedisi=ekspedisi,
            tanggal_pengiriman=tanggal_pengiriman,
            alamat_pengiriman=alamat_pengiriman,
            mata_uang=mata_uang,
            diskon_global=diskon_global,
            ppn=ppn,
            # === Update ASAHI — pilihan PPh23/PPN saat input SI ===
            ppn_applicable=ppn_applicable,
            pph23_applicable=pph23_applicable,
            pph23=pph23,
            total_pph23=total_pph23,
            sub_total=sub_total,
            total_diskon=total_diskon,
            total_ppn=total_ppn,
            total_biaya_tambahan=total_biaya_tambahan,
            grand_total=grand_total,
            # === Update #042 — DP/Pelunasan header: nilai yang ditagih
            # invoice ini (0 = tagih penuh, perilaku lama). ===
            dp_pelunasan=dp_final,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusPenjualan.DRAFT,
            keterangan=keterangan,
            created_by=created_by,
        )
        db.add(inv)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = SalesInvoiceDetail(
                sales_invoice_id=inv.id,
                # Update #032 — barang_id bisa None (baris item bebas jasa/
                # custom dari penawaran → SO; wajib item_nama bila tanpa barang).
                barang_id=d.get("barang_id"),
                item_nama=d.get("item_nama"),
                item_satuan=d.get("item_satuan"),
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=Decimal(str(d["sub_total"])),
                # === Update #042 — dp per-baris dihapus: porsi DP/Pelunasan
                # baris dihitung refresh_totals (distribusi proporsional
                # dp_pelunasan) setelah flush di bawah. ===
                # === Phase 4 — source-line trace (Roadmap §14) ===
                sales_order_detail_id=d.get("sales_order_detail_id"),
                delivery_detail_id=d.get("delivery_detail_id"),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, inv, biaya_data, "sales_invoice_id")

        # Auto-post jurnal (piutang dagang D, pendapatan penjualan K)
        # Guard: skip jika pelanggan belum punya akun piutang (Phase 3 akan ganti mekanisme COA)
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(inv)
        if auto_post_jurnal:
            post_sales_invoice(db, inv, created_by)

        from app.services.invoice_terms import set_due_date
        set_due_date(db, inv, tanggal_jatuh_tempo)
        db.commit()
        db.refresh(inv)
        logger.info(f"SalesInvoice created: {no_invoice} | grand_total={grand_total}")
        return inv

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating SalesInvoice: {e}")
        raise


@atomic_accounting_write
def update_sales_invoice(
    db: Session,
    db_obj: SalesInvoice,
    tanggal: Optional[datetime] = None,
    pelanggan_id: Optional[UUID] = None,
    syarat_bayar_id: Optional[UUID] = None,
    sales_order_id: Optional[UUID] = None,
    ekspedisi: Optional[str] = None,
    tanggal_pengiriman: Optional[datetime] = None,
    alamat_pengiriman: Optional[str] = None,
    mata_uang: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    # === Update ASAHI — pilihan PPh23/PPN saat update SI ===
    ppn_applicable: Optional[bool] = None,
    pph23_applicable: Optional[bool] = None,
    pph23: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    tanggal_jatuh_tempo=None,
    details: Optional[list] = None,
    # === Update #042 — DP/Pelunasan opsional saat update: bila dikirim →
    # divalidasi ulang terhadap sisa tagihan SO (invoice ini dikecualikan);
    # None = pertahankan nilai tersimpan. ===
    dp_pelunasan: Optional[Decimal] = None,
) -> SalesInvoice:
    """Update data sales invoice (field header + opsional replace detail).

    Update #042 — dp_pelunasan: bila dikirim, divalidasi ulang terhadap sisa
    tagihan SO (invoice ini dikecualikan dari Σ nilai-tagih) / Σ gross baris
    untuk invoice standalone.
    """
    require_unposted(db_obj)
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Sales Invoice dengan status {db_obj.status.value} tidak bisa diupdate")

    # === Update #042 — validasi ulang dp_pelunasan bila dikirim ===
    if dp_pelunasan is not None:
        dp_val = safe_decimal(dp_pelunasan)
        # SO efektif SETELAH request ini (sales_order_id bisa ikut diganti
        # dalam payload yang sama).
        so_id_eff = sales_order_id if sales_order_id is not None else db_obj.sales_order_id
        if so_id_eff is not None:
            sisa_lain = _so_sisa_nilai(db, so_id_eff, exclude_invoice_id=db_obj.id)
            so_src = db.get(SalesOrder, so_id_eff)
            if dp_val <= 0:
                raise ValueError("Nilai DP/Pelunasan invoice wajib lebih dari 0")
            if dp_val > sisa_lain + Decimal("0.005"):
                no_so = getattr(so_src, "no_pesanan", "-")
                raise ValueError(
                    f"Nilai DP/Pelunasan ({dp_val}) melebihi sisa tagihan SO {no_so} "
                    f"di luar invoice ini (sisa {sisa_lain})"
                )
        elif dp_val > 0:
            # Standalone: bila diisi harus 0 < x ≤ Σ gross baris (detail baru
            # bila replace, selain itu detail tersimpan).
            rows = details if details is not None else [
                {"harga": d.harga, "qty": d.qty} for d in (db_obj.details or [])
            ]
            gross_total = sum(
                (safe_decimal(r.get("harga")) * safe_int(r.get("qty")) for r in rows),
                Decimal("0"),
            )
            if dp_val > gross_total + Decimal("0.005"):
                raise ValueError(
                    f"Nilai DP/Pelunasan ({dp_val}) melebihi total nilai baris "
                    f"invoice ({gross_total})"
                )

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if pelanggan_id is not None:
        db_obj.pelanggan_id = pelanggan_id
    if syarat_bayar_id is not None:
        db_obj.syarat_bayar_id = syarat_bayar_id
    if sales_order_id is not None:
        db_obj.sales_order_id = sales_order_id
    if ekspedisi is not None:
        db_obj.ekspedisi = ekspedisi
    if tanggal_pengiriman is not None:
        db_obj.tanggal_pengiriman = tanggal_pengiriman
    if alamat_pengiriman is not None:
        db_obj.alamat_pengiriman = alamat_pengiriman
    if mata_uang is not None:
        db_obj.mata_uang = mata_uang
    if diskon_global is not None:
        db_obj.diskon_global = diskon_global
    if ppn is not None:
        db_obj.ppn = ppn
    # === Update ASAHI — pilihan PPh23/PPN ===
    if ppn_applicable is not None:
        db_obj.ppn_applicable = ppn_applicable
    if pph23_applicable is not None:
        db_obj.pph23_applicable = pph23_applicable
    if pph23 is not None:
        db_obj.pph23 = pph23
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal
    # === Update #042 — simpan dp_pelunasan (None = pertahankan nilai
    # tersimpan; sudah tervalidasi ulang di atas). ===
    if dp_pelunasan is not None:
        db_obj.dp_pelunasan = safe_decimal(dp_pelunasan)

    # === Update #043 — REGENERASI keterangan otomatis invoice ter-link SO
    # (E): prefix DP/Pelunasan dihitung ulang dari sisa tagihan SO terkini
    # (invoice ini dikecualikan) sehingga label "— LUNAS" ikut benar saat
    # dp_pelunasan / link SO diedit. Teks user dipertahankan: segmen otomatis
    # lama (termasuk bentuk dobel "SO SO" hasil #042) diupas dari nilai
    # keterangan (tersimpan/baru saja diset), sisanya digabung ulang di
    # belakang auto_ket baru. ===
    so_id_ket = sales_order_id if sales_order_id is not None else db_obj.sales_order_id
    if so_id_ket is not None:
        so_src = db.get(SalesOrder, so_id_ket)
        dp_eff = dp_pelunasan if dp_pelunasan is not None else safe_decimal(
            getattr(db_obj, "dp_pelunasan", 0) or 0
        )
        sisa_sebelum = _so_sisa_nilai(db, so_id_ket, exclude_invoice_id=db_obj.id)
        sisa_user = _strip_auto_keterangan_invoice(db_obj.keterangan)
        auto_ket = _auto_keterangan_invoice(
            (so_src.no_pesanan if so_src else "-"), sisa_sebelum, dp_eff
        )
        db_obj.keterangan = f"{auto_ket} — {sisa_user}" if sisa_user else auto_ket

    # Update #3 — penggantian baris detail (persist link source-line
    # sales_order_detail_id / delivery_detail_id) saat invoice masih draft.
    if details is not None:
        # Update #032 — baris item bebas (jasa/custom): barang_id None +
        # item_nama wajib; normalisasi sama dengan create.
        for i, d in enumerate(details, start=1):
            has_barang = d.get("barang_id") is not None
            item_nama = (d.get("item_nama") or "").strip()
            if not has_barang and not item_nama:
                raise ValueError(
                    f"Baris invoice {i}: pilih barang dari master ATAU isi nama "
                    "item bebas (jasa/custom)"
                )
            d["item_nama"] = item_nama if not has_barang else None
            d["item_satuan"] = (d.get("item_satuan") or "").strip() or None if not has_barang else None
        db_obj.details.clear()
        db.flush()
        for d in details:
            db_obj.details.append(SalesInvoiceDetail(
                barang_id=d.get("barang_id"),
                item_nama=d.get("item_nama"),
                item_satuan=d.get("item_satuan"),
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=Decimal(str(d.get("sub_total", 0))),
                # === Update #042 — dp per-baris DIHAPUS; porsi DP/Pelunasan
                # dihitung ulang refresh_totals di bawah (distribusi
                # proporsional dp_pelunasan header). ===
                sales_order_detail_id=d.get("sales_order_detail_id"),
                delivery_detail_id=d.get("delivery_detail_id"),
            ))
        db.flush()

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    from app.services.invoice_terms import set_due_date
    if tanggal_jatuh_tempo is not None or tanggal is not None or syarat_bayar_id is not None:
        set_due_date(db, db_obj, tanggal_jatuh_tempo)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_sales_invoice(db: Session, db_obj: SalesInvoice, user_id: Optional[UUID] = None) -> SalesInvoice:
    """Batalkan sales invoice."""
    from app.services.settlement_service import require_no_settlements
    require_no_settlements(db, db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Sales Invoice sudah dibatalkan")
    if db.query(SalesRetur.id).filter(
        SalesRetur.sales_invoice_id == db_obj.id,
        SalesRetur.status != StatusPenjualan.DIBATALKAN,
    ).first():
        raise ValueError("Invoice masih memiliki retur aktif. Selesaikan pembatalan/koreksi retur terlebih dahulu.")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"SalesInvoice cancelled: {db_obj.no_invoice}")
    return db_obj


# ==========================================
# SALES RETUR
# ==========================================

def get_sales_retur_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[SalesRetur], int]:
    """Ambil daftar sales retur dengan filter & pagination."""
    query = db.query(SalesRetur).options(
        joinedload(SalesRetur.sales_invoice),
        joinedload(SalesRetur.pelanggan),
        joinedload(SalesRetur.creator),
        joinedload(SalesRetur.details).joinedload(SalesReturDetail.barang).joinedload(Barang.satuan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            SalesRetur.no_retur.ilike(pattern)
            | SalesRetur.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(SalesRetur.status == status)
    if pelanggan_id:
        query = query.filter(SalesRetur.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(SalesRetur.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(SalesRetur.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(SalesRetur.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_sales_retur_by_id(db: Session, retur_id: UUID) -> Optional[SalesRetur]:
    """Ambil 1 sales retur berdasarkan ID dengan detail."""
    return (
        db.query(SalesRetur)
        .options(
            joinedload(SalesRetur.sales_invoice),
            joinedload(SalesRetur.pelanggan),
            joinedload(SalesRetur.creator),
            joinedload(SalesRetur.jurnal),
            joinedload(SalesRetur.details).joinedload(SalesReturDetail.barang).joinedload(Barang.satuan),
        )
        .filter(SalesRetur.id == retur_id)
        .first()
    )


@atomic_accounting_write
def create_sales_retur(
    db: Session,
    tanggal: datetime,
    sales_invoice_id: UUID,
    pelanggan_id: UUID,
    details_data: list,
    alamat_pengembalian: Optional[str] = None,
    no_pengembalian: Optional[str] = None,
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: Optional[UUID] = None,
    gudang_id: Optional[UUID] = None,
    pengiriman_id: Optional[UUID] = None,
) -> SalesRetur:
    """Buat SalesRetur baru beserta detail.
    Jurnal: D - Retur Penjualan / HPP, K - Piutang Dagang
    """
    try:
        # Validasi pelanggan
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # Hitung sub_total dari detail
        sub_total = Decimal("0")
        for d in details_data:
            harga = safe_decimal(d.get("harga"))
            qty = safe_int(d.get("qty"))
            line_total = harga * qty
            sub_total += line_total
            d["sub_total"] = line_total

        total_ppn, _total_pph23, grand_total = _hitung_grand_total(
            sub_total, Decimal("0"), ppn, Decimal("0")
        )

        # Generate nomor retur
        # Update ASAHI: pola penomoran seragam "RET-J ASI/YYYY/NNN" (reset tahunan).
        # === Update ASAHI #019 — prefix & kode perusahaan via Preferensi ===
        # (doc_type RETUR_PENJUALAN; fallback "RET-J" — perilaku lama bila belum diubah).
        no_retur = get_nomor_dokumen_tahunan(
            db, SalesRetur, prefix="RET-J",
            no_column="no_retur", tanggal=tanggal.date(),
            doc_type="RETUR_PENJUALAN",
        )

        # Buat header
        retur = SalesRetur(
            pengiriman_id=pengiriman_id,
            gudang_id=gudang_id,
            no_retur=no_retur,
            tanggal=tanggal,
            sales_invoice_id=sales_invoice_id,
            pelanggan_id=pelanggan_id,
            alamat_pengembalian=alamat_pengembalian,
            no_pengembalian=no_pengembalian,
            diskon_global=diskon_global,
            ppn=ppn,
            sub_total=sub_total,
            total_ppn=total_ppn,
            grand_total=grand_total,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusPenjualan.DRAFT,
            keterangan=keterangan,
            created_by=created_by,
        )
        db.add(retur)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = SalesReturDetail(
                sales_retur_id=retur.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                sub_total=Decimal(str(d["sub_total"])),
                # === Phase 4 — source-line trace (Roadmap §16) ===
                sales_invoice_detail_id=d.get("sales_invoice_detail_id"),
            )
            db.add(detail)

        # Auto-post jurnal (D: Retur Penjualan, K: Piutang Dagang)
        # Guard: skip jika pelanggan belum punya akun piutang (Phase 3 akan ganti mekanisme COA)
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(retur)
        if auto_post_jurnal:
            post_sales_retur(db, retur, created_by)

        db.commit()
        db.refresh(retur)
        logger.info(f"SalesRetur created: {no_retur} | grand_total={grand_total}")
        return retur

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating SalesRetur: {e}")
        raise


@atomic_accounting_write
def update_sales_retur(
    db: Session,
    db_obj: SalesRetur,
    tanggal: Optional[datetime] = None,
    sales_invoice_id: Optional[UUID] = None,
    pelanggan_id: Optional[UUID] = None,
    alamat_pengembalian: Optional[str] = None,
    no_pengembalian: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    gudang_id: Optional[UUID] = None,
    pengiriman_id: Optional[UUID] = None,
) -> SalesRetur:
    """Update data sales retur (hanya field header)."""
    require_unposted(db_obj)
    if pengiriman_id is not None:
        db_obj.pengiriman_id = pengiriman_id
    if gudang_id is not None:
        db_obj.gudang_id = gudang_id
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Sales Retur dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if sales_invoice_id is not None:
        db_obj.sales_invoice_id = sales_invoice_id
    if pelanggan_id is not None:
        db_obj.pelanggan_id = pelanggan_id
    if alamat_pengembalian is not None:
        db_obj.alamat_pengembalian = alamat_pengembalian
    if no_pengembalian is not None:
        db_obj.no_pengembalian = no_pengembalian
    if diskon_global is not None:
        db_obj.diskon_global = diskon_global
    if ppn is not None:
        db_obj.ppn = ppn
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_sales_retur(db: Session, db_obj: SalesRetur, user_id: Optional[UUID] = None) -> SalesRetur:
    """Batalkan sales retur."""
    require_no_stock_movement(db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Sales Retur sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"SalesRetur cancelled: {db_obj.no_retur}")
    return db_obj


# ==========================================
# PENGIRIMAN BARANG
# ==========================================

def get_pengiriman_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    pelanggan_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PengirimanBarang], int]:
    """Ambil daftar pengiriman barang dengan filter & pagination."""
    query = db.query(PengirimanBarang).options(
        joinedload(PengirimanBarang.sales_order),
        joinedload(PengirimanBarang.pelanggan),
        joinedload(PengirimanBarang.creator),
        joinedload(PengirimanBarang.details).joinedload(PengirimanBarangDetail.barang),
        joinedload(PengirimanBarang.details).joinedload(PengirimanBarangDetail.satuan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PengirimanBarang.no_surat_jalan.ilike(pattern)
            | PengirimanBarang.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PengirimanBarang.status == status)
    if pelanggan_id:
        query = query.filter(PengirimanBarang.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(PengirimanBarang.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PengirimanBarang.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PengirimanBarang.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_pengiriman_by_id(db: Session, pengiriman_id: UUID) -> Optional[PengirimanBarang]:
    """Ambil 1 pengiriman berdasarkan ID dengan detail."""
    return (
        db.query(PengirimanBarang)
        .options(
            joinedload(PengirimanBarang.sales_order),
            joinedload(PengirimanBarang.pelanggan),
            joinedload(PengirimanBarang.creator),
            joinedload(PengirimanBarang.details).joinedload(PengirimanBarangDetail.barang),
            joinedload(PengirimanBarang.details).joinedload(PengirimanBarangDetail.satuan),
        )
        .filter(PengirimanBarang.id == pengiriman_id)
        .first()
    )


@atomic_accounting_write
def create_pengiriman(
    db: Session,
    tanggal: datetime,
    sales_order_id: UUID,
    pelanggan_id: UUID,
    details_data: list,
    ekspedisi: Optional[str] = None,
    alamat_pengiriman: Optional[str] = None,
    keterangan: Optional[str] = None,
    created_by: Optional[UUID] = None,
    gudang_id: Optional[UUID] = None,
) -> PengirimanBarang:
    """Buat PengirimanBarang baru beserta detail.
    Tidak ada jurnal posting (pengiriman tidak mengubah keuangan langsung).

    Fix persist sales_order_detail_id (temuan minor): field dari schema
    PengirimanBarangDetailCreate sebelumnya diabaikan sehingga kolom DB
    `sales_order_detail_id` selalu NULL dan validasi over-delivery per baris
    (finish_pengiriman) tidak pernah aktif. Sekarang:
    - disimpan ke baris detail (nullable, backward compatible)
    - divalidasi: SO detail harus milik sales_order_id dokumen ini & barang sama
    - over-delivery dicek agregat per SO line (menggabungkan duplikasi baris
      dalam satu DO) sehingga tolakan terjadi lebih awal saat create
    """
    try:
        # Validasi pelanggan
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # Generate nomor dokumen pengiriman (Delivery Order)
        # Update ASAHI #3: prefix nomor diganti "KB" → "DO" (pola "DO ASI/YYYY/NNN",
        # reset tahunan). Dokumen lama KB ASI/... tetap memakai nomor lamanya.
        # === Update ASAHI #019 — prefix & kode perusahaan via Preferensi ===
        # (doc_type PENGIRIMAN_BARANG; fallback "DO" — perilaku lama bila belum diubah).
        no_surat_jalan = get_nomor_dokumen_tahunan(
            db, PengirimanBarang, prefix="DO",
            no_column="no_surat_jalan", tanggal=tanggal.date(),
            doc_type="PENGIRIMAN_BARANG",
        )

        # === Fix persist sales_order_detail_id: validasi referensi + over-delivery ===
        from collections import defaultdict
        from app.services.sales_validation import validate_over_delivery
        from app.models.detail.sales_order_detail import SalesOrderDetail

        so_qty_map: dict = defaultdict(int)
        # === Update #041 — baris ITEM BEBAS (SO manufaktur/jasa/custom): kini
        #     BOLEH dikirim sebagai baris surat jalan (tanpa stok/HPP).
        #     Validasi mirror #032: barang master ATAU item_nama. ===
        for i, d in enumerate(details_data, start=1):
            has_barang = d.get("barang_id") is not None
            item_nama = (d.get("item_nama") or "").strip()
            if not has_barang and not item_nama:
                raise ValueError(
                    f"Baris {i}: pilih barang dari master ATAU isi nama item bebas"
                )
            if has_barang:
                d["item_nama"] = None
                d["item_satuan"] = None
            else:
                d["item_nama"] = item_nama
                d["item_satuan"] = (d.get("item_satuan") or "").strip() or None

        for d in details_data:
            sod_id = d.get("sales_order_detail_id")
            if not sod_id:
                continue
            sod = db.get(SalesOrderDetail, sod_id)
            if sod is None or str(sod.sales_order_id) != str(sales_order_id):
                raise ValueError(
                    f"Baris detail menunjuk SalesOrderDetail yang tidak termasuk dalam "
                    f"Sales Order dokumen ini. Pengiriman {no_surat_jalan} dibatalkan."
                )
            # Update #041 — baris SO item bebas kini BOLEH dikirim (surat jalan
            # tanpa stok); kecocokan barang hanya dicek untuk baris master.
            if sod.barang_id is not None and str(sod.barang_id) != str(d.get("barang_id")):
                raise ValueError(
                    f"Barang pada baris detail tidak sama dengan barang di line SO "
                    f"({sod.barang.kode if sod.barang else sod.barang_id}). "
                    f"Pengiriman {no_surat_jalan} dibatalkan."
                )
            so_qty_map[sod_id] += int(d["qty"])
            # === Update #066 — snapshot harga/diskon line SO untuk kolom
            # nominal DO (fallback bila payload FE tidak membawa nilai;
            # prioritas tetap nilai eksplisit dari form). ===
            d["_so_harga"] = safe_decimal(getattr(sod, "harga", None))
            d["_so_diskon"] = safe_decimal(getattr(sod, "diskon", None))

        for sod_id, qty_aggregate in so_qty_map.items():
            try:
                validate_over_delivery(
                    db, sod_id, qty_aggregate,
                    context=f"Pengiriman {no_surat_jalan}",
                )
            except Exception as e:
                # HTTPException dari validate_over_delivery → ValueError (400)
                raise ValueError(str(getattr(e, "detail", str(e))))

        # Buat header
        pengiriman = PengirimanBarang(
            gudang_id=gudang_id,
            no_surat_jalan=no_surat_jalan,
            tanggal=tanggal,
            sales_order_id=sales_order_id,
            pelanggan_id=pelanggan_id,
            ekspedisi=ekspedisi,
            alamat_pengiriman=alamat_pengiriman,
            keterangan=keterangan,
            status=StatusPenjualan.DIPROSES,
            created_by=created_by,
        )
        db.add(pengiriman)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PengirimanBarangDetail(
                pengiriman_id=pengiriman.id,
                # Update #041 — barang_id bisa None (baris ITEM BEBAS — SO
                # manufaktur/jasa/custom; nama + satuan bebas tersimpan di
                # baris ini, tidak menyentuh stok/HPP saat finish).
                barang_id=d.get("barang_id"),
                item_nama=d.get("item_nama"),
                item_satuan=d.get("item_satuan"),
                qty=int(d["qty"]),
                satuan_id=d.get("satuan_id"),
                sales_order_detail_id=d.get("sales_order_detail_id"),
                # === Update #066 — snapshot harga/diskon (kolom nominal DO):
                # prioritas payload FE, fallback harga/diskon line SO.
                # Murni informatif — tidak memengaruhi stok/HPP/jurnal. ===
                harga=(
                    safe_decimal(d.get("harga"))
                    if d.get("harga") is not None
                    else d.get("_so_harga") or Decimal("0")
                ),
                diskon=(
                    safe_decimal(d.get("diskon"))
                    if d.get("diskon") is not None
                    else d.get("_so_diskon") or Decimal("0")
                ),
            )
            db.add(detail)

        db.commit()
        db.refresh(pengiriman)
        logger.info(f"PengirimanBarang created: {no_surat_jalan}")
        return pengiriman

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PengirimanBarang: {e}")
        raise


@atomic_accounting_write
def update_pengiriman(
    db: Session,
    db_obj: PengirimanBarang,
    tanggal: Optional[datetime] = None,
    sales_order_id: Optional[UUID] = None,
    pelanggan_id: Optional[UUID] = None,
    ekspedisi: Optional[str] = None,
    alamat_pengiriman: Optional[str] = None,
    keterangan: Optional[str] = None,
    gudang_id: Optional[UUID] = None,
) -> PengirimanBarang:
    """Update data pengiriman (hanya field header)."""
    require_unposted(db_obj)
    if gudang_id is not None:
        db_obj.gudang_id = gudang_id
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Pengiriman dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if sales_order_id is not None:
        db_obj.sales_order_id = sales_order_id
    if pelanggan_id is not None:
        db_obj.pelanggan_id = pelanggan_id
    if ekspedisi is not None:
        db_obj.ekspedisi = ekspedisi
    if alamat_pengiriman is not None:
        db_obj.alamat_pengiriman = alamat_pengiriman
    if keterangan is not None:
        db_obj.keterangan = keterangan

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def _refresh_fulfillment_status(db: Session, sales_order_id: UUID) -> None:
    """Update #7 (M-05): hitung ulang ``SalesOrder.fulfillment_status``.

    Kolom ini sebelumnya tidak pernah ditulis oleh kode mana pun (field mati)
    sehingga SO yang sudah 100% dikirim tetap tampil "-" di UI.

    Aturan (Catatan Sales Order §8):
    - ``OPEN``      — belum ada baris SO yang terkirim (semua delivered 0)
    - ``PARTIAL``   — sebagian baris/qty terkirim (ada 0 < delivered < qty,
                      atau sebagian baris penuh & sisanya belum)
    - ``FULFILLED`` — semua baris SO terkirim penuh (delivered >= qty)
    (``CLOSED`` tidak pernah diset otomatis — butuh keputusan manual.)

    Sumber pengiriman dihitung HANYA dari PengirimanBarang berstatus SELESAI
    (pengiriman DIPROSES belum mengurangi stok; DIBATALKAN tidak berlaku).
    Satu query agregat ``GROUP BY sales_order_detail_id`` untuk semua baris
    (menghindari N+1 per detail).
    """
    if sales_order_id is None:
        return

    detail_rows = (
        db.query(SalesOrderDetail.id, SalesOrderDetail.qty)
        .filter(SalesOrderDetail.sales_order_id == sales_order_id)
        .all()
    )
    if not detail_rows:
        return

    delivered = dict(
        db.query(
            PengirimanBarangDetail.sales_order_detail_id,
            func.coalesce(func.sum(PengirimanBarangDetail.qty), 0),
        )
        .join(PengirimanBarang, PengirimanBarangDetail.pengiriman_id == PengirimanBarang.id)
        .filter(
            PengirimanBarang.sales_order_id == sales_order_id,
            PengirimanBarang.status == StatusPenjualan.SELESAI,
            PengirimanBarangDetail.sales_order_detail_id.in_(
                [row.id for row in detail_rows]
            ),
        )
        .group_by(PengirimanBarangDetail.sales_order_detail_id)
        .all()
    )

    fully = partly = 0
    for sod_id, qty in detail_rows:
        delivered_qty = int(delivered.get(sod_id, 0) or 0)
        if delivered_qty <= 0:
            continue
        if delivered_qty < qty:
            partly += 1
        else:
            fully += 1

    if fully == len(detail_rows):
        new_status = "FULFILLED"
    elif fully or partly:
        new_status = "PARTIAL"
    else:
        new_status = "OPEN"

    so = db.get(SalesOrder, sales_order_id)
    # Set hanya bila nilainya berubah — hindari flush dirty yang tidak perlu.
    if so is not None and so.fulfillment_status != new_status:
        so.fulfillment_status = new_status
        db.add(so)


@atomic_accounting_write
def cancel_pengiriman(db: Session, db_obj: PengirimanBarang, user_id: Optional[UUID] = None) -> PengirimanBarang:
    """Batalkan pengiriman."""
    require_no_stock_movement(db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Pengiriman sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    # Update #7 (M-05): pengiriman yang dibatalkan tidak lagi menghitung ke
    # delivered SO — refresh supaya fulfillment_status mundur (PARTIAL/OPEN).
    _refresh_fulfillment_status(db, db_obj.sales_order_id)
    # === Update #070 — SO ikut disinkronkan pasca-batal pengiriman: kriteria
    # SELESAI SO kini memperhitungkan kelengkapan pengiriman (DO batal bisa
    # menurunkan SO yang sudah SELESAI kembali ke DIPROSES). ===
    if db_obj.sales_order_id is not None:
        try:
            sync_so_completion(db, {db_obj.sales_order_id})
        except Exception as sync_exc:  # noqa: BLE001 — jangan gagalkan pembatalan
            logger.warning(
                f"Sync status SO pasca-batal pengiriman {db_obj.no_surat_jalan} dilewati: {sync_exc}"
            )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PengirimanBarang cancelled: {db_obj.no_surat_jalan}")
    return db_obj


@atomic_accounting_write
def finish_pengiriman(db: Session, db_obj: PengirimanBarang) -> PengirimanBarang:
    """Finalisasi pengiriman barang — status SELESAI + kurangi stok barang.

    === Update #069 — JURNAL HPP (D HPP Penjualan / K Persediaan) SAAT
    PENGIRIMAN DIHAPUS — MODEL PERIODIK. ===
    Biaya pembelian kini dibukukan di jurnal INVOICE PEMBELIAN (D Pembelian
    (HPP) / K Utang) dan langsung muncul di baris HPP Laba Rugi. Bila
    pengiriman masih mempost COGS, HPP akan TERHITUNG DUA KALI (pembelian +
    COGS) dan akun Persediaan GL jadi negatif karena tidak pernah di-debit
    oleh pembelian. Pengiriman kini murni operasi GUDANG (StokMutasi +
    snapshot akun persediaan/HPP per barang untuk retur & rekonsiliasi).
    Pendapatan tetap dibukukan di jurnal Invoice Penjualan (tidak berubah).

    Phase C fix (Catatan Audit Delivery §3):
    - qty > 0 validation
    - gudang_id required
    - over-delivery validation (via sales_validation helper)
    """
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Pengiriman yang sudah dibatalkan tidak bisa difinalisasi")
    if db_obj.status == StatusPenjualan.SELESAI:
        raise ValueError("Pengiriman sudah selesai")

    # === Update #066 — gudang wajib HANYA bila ada baris barang master ===
    # (baris master menggerakkan stok → harus warehouse-explicit, Catatan
    # Audit Delivery §3). DO yang seluruh barisnya ITEM BEBAS (jasa/manufaktur/
    # custom — tidak menyentuh stok, mirror #041) boleh tanpa gudang. ===
    has_master_line = any(detail.barang_id is not None for detail in db_obj.details)
    if has_master_line and not db_obj.gudang_id:
        raise ValueError(
            "Gudang wajib diisi untuk pengiriman yang memuat barang master "
            "(transaksi stok harus warehouse-explicit). Pengiriman yang "
            "seluruh barisnya item bebas boleh tanpa gudang."
        )

    # === Phase C: qty > 0 + over-delivery validation per detail ===
    from app.services.sales_validation import validate_over_delivery
    for detail in db_obj.details:
        if detail.qty <= 0:
            raise ValueError(
                f"Qty pengiriman harus > 0 (baris {(detail.barang.kode if detail.barang else getattr(detail, 'item_nama', None)) or 'unknown'}: {detail.qty})"
            )
        # Phase C: over-delivery check (kalau detail punya sales_order_detail_id)
        if detail.sales_order_detail_id:
            try:
                validate_over_delivery(
                    db, detail.sales_order_detail_id, detail.qty,
                    exclude_pengiriman_detail_id=detail.id,
                    context=f"Pengiriman {db_obj.no_surat_jalan}",
                )
            except Exception as e:
                # HTTPException from validate_over_delivery
                raise ValueError(str(getattr(e, 'detail', str(e))))

    try:
        # Kurangi stok untuk setiap detail barang
        total_hpp = Decimal("0")
        for detail in db_obj.details:
            # === Update #041 — baris ITEM BEBAS (barang_id None): tidak
            #     menyentuh stok/HPP — murni baris surat jalan (SO manufaktur/
            #     jasa/custom). Baris master tetap mengurangi stok seperti biasa. ===
            if detail.barang_id is None:
                continue
            movement = update_stok_barang(
                db=db,
                barang_id=detail.barang_id,
                qty_change=detail.qty,
                mode="KURANGI",
                deskripsi=f"Pengiriman {db_obj.no_surat_jalan}",
                ref_module=RefModule.SALES_DELIVERY,
                ref_no=db_obj.no_surat_jalan,
                ref_id=db_obj.id,
                gudang_id=db_obj.gudang_id,
            )
            total_hpp += movement['total_nilai']
            # Snapshot akun di StokMutasi (dipakai retur stok / rekonsiliasi) —
            # tetap diisi walau jurnal GL pengiriman sudah dihapus (#069).
            from app.services.persediaan_service import _get_akun_persediaan_id, _get_akun_hpp_id
            movement['mutasi'].inventory_account_id = _get_akun_persediaan_id(db, detail.barang)
            movement['mutasi'].expense_account_id = _get_akun_hpp_id(db, detail.barang)

        db_obj.status = StatusPenjualan.SELESAI

        # === Update #069 — jurnal HPP pengiriman DIHAPUS (lihat docstring):
        #     HPP Laba Rugi = Pembelian (dari invoice pembelian) − retur;
        #     pendapatan tetap dibukukan di jurnal invoice penjualan. ===

        # Update #7 (M-05): setelah stok + jurnal HPP sukses (sebelum commit),
        # hitung ulang fulfillment_status SO (OPEN/PARTIAL/FULFILLED) — kolom
        # ini sebelumnya tidak pernah ditulis siapa pun (field mati).
        _refresh_fulfillment_status(db, db_obj.sales_order_id)

        # === Update #070 — SO ikut disinkronkan saat DO selesai: kriteria
        # SELESAI SO kini memperhitungkan kelengkapan pengiriman — DO terakhir
        # yang melengkapi kirim (padahal tagihan sudah penuh) bisa menaikkan
        # SO ke SELESAI. Tanpa commit — commit tetap milik alur ini. ===
        if db_obj.sales_order_id is not None:
            try:
                sync_so_completion(db, {db_obj.sales_order_id})
            except Exception as sync_exc:  # noqa: BLE001 — jangan gagalkan finalisasi
                logger.warning(
                    f"Sync status SO pasca-selesai pengiriman {db_obj.no_surat_jalan} dilewati: {sync_exc}"
                )

        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        logger.info(f"PengirimanBarang finished: {db_obj.no_surat_jalan} | stok dikurangi | HPP={total_hpp}")
        return db_obj

    except Exception as e:
        db.rollback()
        logger.error(f"Error finishing PengirimanBarang {db_obj.no_surat_jalan}: {e}")
        raise


@atomic_accounting_write
def finish_sales_retur(db: Session, db_obj: SalesRetur) -> SalesRetur:
    """Finalisasi sales retur — status SELESAI + tambah stok barang kembali."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Sales Retur yang sudah dibatalkan tidak bisa difinalisasi")
    if db_obj.status == StatusPenjualan.SELESAI:
        raise ValueError("Sales Retur sudah selesai")

    from app.services.stock_return_service import execute_sales_return
    execute_sales_return(db, db_obj)

    db_obj.status = StatusPenjualan.SELESAI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"SalesRetur finished: {db_obj.no_retur} | stok ditambahkan")
    return db_obj


def post_sales_invoice(db: Session, inv, created_by):
    """Post the existing document; caller owns commit/rollback and workflow checks.

    Update #036 — DP termin SO diterapkan ke Piutang invoice: pembayaran
    termin yang sudah diterima SEBELUM posting (aktif, umumnya di akun
    Uang Muka Penjualan) diaplikasikan di jurnal invoice ini — Piutang
    dibukukan NET (grand_total − DP) + D akun lawan termin sebesar DP.
    Saldo Uang Muka ikut kosong dan Pelunasan hanya menagih sisa NET
    (balances(): GT − bayar − retur − dp_teraplikasi) — customer tidak
    ditagih dua kali untuk bagian DP.
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, inv)
    tanggal = inv.tanggal
    pelanggan = db.get(Pelanggan, inv.pelanggan_id)
    no_invoice = inv.no_invoice
    sub_total = inv.sub_total
    total_diskon = inv.total_diskon
    total_ppn = inv.total_ppn
    # === Update ASAHI — PPh23 opsional: nilai potongan pajak pada invoice ===
    total_pph23 = safe_decimal(getattr(inv, "total_pph23", 0))
    total_biaya_tambahan = inv.total_biaya_tambahan
    grand_total = inv.grand_total
    if not pelanggan.akun_piutang_id:
        raise ValueError("Mapping akun akun_piutang_id belum diisi; posting dibatalkan")
    dasar_pajak = sub_total - total_diskon

    # === Tahap 2: pendapatan per-barang (relasi modul ↔ COA) ===
    # Pendapatan dikelompokkan per akun: barang yang punya mapping
    # `akun_penjualan_id` (field "Akun Penjualan (Revenue)" di form barang)
    # diposting ke akun tsb; sisanya ke setting PENDAPATAN_PENJUALAN.
    # Diskon global (invoice-level) dikurangkan dari akun default supaya
    # total kredit pendapatan == dasar_pajak (jurnal tetap balance).
    default_rev_id = get_akun_id_or_raise(db, KEY_PENDAPATAN_PENJUALAN, context=no_invoice)
    revenue_by_akun: dict = {}
    line_discount_total = Decimal("0")
    # === Update #042 — mode DP/Pelunasan (dp_pelunasan > 0): selisih gross vs
    # sub_total baris adalah porsi DP (nilai yang BELUM ditagih tahap ini —
    # BUKAN diskon) → tidak menyumbang line_discount_total. Mode ini
    # total_diskon = 0 (diskon baris/global diabaikan) sehingga global_diskon
    # tetap 0 dan pendapatan K = Σ sub_total baris = dp_pelunasan (jurnal
    # balance: D Piutang = grand_total = dp + PPN + biaya − PPh23). ===
    dp_mode = safe_decimal(getattr(inv, "dp_pelunasan", 0) or 0) > 0
    for d in inv.details:
        gross = safe_decimal(d.harga) * safe_int(d.qty)
        d_net = safe_decimal(d.sub_total)
        if not dp_mode:
            line_discount_total += (gross - d_net)
        akun_rev = None
        if d.barang is not None:
            akun_rev = getattr(d.barang, "akun_penjualan_id", None)
        akun_rev = akun_rev or default_rev_id
        revenue_by_akun[akun_rev] = revenue_by_akun.get(akun_rev, Decimal("0")) + d_net
    global_diskon = (safe_decimal(total_diskon) - line_discount_total)
    if global_diskon:
        revenue_by_akun[default_rev_id] = revenue_by_akun.get(default_rev_id, Decimal("0")) - global_diskon

    # === Update #036 — susun aplikasi DP termin SO (bila ada) ===
    # DP tersedia = Σ pembayaran termin aktif − yang sudah diterapkan ke
    # invoice AKTIF lain dari SO yang sama (anti dobel untuk sebagian
    # invoicing). Diaplikasikan maksimal sebesar grand_total invoice ini;
    # kelebihannya tetap menggantung di Uang Muka (termin bebas > GT).
    dp_rows: list = []
    dp_apply = Decimal("0")
    if inv.sales_order_id is not None:
        tersedia = _dp_termin_tersedia(db, inv.sales_order_id, exclude_invoice_id=inv.id)
        dp_apply = min(tersedia, grand_total)
        anggaran = dp_apply
        if anggaran > 0:
            for akun_lawan, nilai in _dp_termin_per_akun(db, inv.sales_order_id):
                if anggaran <= 0:
                    break
                pakai = min(nilai, anggaran)
                dp_rows.append((akun_lawan, pakai))
                anggaran -= pakai

    piutang_debit = grand_total - dp_apply
    entries = []
    if piutang_debit > 0:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=pelanggan.akun_piutang_id,
                debit=piutang_debit,
                keterangan=f"INV {no_invoice} - {pelanggan.nama}",
            ),
        )
    if dp_rows:
        so_no = "-"
        if inv.sales_order_id is not None:
            _so_src = db.get(SalesOrder, inv.sales_order_id)
            so_no = getattr(_so_src, "no_pesanan", None) or "-"
        for akun_lawan, nilai in dp_rows:
            entries.append(
                JurnalEntryItem(
                    akun_perkiraan_id=akun_lawan,
                    debit=nilai,
                    keterangan=f"DP termin SO {so_no} diterapkan INV {no_invoice}",
                ),
            )
    for akun_rev, nilai in revenue_by_akun.items():
        if nilai == 0:
            continue
        if nilai > 0:
            entries.append(
                JurnalEntryItem(
                    akun_perkiraan_id=akun_rev,
                    kredit=nilai,
                    keterangan=f"Pendapatan INV {no_invoice}",
                )
            )
        else:
            # Diskon melebihi pendapatan akun tsb → sisa menjadi debit
            entries.append(
                JurnalEntryItem(
                    akun_perkiraan_id=akun_rev,
                    debit=abs(nilai),
                    keterangan=f"Diskon INV {no_invoice}",
                )
            )
    if total_ppn > 0:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_PPN_KELUARAN, context=no_invoice
                ),
                kredit=total_ppn,
                keterangan=f"PPN INV {no_invoice}",
            )
        )

    if total_biaya_tambahan > 0:
        # Kredit: Pendapatan Angkut — mengimbangi grand_total (Debit
        # Piutang) yang sudah termasuk biaya tambahan, supaya jurnal balance.
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_PENDAPATAN_ANGKUT, context=no_invoice
                ),
                kredit=total_biaya_tambahan,
                keterangan=f"Biaya tambahan INV {no_invoice}",
            )
        )

    # === Update ASAHI — PPh23 opsional (potongan pajak oleh pelanggan) ===
    # Grand total sudah dipotong total_pph23 (Debit Piutang lebih kecil),
    # sehingga perlu Debit "PPh 23 Dibayar Dimuka" (aktiva pajak) agar
    # jurnal tetap balance: D(Piutang + PPh23) = K(Pendapatan + PPN + Angkut).
    if total_pph23 > 0:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_PPH23_DIBAYAR_DIMUKA, context=no_invoice
                ),
                debit=total_pph23,
                keterangan=f"PPh23 dipotong pelanggan INV {no_invoice}",
            )
        )

    try:
        jurnal = auto_posting_jurnal(
            db=db,
            ref_module=RefModule.SALES_INVOICE,
            ref_no=no_invoice,
            entries=entries,
            keterangan=f"Sales Invoice {no_invoice}",
            ref_id=inv.id,
            tanggal=tanggal,
            created_by=created_by,
        )
        inv.jurnal_umum_id = jurnal.id
        inv.akun_kontrol_id = pelanggan.akun_piutang_id
        # Update #036 — simpan besaran DP yang diterapkan (dipakai
        # settlement_service.balances utk sisa tagihan NET invoice ini).
        inv.dp_teraplikasi = dp_apply
        inv.status = StatusPenjualan.SELESAI
    except Exception as e:
        raise ValueError(f"Jurnal INV gagal diposting: {e}")
    # === Update #052 — SO sumber ikut disinkronkan saat invoice final.
    # Menutup celah "SO nyangkut di Diproses": sebelumnya sync_so_completion
    # hanya dipanggil saat pelunasan diposting — padahal alur #042 bisa
    # terbalik (DP/termin dibayar DULU lewat Penerimaan, invoice dibuat
    # BELAKANGAN). Saat pelunasan diposting, SO belum tertagih penuh →
    # tidak naik; saat invoice akhirnya dibuat, sync tidak dipanggil →
    # SO terkunci di DIPROSES selamanya. Dengan hook ini kedua urutan
    # alur tersinkron. Tanpa commit — pemanggil (workflow) memiliki
    # transaksi. ===
    if inv.sales_order_id is not None:
        try:
            sync_so_completion(db, {inv.sales_order_id})
        except Exception as sync_exc:  # noqa: BLE001 — jangan gagalkan posting
            logger.warning(
                f"Sync status SO pasca-posting INV {no_invoice} dilewati: {sync_exc}"
            )
    return inv


def post_sales_retur(db: Session, retur, created_by):
    """Post the existing document; caller owns commit/rollback and workflow checks.

    Phase E fix (Catatan Sales Return §3):
    - Auto-call validate_returnable_qty per detail (prevent over-return)
    - Reject if return price != source invoice price
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, retur)
    from app.services.settlement_service import validate_return, control_account
    validate_return(db, retur)

    # === Phase E: validate returnable qty per detail ===
    from app.services.sales_validation import validate_returnable_qty
    for detail in retur.details:
        if detail.sales_invoice_detail_id:
            try:
                validate_returnable_qty(
                    db, detail.sales_invoice_detail_id, detail.qty,
                    exclude_retur_detail_id=detail.id,
                    context=f"Sales Retur {retur.no_retur}",
                )
            except Exception as e:
                raise ValueError(str(getattr(e, 'detail', str(e))))

        # === Phase E: enforce return price = source invoice price ===
        if detail.sales_invoice_detail_id:
            from app.models.detail.sales_invoice_detail import SalesInvoiceDetail
            inv_detail = db.get(SalesInvoiceDetail, detail.sales_invoice_detail_id)
            if inv_detail and detail.harga != inv_detail.harga:
                raise ValueError(
                    f"Harga retur ({detail.harga}) tidak boleh berbeda dari harga invoice sumber "
                    f"({inv_detail.harga}) untuk barang {(detail.barang.kode if detail.barang else getattr(detail, 'item_nama', None)) or 'unknown'}. "
                    f"(Catatan Sales Return §3: Financial return harus memakai harga snapshot Invoice sumber)"
                )

    tanggal = retur.tanggal
    pelanggan = db.get(Pelanggan, retur.pelanggan_id)
    no_retur = retur.no_retur
    sub_total = retur.sub_total
    total_ppn = retur.total_ppn
    grand_total = retur.grand_total
    if not pelanggan.akun_piutang_id:
        raise ValueError("Mapping akun akun_piutang_id belum diisi; posting dibatalkan")
    entries = [
        # Debit: Retur Penjualan
        JurnalEntryItem(
            akun_perkiraan_id=get_akun_id_or_raise(
                db, KEY_RETUR_PENJUALAN, context=f"Retur {no_retur}"
            ),
            debit=grand_total - total_ppn,
            keterangan=f"Retur Penjualan {no_retur}",
        ),
        # Kredit: Piutang Dagang
        JurnalEntryItem(
            akun_perkiraan_id=control_account(db, retur.sales_invoice) if retur.sales_invoice else pelanggan.akun_piutang_id,
            kredit=grand_total,
            keterangan=f"Kurangi piutang {no_retur}",
        ),
    ]
    if total_ppn > 0:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_PPN_KELUARAN, context=f"Retur {no_retur}"
                ),
                debit=total_ppn,
                keterangan=f"PPN Retur {no_retur}",
            )
        )

    try:
        jurnal = auto_posting_jurnal(
            db=db,
            ref_module=RefModule.SALES_RETUR,
            ref_no=no_retur,
            entries=entries,
            keterangan=f"Sales Retur {no_retur}",
            ref_id=retur.id,
            tanggal=tanggal,
            created_by=created_by,
        )
        retur.jurnal_umum_id = jurnal.id
    except Exception as e:
        raise ValueError(f"Jurnal Retur gagal diposting: {e}")
    return retur
