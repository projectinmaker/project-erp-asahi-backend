from app.models.master.barang import Barang  # Update #5: eager-load satuan barang utk detail response
from app.services import inventory_receipt_control as receipt_control
_RECEIPT_LINK_UNSET = object()
"""
pembelian_service.py

Service layer untuk modul Pembelian.
Menghandle CRUD + auto-posting jurnal untuk:
- PurchaseOrder (+ PurchaseOrderDetail + TransaksiBiaya)
- PurchaseInvoice (+ PurchaseInvoiceDetail + TransaksiBiaya)
- PurchaseRetur (+ PurchaseReturDetail)
- PenerimaanBarang (+ PenerimaanBarangDetail)
"""

from app.services.accounting_control import atomic_accounting_write, require_unposted, require_no_stock_movement
from app.services.posting_service import reverse_journal

from datetime import datetime, date, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.transaksi.pembelian.purchase_order import PurchaseOrder, JENIS_PEMBELIAN_VALUES
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.models.transaksi.pembelian.purchase_retur import PurchaseRetur
from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
from app.models.detail.purchase_order_detail import PurchaseOrderDetail
from app.models.detail.purchase_invoice_detail import PurchaseInvoiceDetail
from app.models.detail.purchase_retur_detail import PurchaseReturDetail
from app.models.detail.penerimaan_barang_detail import PenerimaanBarangDetail
from app.models.transaksi.transaksi_biaya import TransaksiBiaya
from app.models.master.supplier import Supplier
from app.models.transaksi.penjualan.sales_order import StatusPenjualan, SalesOrder
# === Update #037 — alur belanja dari ROP (Detail ROP → PO draft) ===
from app.models.transaksi.produksi.request_order_part import (
    RequestOrderPart,
    RequestOrderPartItem,
    StatusRequestOrderPart,
)
from app.models.transaksi.jurnal import RefModule
from app.services.posting_service import auto_posting_jurnal, JurnalEntryItem
from app.services.stok_service import update_stok_barang
from app.services.decimal_utils import safe_decimal, safe_int
from app.services.setting_akun_service import (
    get_akun_id_or_raise,
    KEY_PEMBELIAN,
    KEY_PPN_MASUKAN,
    KEY_RETUR_PEMBELIAN,
    KEY_BEBAN_ANGKUT_PEMBELIAN,
    # === Update #052 — PPh23 Invoice Pembelian: akun lawan potongan ===
    KEY_HUTANG_PPH23,
)
from app.models.detail.purchase_invoice_receipt_match import PurchaseInvoiceReceiptMatch
from app.utils.nomor_dokumen import get_nomor_dokumen, get_nomor_dokumen_tahunan


# ==========================================
# HELPER: Hitung total dari detail
# ==========================================
def _validate_po_detail_refs(db: Session, details_data: list) -> None:
    """Update ASAHI #002 — validasi referensi per baris PO.

    - ``jenis_pembelian`` (opsional) harus salah satu dari
      CONSUMABLE / PROJECT / ASSET / ATK (dasar belanja → tujuan posting).
    - ``sales_order_id`` (opsional) harus merujuk SO yang ada — satu PO bisa
      memuat item dari beberapa SO berbeda (tracking per baris).
    - Update #037: ``rop_item_id`` (opsional) harus merujuk baris ROP yang
      ada — jejak baris PO berasal dari ROP mana (status belanja di
      Pembelian → Detail ROP dihitung dari link ini).
    """
    so_ids = set()
    rop_item_ids = set()
    for d in details_data:
        jenis = (d.get("jenis_pembelian") or "").strip() or None
        if jenis is not None and jenis not in JENIS_PEMBELIAN_VALUES:
            raise ValueError(
                f"Jenis pembelian tidak valid: {jenis} — gunakan CONSUMABLE, PROJECT, ASSET, atau ATK"
            )
        so_id = d.get("sales_order_id")
        if so_id:
            so_ids.add(so_id)
        rop_item_id = d.get("rop_item_id")
        if rop_item_id:
            rop_item_ids.add(rop_item_id)
    if so_ids:
        found = {
            row.id
            for row in db.query(SalesOrder.id).filter(SalesOrder.id.in_(so_ids)).all()
        }
        missing = so_ids - found
        if missing:
            raise ValueError("Sales Order yang dipilih pada detail barang tidak ditemukan")
    if rop_item_ids:
        found_rop_items = {
            row.id
            for row in db.query(RequestOrderPartItem.id)
            .filter(RequestOrderPartItem.id.in_(rop_item_ids))
            .all()
        }
        missing_rop = rop_item_ids - found_rop_items
        if missing_rop:
            raise ValueError("Baris ROP yang ditaut pada detail barang tidak ditemukan")


def _assert_po_details_replaceable(db: Session, db_obj: PurchaseOrder) -> None:
    """Update ASAHI #002 — detail PO hanya boleh diganti bila belum ada
    dokumen lanjutan (penerimaan / invoice / retur) yang menaut barisnya."""
    detail_ids = [d.id for d in db_obj.details]
    if not detail_ids:
        return
    if (
        db.query(PenerimaanBarangDetail.id)
        .filter(PenerimaanBarangDetail.purchase_order_detail_id.in_(detail_ids))
        .first()
    ):
        raise ValueError("Detail tidak bisa diubah — sudah ada Penerimaan Barang yang menaut baris PO ini")
    if (
        db.query(PurchaseInvoiceDetail.id)
        .filter(PurchaseInvoiceDetail.purchase_order_detail_id.in_(detail_ids))
        .first()
    ):
        raise ValueError("Detail tidak bisa diubah — sudah ada Invoice Pembelian yang menaut baris PO ini")
    if (
        db.query(PurchaseReturDetail.id)
        .filter(PurchaseReturDetail.purchase_order_detail_id.in_(detail_ids))
        .first()
    ):
        raise ValueError("Detail tidak bisa diubah — sudah ada Retur Pembelian yang menaut baris PO ini")


def _hitung_total_detail_with_diskon(details_data: list) -> Tuple[Decimal, Decimal]:
    """Hitung sub_total dan total_diskon dari list detail (harga * qty, diskon per line).
    Mengembalikan (sub_total_bruto, total_diskon_nilai).
    """
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
    return sub_total, total_diskon


def _hitung_total_detail_no_diskon(details_data: list) -> Decimal:
    """Hitung sub_total dari list detail tanpa diskon (harga * qty).
    Digunakan untuk PurchaseRetur.
    """
    sub_total = Decimal("0")
    for d in details_data:
        harga = safe_decimal(d.get("harga"))
        qty = safe_int(d.get("qty"))
        line_total = harga * qty
        sub_total += line_total
        d["sub_total"] = line_total
    return sub_total


def _hitung_total_biaya(biaya_data: list) -> Decimal:
    """Hitung total biaya tambahan."""
    return sum((safe_decimal(b.get("jumlah")) for b in biaya_data), Decimal("0"))


def _hitung_grand_total(
    sub_total: Decimal,
    total_diskon: Decimal,
    ppn_pct: Decimal,
    total_biaya_tambahan: Decimal,
    # === Update ASAHI — PPh23/PPN opsional (Purchase Order) ===
    ppn_applicable: bool = True,
    pph23_pct: Optional[Decimal] = None,
    pph23_applicable: bool = False,
) -> Tuple[Decimal, Decimal, Decimal]:
    """Hitung total_ppn, total_pph23, dan grand_total.

    PPN hanya diterapkan bila ppn_applicable; PPh23 hanya dihitung bila
    pph23_applicable dan memotong grand_total (potongan pajak).
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
    """Buat TransaksiBiaya rows untuk PO atau PINV."""
    for b in biaya_data:
        tb = TransaksiBiaya(
            nama=b["nama"],
            jumlah=Decimal(str(b["jumlah"])),
            **{fk_field: model_obj.id},
        )
        db.add(tb)


# ==========================================
# PURCHASE ORDER
# ==========================================

def get_purchase_order_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PurchaseOrder], int]:
    """Ambil daftar purchase order dengan filter & pagination."""
    query = db.query(PurchaseOrder).options(
        joinedload(PurchaseOrder.supplier),
        # === Update #061 — eager-load syarat bayar: nama Payment Term ikut
        # response list PO (kolom "Payment Term" di FE membaca syaratBayar.nama;
        # sebelumnya relasi tidak di-load → nama bisa kosong/None). ===
        joinedload(PurchaseOrder.syarat_bayar),
        joinedload(PurchaseOrder.creator),
        joinedload(PurchaseOrder.details).joinedload(PurchaseOrderDetail.barang),
        # Update #7 (B-05): eager-load satuan baris — FE membaca satuan?.nama
        joinedload(PurchaseOrder.details).joinedload(PurchaseOrderDetail.satuan),
        # Update ASAHI #002: eager-load SO per baris — hindari N+1 di list
        joinedload(PurchaseOrder.details).joinedload(PurchaseOrderDetail.sales_order).joinedload(SalesOrder.pelanggan),
        joinedload(PurchaseOrder.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PurchaseOrder.no_pesanan.ilike(pattern)
            | PurchaseOrder.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PurchaseOrder.status == status)
    if supplier_id:
        query = query.filter(PurchaseOrder.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(PurchaseOrder.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PurchaseOrder.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PurchaseOrder.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_purchase_order_by_id(db: Session, po_id: UUID) -> Optional[PurchaseOrder]:
    """Ambil 1 purchase order berdasarkan ID dengan detail."""
    return (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.supplier),
            # === Update #061 — eager-load syarat bayar (mirror list): form
            # edit/detail PO membaca original.syaratBayar.nama untuk dropdown
            # "Syarat Bayar"; jamin tersedia tanpa lazy-load. ===
            joinedload(PurchaseOrder.syarat_bayar),
            joinedload(PurchaseOrder.creator),
            joinedload(PurchaseOrder.jurnal),
            joinedload(PurchaseOrder.details).joinedload(PurchaseOrderDetail.barang),
            # Update #7 (B-05): eager-load satuan baris — FE membaca satuan?.nama
            joinedload(PurchaseOrder.details).joinedload(PurchaseOrderDetail.satuan),
            # Update ASAHI #002: eager-load SO per baris (+ pelanggan untuk nama)
            # — kolom "Keterangan SO" di form & cetak PO.
            joinedload(PurchaseOrder.details).joinedload(PurchaseOrderDetail.sales_order).joinedload(SalesOrder.pelanggan),
            joinedload(PurchaseOrder.biaya_tambahan),
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )


@atomic_accounting_write
def create_purchase_order(
    db: Session,
    tanggal: datetime,
    supplier_id: UUID,
    details_data: list,
    biaya_data: Optional[list] = None,
    tanggal_kirim: Optional[datetime] = None,
    alamat: Optional[str] = None,
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    # === Update ASAHI — pilihan PPh23/PPN saat input PO ===
    ppn_applicable: bool = True,
    pph23_applicable: bool = False,
    pph23: Decimal = Decimal("2"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: Optional[UUID] = None,
    syarat_bayar_id: Optional[UUID] = None,
    currency: str = "IDR",
    # === Update ASAHI #3 — alamat pengiriman (wajib pilih satu) + PPIC ===
    alamat_pengiriman_id: Optional[UUID] = None,
    ppic: bool = False,
    # === Update ASAHI #002 — PIC Gudang (kolom Pengiriman PO) ===
    pic_gudang: Optional[str] = None,
    pic_gudang_telepon: Optional[str] = None,
    # === Update #067 — PIC Supplier & No. HP (field aktif saat buat PO) ===
    pic_supplier: Optional[str] = None,
    pic_supplier_telepon: Optional[str] = None,
) -> PurchaseOrder:
    """Buat PurchaseOrder baru beserta detail + biaya tambahan.
    - Generate no_pesanan otomatis (PO-YYYY-MM-NNN)
    - Hitung sub_total, total_diskon, ppn, grand_total dari detail
    - Order tidak melakukan posting jurnal; pencatatan dilakukan pada invoice.
    """
    try:
        biaya_data = biaya_data or []

        # Validasi supplier
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

        # === Update ASAHI #3 — alamat pengiriman WAJIB dipilih satu ===
        if alamat_pengiriman_id is None:
            raise ValueError("Alamat pengiriman wajib dipilih — pilih satu gudang tujuan")
        from app.models.master.alamat_pengiriman import AlamatPengiriman
        alamat_kirim = db.query(AlamatPengiriman).filter(AlamatPengiriman.id == alamat_pengiriman_id).first()
        if not alamat_kirim:
            raise ValueError("Alamat pengiriman tidak ditemukan")
        # Snapshot teks untuk cetak: "<prefix>\n<nama>" — dokumen tidak berubah
        # walau master diedit/dihapus kemudian.
        alamat_pengiriman_snapshot = f"{alamat_kirim.prefix}\n{alamat_kirim.nama}"

        # === Update ASAHI #002 — PIC Gudang ===
        # None (tidak dikirim) → fallback ke master gudang; string kosong →
        # sengaja dikosongkan user (tetap kosong, menimpa master).
        if pic_gudang is None:
            pic_gudang = (alamat_kirim.pic_nama or "").strip() or None
        if pic_gudang_telepon is None:
            pic_gudang_telepon = (alamat_kirim.pic_telepon or "").strip() or None

        # === Update #067 — PIC Supplier & No. HP (snapshot kontak supplier) ===
        # Mirror perilaku PIC Gudang: None (tidak dikirim — mis. PO draft hasil
        # alur Detail ROP / Part Stok Menipis) → fallback ke Kontak Person /
        # Telepon master SUPPLIER; string kosong → sengaja dikosongkan.
        # Nilai dari form (terisi otomatis saat supplier dipilih, boleh
        # disunting) disimpan apa adanya sebagai snapshot per-PO.
        if pic_supplier is None:
            pic_supplier = (supplier.kontak_person or "").strip() or None
        if pic_supplier_telepon is None:
            pic_supplier_telepon = (supplier.telepon or "").strip() or None

        # === Update ASAHI #002 — validasi tracking per baris (SO + jenis) ===
        _validate_po_detail_refs(db, details_data)

        # === Update ASAHI #3 — validasi mata uang terhadap master ===
        currency = (currency or "IDR").strip().upper()
        from app.models.master.mata_uang import MataUang
        if db.query(MataUang).filter(MataUang.kode == currency).first() is None:
            raise ValueError(f"Mata uang {currency} tidak dikenal — tambahkan dulu di Pengaturan → Profil Perusahaan")

        # Hitung sub_total dan total_diskon dari detail
        sub_total, total_diskon = _hitung_total_detail_with_diskon(details_data)

        total_biaya_tambahan = _hitung_total_biaya(biaya_data)
        total_ppn, total_pph23, grand_total = _hitung_grand_total(
            sub_total, total_diskon, ppn, total_biaya_tambahan,
            ppn_applicable=ppn_applicable,
            pph23_pct=pph23,
            pph23_applicable=pph23_applicable,
        )

        # Generate nomor pesanan
        # Update ASAHI: format baru "PO ASI/YYYY/NNN" — reset tahunan,
        # sequence terpisah dari SO (jawaban user, catatan update cetak SO/PO).
        # === Update ASAHI #019 — prefix & kode perusahaan via Preferensi ===
        # (doc_type PURCHASE_ORDER; fallback "PO" — perilaku lama bila belum diubah).
        no_pesanan = get_nomor_dokumen_tahunan(
            db, PurchaseOrder, prefix="PO",
            no_column="no_pesanan", tanggal=tanggal.date(),
            doc_type="PURCHASE_ORDER",
        )

        # Buat header
        po = PurchaseOrder(
            no_pesanan=no_pesanan,
            tanggal=tanggal,
            supplier_id=supplier_id,
            tanggal_kirim=tanggal_kirim,
            alamat=alamat,
            diskon_global=diskon_global,
            ppn=ppn,
            # === Update ASAHI — pilihan PPh23/PPN saat input PO ===
            ppn_applicable=ppn_applicable,
            pph23_applicable=pph23_applicable,
            pph23=pph23,
            total_pph23=total_pph23,
            sub_total=sub_total,
            total_diskon=total_diskon,
            total_ppn=total_ppn,
            total_biaya_tambahan=total_biaya_tambahan,
            grand_total=grand_total,
            auto_post_jurnal=False,
            status=StatusPenjualan.DRAFT,
            keterangan=keterangan,
            created_by=created_by,
            syarat_bayar_id=syarat_bayar_id,
            currency=currency,
            # === Update ASAHI #3 — alamat pengiriman + PPIC ===
            alamat_pengiriman_id=alamat_pengiriman_id,
            alamat_pengiriman=alamat_pengiriman_snapshot,
            ppic=ppic,
            # === Update ASAHI #002 — PIC Gudang (snapshot) ===
            pic_gudang=pic_gudang,
            pic_gudang_telepon=pic_gudang_telepon,
            # === Update #067 — PIC Supplier & No. HP (snapshot) ===
            pic_supplier=pic_supplier,
            pic_supplier_telepon=pic_supplier_telepon,
        )
        db.add(po)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PurchaseOrderDetail(
                purchase_order_id=po.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                # Update ASAHI #002: .get() + safe_decimal — client boleh tidak
                # mengirim sub_total (model_dump exclude_unset men-drop default);
                # refresh_totals menghitung ulang nilai finalnya.
                sub_total=safe_decimal(d.get("sub_total")),
                # Update #7 (B-05): persist satuan per baris (schema Phase B sudah
                # punya field; sebelumnya diabaikan → kolom DB selalu NULL dan FE
                # menampilkan dialog warning "satuan belum tersimpan").
                satuan_id=d.get("satuan_id"),
                # === Update ASAHI #002 — tracking per baris: SO + jenis belanja ===
                sales_order_id=d.get("sales_order_id"),
                jenis_pembelian=(d.get("jenis_pembelian") or "").strip() or None,
                # === Update #037 — jejak baris ROP sumber (Detail ROP → PO) ===
                rop_item_id=d.get("rop_item_id"),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, po, biaya_data, "purchase_order_id")

        # Fix diskon global (temuan minor): total PO sebelumnya mengabaikan
        # diskon_global — field hanya tersimpan tanpa memengaruhi total.
        # refresh_totals menghitung ulang sub_total/total_diskon/total_ppn/
        # grand_total dengan diskon baris + diskon global (konsisten invoice).
        # flush dulu supaya relationship .details terisi dari pending inserts
        # (pola yang sama dengan create_sales_invoice).
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(po)

        # Order tidak mengakui pendapatan/piutang atau pembelian/utang.
        db.commit()
        db.refresh(po)
        logger.info(f"PurchaseOrder created: {no_pesanan} | grand_total={grand_total}")
        return po

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseOrder: {e}")
        raise


@atomic_accounting_write
def update_purchase_order(
    db: Session,
    db_obj: PurchaseOrder,
    tanggal: Optional[datetime] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_kirim: Optional[datetime] = None,
    alamat: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    # === Update ASAHI — pilihan PPh23/PPN saat update PO ===
    ppn_applicable: Optional[bool] = None,
    pph23_applicable: Optional[bool] = None,
    pph23: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    # === Update ASAHI #3 — dukung update currency/syarat bayar/alamat kirim/PPIC ===
    syarat_bayar_id: Optional[UUID] = None,
    currency: Optional[str] = None,
    alamat_pengiriman_id: Optional[UUID] = None,
    ppic: Optional[bool] = None,
    # === Update ASAHI #002 — PIC Gudang + replace detail (SO/jenis per baris) ===
    pic_gudang: Optional[str] = None,
    pic_gudang_telepon: Optional[str] = None,
    # === Update #067 — PIC Supplier & No. HP (edit PO draft/diproses) ===
    pic_supplier: Optional[str] = None,
    pic_supplier_telepon: Optional[str] = None,
    details: Optional[list] = None,
) -> PurchaseOrder:
    """Update data purchase order (header + opsional replace detail).

    Update ASAHI #002: ``details`` (jika dikirim) mengganti seluruh baris
    barang — termasuk relasi SO per baris & jenis pembelian. Hanya boleh
    untuk PO DRAFT yang belum ditaut penerimaan/invoice/retur; total
    dihitung ulang setelah replace (pola Purchase Invoice).
    """
    require_unposted(db_obj)
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Purchase Order dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if supplier_id is not None:
        db_obj.supplier_id = supplier_id
    if tanggal_kirim is not None:
        db_obj.tanggal_kirim = tanggal_kirim
    if alamat is not None:
        db_obj.alamat = alamat
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

    # === Update ASAHI #3 — field baru pada update header ===
    if syarat_bayar_id is not None:
        db_obj.syarat_bayar_id = syarat_bayar_id
    if currency is not None:
        currency = currency.strip().upper()
        from app.models.master.mata_uang import MataUang
        if db.query(MataUang).filter(MataUang.kode == currency).first() is None:
            raise ValueError(f"Mata uang {currency} tidak dikenal — tambahkan dulu di Pengaturan → Profil Perusahaan")
        db_obj.currency = currency
    if alamat_pengiriman_id is not None:
        from app.models.master.alamat_pengiriman import AlamatPengiriman
        alamat_kirim = db.query(AlamatPengiriman).filter(AlamatPengiriman.id == alamat_pengiriman_id).first()
        if not alamat_kirim:
            raise ValueError("Alamat pengiriman tidak ditemukan")
        db_obj.alamat_pengiriman_id = alamat_pengiriman_id
        db_obj.alamat_pengiriman = f"{alamat_kirim.prefix}\n{alamat_kirim.nama}"
    if ppic is not None:
        db_obj.ppic = ppic

    # === Update ASAHI #002 — PIC Gudang pada update header ===
    if pic_gudang is not None:
        db_obj.pic_gudang = pic_gudang.strip() or None
    if pic_gudang_telepon is not None:
        db_obj.pic_gudang_telepon = pic_gudang_telepon.strip() or None

    # === Update #067 — PIC Supplier & No. HP pada update header ===
    if pic_supplier is not None:
        db_obj.pic_supplier = pic_supplier.strip() or None
    if pic_supplier_telepon is not None:
        db_obj.pic_supplier_telepon = pic_supplier_telepon.strip() or None

    # === Update ASAHI #002 — replace detail (SO/jenis per baris ikut terganti) ===
    if details is not None:
        _assert_po_details_replaceable(db, db_obj)
        _validate_po_detail_refs(db, details)
        db_obj.details.clear()
        db.flush()
        for d in details:
            db_obj.details.append(
                PurchaseOrderDetail(
                    barang_id=d["barang_id"],
                    harga=Decimal(str(d["harga"])),
                    qty=int(d["qty"]),
                    diskon=safe_decimal(d.get("diskon")),
                    # Update ASAHI #002: .get() + safe_decimal — client boleh tidak
                    # mengirim sub_total (exclude_unset men-drop default 0);
                    # refresh_totals menghitung ulang nilai finalnya.
                    sub_total=safe_decimal(d.get("sub_total")),
                    satuan_id=d.get("satuan_id"),
                    sales_order_id=d.get("sales_order_id"),
                    jenis_pembelian=(d.get("jenis_pembelian") or "").strip() or None,
                    # === Update #037 — jejak baris ROP sumber tetap terjaga saat
                    # PO draft di-edit (FE round-trip ropItemId per baris). ===
                    rop_item_id=d.get("rop_item_id"),
                )
            )
        db.flush()

    # Fix diskon global: hitung ulang total bila diskon_global/ppn/pilihan
    # pajak (PPh23/PPN) diubah — sebelumnya total lama (stale) tetap
    # tersimpan meski persen berubah.
    # Update ASAHI #002: total juga dihitung ulang setelah replace detail.
    if details is not None or diskon_global is not None or ppn is not None or ppn_applicable is not None or pph23_applicable is not None or pph23 is not None:
        from app.services.document_totals import refresh_totals
        refresh_totals(db_obj)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_purchase_order(db: Session, db_obj: PurchaseOrder, user_id: Optional[UUID] = None) -> PurchaseOrder:
    """Batalkan purchase order."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Order sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseOrder cancelled: {db_obj.no_pesanan}")
    return db_obj


# ==========================================
# === Update #037 — DETAIL ROP (kolam belanja PIC Pembelian) ===
# Alur: Engineering buat ROP (DIBUAT) → seluruh item masuk daftar Detail ROP
# → PIC Pembelian centang item yang mau dibelanjakan → pilih supplier →
# sistem buat PO DRAFT (bisa dikoreksi lagi di menu Pesanan). Qty belanja
# otomatis = Kekurangan (qty diminta − stok tersedia gudang; jawaban user
# "beli selisihnya saja"). Status "sudah dibelanjakan" = derived dari
# SUM qty baris PO aktif (≠ DIBATALKAN) yang menaut item → PO dibatalkan /
# barisnya dihapus otomatis mengembalikan item ke "belum dibelanjakan".
# ==========================================

# Status belanja per item ROP (nilai enum string — FE memakai labelnya).
ROP_BELANJA_BELUM = "BELUM"            # perlu beli, belum masuk PO mana pun
ROP_BELANJA_SEBAGIAN = "SEBAGIAN"      # sebagian kekurangan sudah masuk PO
ROP_BELANJA_LENGKAP = "LENGKAP"        # seluruh kekurangan sudah masuk PO
ROP_BELANJA_STOK_CUKUP = "STOK_CUKUP"  # stok gudang menutup qty diminta


def _bought_qty_per_rop_item(db: Session, item_ids: List[UUID]) -> dict:
    """Agregat qty per baris ROP dari PO aktif (status ≠ DIBATALKAN) —
    satu query GROUP BY (hindari N+1). Derived: tidak ada kolom status
    tersimpan, sehingga pembatalan/penghapusan PO otomatis membebaskan
    item ROP kembali "belum dibelanjakan"."""
    if not item_ids:
        return {}
    rows = (
        db.query(PurchaseOrderDetail.rop_item_id, func.sum(PurchaseOrderDetail.qty))
        .join(PurchaseOrder, PurchaseOrderDetail.purchase_order_id == PurchaseOrder.id)
        .filter(
            PurchaseOrderDetail.rop_item_id.in_(item_ids),
            PurchaseOrder.status != StatusPenjualan.DIBATALKAN,
        )
        .group_by(PurchaseOrderDetail.rop_item_id)
        .all()
    )
    return {rop_item_id: (qty or 0) for rop_item_id, qty in rows}


def _draft_po_nos_per_rop_item(db: Session, item_ids: List[UUID]) -> dict:
    """Nomor PO DRAFT yang menaut item (badge "sudah masuk PO draft" di
    daftar Detail ROP — PIC tahu item mana yang sudah dikerjakan)."""
    if not item_ids:
        return {}
    result: dict = {}
    rows = (
        db.query(PurchaseOrderDetail.rop_item_id, PurchaseOrder.no_pesanan)
        .join(PurchaseOrder, PurchaseOrderDetail.purchase_order_id == PurchaseOrder.id)
        .filter(
            PurchaseOrderDetail.rop_item_id.in_(item_ids),
            PurchaseOrder.status == StatusPenjualan.DRAFT,
        )
        .order_by(PurchaseOrder.no_pesanan)
        .all()
    )
    for rop_item_id, no_pesanan in rows:
        result.setdefault(rop_item_id, []).append(no_pesanan)
    return result


def _rop_item_belanja_row(item: RequestOrderPartItem, bought: Decimal, draft_nos: List[str]) -> Dict[str, Any]:
    """Serialisasi satu baris daftar belanja Detail ROP (+ hitung stok /
    kekurangan / sisa / status). Dipisah agar testable tanpa DB."""
    rop = item.request_order_part
    barang = getattr(item, "barang", None)
    qty = safe_decimal(item.qty) or Decimal("0")
    stok = int(getattr(barang, "stok", 0) or 0) if barang else 0
    kekurangan = qty - Decimal(stok)
    if kekurangan < 0:
        kekurangan = Decimal("0")
    sudah = bought or Decimal("0")
    sisa = kekurangan - sudah
    if sisa < 0:
        sisa = Decimal("0")
    if kekurangan <= 0:
        status = ROP_BELANJA_STOK_CUKUP
    elif sisa <= 0:
        status = ROP_BELANJA_LENGKAP
    elif sudah > 0:
        status = ROP_BELANJA_SEBAGIAN
    else:
        status = ROP_BELANJA_BELUM
    supplier = getattr(item, "supplier", None)
    so = getattr(rop, "sales_order", None)
    return {
        "id": item.id,
        "no": item.no,
        "rop_id": rop.id,
        "no_rop": rop.no_rop,
        "project_name": rop.project_name,
        "model": rop.model,
        "tanggal": rop.tanggal,
        "sales_order_id": rop.sales_order_id,
        "sales_order_no": (so.no_pesanan if so else None),
        "id_part": item.id_part,
        "description": item.description,
        "material": item.material,
        "qty": qty,
        "qs": item.qs,
        "supplier_id": item.supplier_id,
        "supplier_nama": (supplier.nama if supplier else None),
        "barang_id": item.barang_id,
        "barang_kode": (barang.kode if barang else None),
        "barang_nama": (barang.nama if barang else None),
        "barang_stok": stok,
        "satuan_nama": (getattr(barang.satuan, "nama", None) if barang and getattr(barang, "satuan", None) else None),
        "kekurangan": kekurangan,
        "sudah_dibelanja": sudah,
        "sisa": sisa,
        "status": status,
        "in_draft_po": bool(draft_nos),
        "draft_po_nos": draft_nos,
    }


def get_rop_item_belanja_list(
    db: Session,
    search: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    status_filter: Optional[str] = None,
    need_purchase: bool = False,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Daftar seluruh item ROP berstatus DIBUAT — kolam belanja PIC
    Pembelian (menu Pembelian → Detail ROP, di atas Pesanan).

    - Satu baris = satu item ROP (tidak digabung antar ROP supaya jejak
      ROP/SO per baris jelas; 4 ROP × 10 item = 40 baris).
    - Stok tersedia dibaca dari master barang (total semua gudang); baris
      tanpa link master → stok 0 (seluruh qty dianggap kekurangan).
    - Filter ``need_purchase=True`` → hanya baris dengan sisa > 0.
    """
    query = (
        db.query(RequestOrderPartItem)
        .join(RequestOrderPart, RequestOrderPartItem.request_order_part_id == RequestOrderPart.id)
        .options(
            # Hotfix #038 — SQLAlchemy 2.x menolak string pada joinedload;
            # wajib class-bound attribute (Barang.satuan).
            joinedload(RequestOrderPartItem.barang).joinedload(Barang.satuan),
            joinedload(RequestOrderPartItem.supplier),
            joinedload(RequestOrderPartItem.request_order_part).joinedload(RequestOrderPart.sales_order),
        )
        .filter(RequestOrderPart.status == StatusRequestOrderPart.DIBUAT)
    )
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                RequestOrderPartItem.id_part.ilike(like),
                RequestOrderPartItem.description.ilike(like),
                RequestOrderPart.no_rop.ilike(like),
                RequestOrderPart.project_name.ilike(like),
            )
        )
    if supplier_id:
        query = query.filter(RequestOrderPartItem.supplier_id == supplier_id)
    rows = (
        query.order_by(
            RequestOrderPart.tanggal.desc(),
            RequestOrderPart.created_at.desc(),
            RequestOrderPartItem.no,
        )
        .limit(limit)
        .all()
    )
    if not rows:
        return []

    item_ids = [r.id for r in rows]
    bought_map = _bought_qty_per_rop_item(db, item_ids)
    draft_map = _draft_po_nos_per_rop_item(db, item_ids)

    result = []
    for it in rows:
        row = _rop_item_belanja_row(it, Decimal(str(bought_map.get(it.id, 0) or 0)), draft_map.get(it.id, []))
        if need_purchase and row["sisa"] <= 0:
            continue
        if status_filter and row["status"] != status_filter:
            continue
        result.append(row)
    return result


def _get_or_create_rop_kategori(db: Session):
    """Kategori default barang hasil auto-create dari ROP (get-or-create)."""
    from app.models.master.kategori_barang import KategoriBarang

    kat = db.query(KategoriBarang).filter(func.upper(KategoriBarang.nama) == "ROP").first()
    if kat:
        return kat
    kat = KategoriBarang(kode="ROP", nama="ROP", status="AKTIF")
    db.add(kat)
    db.flush()
    return kat


def _get_or_create_rop_satuan(db: Session):
    """Satuan default barang hasil auto-create dari ROP (get-or-create PCS)."""
    from app.models.master.satuan import Satuan

    sat = db.query(Satuan).filter(func.upper(Satuan.nama) == "PCS").first()
    if sat:
        return sat
    sat = Satuan(nama="PCS", status="AKTIF")
    db.add(sat)
    db.flush()
    return sat


def _generate_barang_kode(db: Session, base: str) -> str:
    """Kode barang unik dari ID PART (sanitasi + anti-bentrok, maks 20 char)."""
    import re

    kode = re.sub(r"[^A-Za-z0-9\-_/]", "-", (base or "").strip().upper())[:20].strip("-") or "ROP"
    if db.query(Barang.id).filter(func.upper(Barang.kode) == kode.upper()).first() is None:
        return kode
    for i in range(2, 100):
        suffix = f"-{i}"
        candidate = kode[: 20 - len(suffix)] + suffix
        if db.query(Barang.id).filter(func.upper(Barang.kode) == candidate.upper()).first() is None:
            return candidate
    raise ValueError("Gagal membuat kode barang unik dari ID PART — ubah kode secara manual di master barang")


def _resolve_or_create_barang_from_rop_item(db: Session, item: RequestOrderPartItem, auto_created: list) -> Barang:
    """Pastikan baris ROP punya barang master (jawaban user #037 no. 6c).

    Urutan resolusi:
    1. ``item.barang_id`` terisi & valid → pakai master itu.
    2. Auto-match by kode: ID PART persis sama dengan kode barang master
       (case-insensitive) → pakai & relink (stok gudang langsung berlaku).
    3. Belum ada → buat barang baru otomatis: kode = ID PART (sanitasi,
       anti-bentrok), nama = description, kategori "ROP", satuan "PCS",
       item_type BARANG_BAKU (part produksi), stock-tracked, harga 0
       (PIC mengisi harga di PO draft). Akun persediaan null → fallback
       posting via item_type (PERSEDIAAN_BAHAN_BAKU) seperti barang master
       lain yang belum dipetakan.
    """
    if item.barang_id:
        existing = db.query(Barang).filter(Barang.id == item.barang_id).first()
        if existing:
            return existing
        # link basi (master dihapus) → jatuh ke resolusi di bawah & relink.

    from app.models.master.barang import ItemTypeBarang

    kode_sumber = (item.id_part or "").strip()
    if kode_sumber:
        matched = db.query(Barang).filter(func.upper(Barang.kode) == kode_sumber.upper()).first()
        if matched:
            item.barang_id = matched.id
            return matched

    kode = _generate_barang_kode(db, kode_sumber or f"ROP-{str(item.id)[:8]}")
    barang = Barang(
        kode=kode,
        nama=(item.description or kode).strip()[:200],
        kategori_id=_get_or_create_rop_kategori(db).id,
        satuan_id=_get_or_create_rop_satuan(db).id,
        item_type=ItemTypeBarang.BARANG_BAKU,
        stock_item=True,
        harga_pokok=Decimal("0"),
        harga_jual=Decimal("0"),
        stok=0,
        stok_minimum=0,
        status="AKTIF",
    )
    db.add(barang)
    db.flush()
    item.barang_id = barang.id
    auto_created.append({"kode": barang.kode, "nama": barang.nama})
    logger.info(f"Barang auto-create dari ROP: {barang.kode} — {barang.nama}")
    return barang


@atomic_accounting_write
def create_po_draft_from_rop_items(
    db: Session,
    item_ids: List[UUID],
    supplier_id: UUID,
    alamat_pengiriman_id: UUID,
    tanggal: Optional[datetime] = None,
    created_by: Optional[UUID] = None,
    keterangan: Optional[str] = None,
) -> Tuple[PurchaseOrder, Dict[str, Any]]:
    """Buat PO DRAFT dari seleksi item ROP (tombol "Buat PO Draft" di menu
    Pembelian → Detail ROP).

    - Item boleh dari BEBERAPA ROP sekaligus (mis. 4 ROP → 1 PO); tiap baris
      PO menyimpan rop_item_id + sales_order_id ROP asalnya (tracking per
      baris, pola #002) + jenis_pembelian default "PROJECT" (bisa diganti
      per baris di form PO — keputusan user "jenis belanja tetap per baris").
    - Qty per baris = sisa kekurangan LANGSUNG saat ini (qty diminta − stok
      tersedia − yang sudah masuk PO aktif lain); pecahan dibulatkan KE ATAS
      supaya tidak kurang beli.
    - Harga = harga pokok master (boleh 0) — PIC pelengkap di PO draft.
    - Barang master untuk part yang belum terdaftar dibuat OTOMATIS
      (lihat _resolve_or_create_barang_from_rop_item).
    - Status PO = DRAFT — TIDAK memanggil workflow direct_complete, jadi
      PO hasil spawn SELALU bisa dikoreksi dulu di menu Pesanan (permintaan
      user: "simpan draft ... agar nanti bisa dikoreksi lagi"), termasuk
      untuk user Administrator.
    """
    if not item_ids:
        raise ValueError("Pilih minimal satu item ROP")
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

    rows = (
        db.query(RequestOrderPartItem)
        .join(RequestOrderPart, RequestOrderPartItem.request_order_part_id == RequestOrderPart.id)
        .options(
            joinedload(RequestOrderPartItem.barang),
            joinedload(RequestOrderPartItem.request_order_part).joinedload(RequestOrderPart.sales_order),
        )
        .filter(
            RequestOrderPartItem.id.in_(item_ids),
            RequestOrderPart.status == StatusRequestOrderPart.DIBUAT,
        )
        .all()
    )
    found_ids = {r.id for r in rows}
    if len(found_ids) != len(set(item_ids)):
        raise ValueError(
            "Ada item yang bukan bagian ROP berstatus DIBUAT (mungkin masih DRAFT atau sudah dihapus) — muat ulang daftar Detail ROP"
        )

    # Urutkan baris PO: ROP terlama dulu lalu no baris (kronologis & rapi).
    rows = sorted(
        rows,
        key=lambda it: (it.request_order_part.tanggal, it.request_order_part.no_rop, it.no),
    )

    bought_map = _bought_qty_per_rop_item(db, [r.id for r in rows])
    auto_created: list = []
    skipped: list = []
    linked_rops: list = []
    details_data: list = []

    for it in rows:
        barang = _resolve_or_create_barang_from_rop_item(db, it, auto_created)
        qty = safe_decimal(it.qty) or Decimal("0")
        stok = int(getattr(barang, "stok", 0) or 0)
        kekurangan = qty - Decimal(stok)
        if kekurangan < 0:
            kekurangan = Decimal("0")
        sudah = Decimal(str(bought_map.get(it.id, 0) or 0))
        sisa = kekurangan - sudah
        if sisa < 0:
            sisa = Decimal("0")
        if sisa <= 0:
            skipped.append(
                {
                    "no_rop": it.request_order_part.no_rop,
                    "id_part": it.id_part,
                    "alasan": "stok gudang cukup / kekurangan sudah masuk PO lain",
                }
            )
            continue
        # PO qty bertipe integer — pecahan dibulatkan ke atas (tidak kurang beli).
        qty_po = int(sisa.quantize(Decimal("1"), rounding=ROUND_CEILING))
        if qty_po <= 0:
            skipped.append(
                {
                    "no_rop": it.request_order_part.no_rop,
                    "id_part": it.id_part,
                    "alasan": "sisa kekurangan terlalu kecil",
                }
            )
            continue
        harga = safe_decimal(getattr(barang, "harga_pokok", 0)) or Decimal("0")
        details_data.append(
            {
                "barang_id": barang.id,
                "harga": harga,
                "qty": qty_po,
                "diskon": Decimal("0"),
                "sub_total": harga * qty_po,
                "satuan_id": getattr(barang, "satuan_id", None),
                # Tracking per baris (#002): SO asal dari ROP + jenis default
                # PROJECT (editabel per baris di PO draft — keputusan user).
                "sales_order_id": it.request_order_part.sales_order_id,
                "jenis_pembelian": "PROJECT",
                # Jejak baris ROP sumber (#037).
                "rop_item_id": it.id,
            }
        )
        no_rop = it.request_order_part.no_rop
        if no_rop not in linked_rops:
            linked_rops.append(no_rop)

    if not details_data:
        raise ValueError("Semua item terpilih tidak perlu dibelanjakan (stok cukup atau kekurangan sudah masuk PO lain)")

    ket = (keterangan or "").strip()
    if not ket:
        ket = f"Dari ROP: {', '.join(linked_rops)}"
    if skipped:
        ket += f" — {len(skipped)} item terpilih dilewati (stok cukup/sudah dibelanjakan)"

    po = create_purchase_order(
        db=db,
        tanggal=tanggal or datetime.now(timezone.utc),
        supplier_id=supplier_id,
        details_data=details_data,
        biaya_data=[],
        keterangan=ket,
        created_by=created_by,
        alamat_pengiriman_id=alamat_pengiriman_id,
    )
    info = {"skipped": skipped, "auto_created_barang": auto_created, "linked_rops": linked_rops}
    logger.info(
        f"PO draft dari ROP: {po.no_pesanan} | {len(details_data)} baris dari {len(linked_rops)} ROP "
        f"| auto-create barang: {len(auto_created)}"
    )
    return po, info


# ==========================================
# === Update #043 — PART STOK MENIPIS (restock) ===
# Daftar part master dengan stok < stok_minimum (menu Pembelian → Part Stok
# Menipis) → tombol "Buat PO Draft" → PO DRAFT restock ke supplier pilihan.
# Berbeda dari Detail ROP (kolam belanja part produksi berbasis item ROP),
# sumber data adalah MASTER BARANG langsung: stok, stok_minimum, kekurangan
# = stok_minimum − stok. "Sudah dibeli" dihitung dari baris PO yang masih
# berstatus DRAFT/DIPROSES (sudah dipesan tapi BELUM diterima — PO SELESAI
# sudah menambah stok sehingga tidak dihitung ganda; DIBATALKAN bebas).
# ==========================================

def _inflight_po_per_barang(db: Session, barang_ids: List[UUID]) -> Dict[UUID, Dict[str, Any]]:
    """Agregat belanja "dalam penerbangan" per barang (#043 — Part Stok
    Menipis): Σ qty + daftar nomor PO penyumbang (urut asc) dari baris PO
    aktif DRAFT/DIPROSES. Satu query JOIN (hindari N+1), derived — tanpa
    kolom status tersimpan."""
    if not barang_ids:
        return {}
    rows = (
        db.query(PurchaseOrderDetail.barang_id, PurchaseOrder.no_pesanan, PurchaseOrderDetail.qty)
        .join(PurchaseOrder, PurchaseOrderDetail.purchase_order_id == PurchaseOrder.id)
        .filter(
            PurchaseOrderDetail.barang_id.in_(barang_ids),
            PurchaseOrder.status.in_((StatusPenjualan.DRAFT, StatusPenjualan.DIPROSES)),
        )
        .order_by(PurchaseOrder.no_pesanan)
        .all()
    )
    result: Dict[UUID, Dict[str, Any]] = {}
    for barang_id, no_pesanan, qty in rows:
        agg = result.setdefault(barang_id, {"qty": 0, "po_nos": []})
        agg["qty"] += int(qty or 0)
        if no_pesanan and no_pesanan not in agg["po_nos"]:
            agg["po_nos"].append(no_pesanan)
    return result


def get_part_stok_menipis_list(
    db: Session,
    need_purchase: bool = False,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Daftar part master dengan stok di bawah minimum — menu Pembelian →
    Part Stok Menipis (restock, Update #043).

    - Hanya barang AKTIF dengan stok_minimum > 0 dan stok < stok_minimum,
      urut kode (mirror pola query & filter get_rop_item_belanja_list).
    - sudah_dibeli = Σ qty baris PO DRAFT/DIPROSES yang menaut barang
      (belum diterima); draft_po_nos = nomor PO penyumbang.
    - kekurangan = max(0, stok_minimum − stok); sisa = max(0, kekurangan −
      sudah_dibeli); status BELUM/SEBAGIAN/LENGKAP/STOK_CUKUP (enum konsisten
      Detail ROP — untuk daftar ini semua baris memang menipis, STOK_CUKUP
      disiapkan bila kekurangan ≤ 0).
    - Filter ``need_purchase=True`` → hanya baris dengan sisa > 0.
    """
    rows = (
        db.query(Barang)
        .options(joinedload(Barang.satuan))
        .filter(
            Barang.status == "AKTIF",
            Barang.stok_minimum > 0,
            Barang.stok < Barang.stok_minimum,
        )
        .order_by(Barang.kode)
        .limit(limit)
        .all()
    )
    if not rows:
        return []

    inflight = _inflight_po_per_barang(db, [r.id for r in rows])

    result = []
    for barang in rows:
        stok = int(barang.stok or 0)
        stok_min = int(barang.stok_minimum or 0)
        kekurangan = stok_min - stok
        if kekurangan < 0:
            kekurangan = 0
        agg = inflight.get(barang.id) or {}
        sudah = int(agg.get("qty") or 0)
        po_nos = list(agg.get("po_nos") or [])
        sisa = kekurangan - sudah
        if sisa < 0:
            sisa = 0
        if kekurangan <= 0:
            status = ROP_BELANJA_STOK_CUKUP
        elif sisa <= 0:
            status = ROP_BELANJA_LENGKAP
        elif sudah > 0:
            status = ROP_BELANJA_SEBAGIAN
        else:
            status = ROP_BELANJA_BELUM
        if need_purchase and sisa <= 0:
            continue
        result.append(
            {
                "barang_id": barang.id,
                "kode": barang.kode,
                "nama": barang.nama,
                "satuan": (barang.satuan.nama if barang.satuan else None),
                "stok": stok,
                "stok_minimum": stok_min,
                "kekurangan": kekurangan,
                "sudah_dibeli": sudah,
                "sisa": sisa,
                "status": status,
                "draft_po_nos": po_nos,
                "in_draft_po": bool(po_nos),
            }
        )
    return result


@atomic_accounting_write
def create_po_draft_from_stok_menipis(
    db: Session,
    supplier_id: UUID,
    gudang_id: Optional[UUID],
    tanggal: Optional[datetime] = None,
    catatan: Optional[str] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    created_by: Optional[UUID] = None,
) -> Tuple[PurchaseOrder, Dict[str, Any]]:
    """Buat PO DRAFT restock dari seleksi part stok menipis (tombol "Buat PO
    Draft" di menu Pembelian → Part Stok Menipis, Update #043).

    - Mirror create_po_draft_from_rop_items: supplier wajib ada; nomor PO
      pakai helper penomoran tahunan yang sama (PO ASI/YYYY/NNN); harga per
      baris = harga_pokok master (boleh 0 — PIC melengkapi di PO draft);
      PO disimpan DRAFT — TIDAK memanggil workflow direct_complete supaya
      bisa dikoreksi dulu di menu Pesanan (finalisasi saat diedit & disimpan).
    - gudang_id = id master ALAMAT_PENGIRIMAN, OPSIONAL: terisi → snapshot
      alamat + PIC gudang + telepon (mirror create_purchase_order); None →
      kolom alamat pengiriman PO dibiarkan kosong.
    - Qty final per baris = min(qty diminta, sisa kekurangan saat ini);
      qty ≤ 0 / barang tidak ada di master → masuk daftar "skipped".
    - jenis_pembelian baris = "PROJECT" (nilai valid existing — tidak ada
      opsi "MATERIAL" di JENIS_PEMBELIAN_VALUES; ROP flow juga memakai
      PROJECT) — baris PO tidak punya kolom keterangan sehingga rincian
      per baris "Stok menipis: {kode} {nama}" dituangkan ke keterangan
      header (gabung catatan user + "Restock part stok menipis (N item)").
    """
    if not items:
        raise ValueError("Pilih minimal satu part")
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

    # Gudang tujuan (master AlamatPengiriman) — OPSIONAL, None → kosong.
    alamat_kirim = None
    if gudang_id is not None:
        from app.models.master.alamat_pengiriman import AlamatPengiriman

        alamat_kirim = db.query(AlamatPengiriman).filter(AlamatPengiriman.id == gudang_id).first()
        if not alamat_kirim:
            raise ValueError("Gudang (alamat pengiriman) tidak ditemukan")
    alamat_pengiriman_snapshot = f"{alamat_kirim.prefix}\n{alamat_kirim.nama}" if alamat_kirim else None
    pic_gudang = ((alamat_kirim.pic_nama or "").strip() or None) if alamat_kirim else None
    pic_gudang_telepon = ((alamat_kirim.pic_telepon or "").strip() or None) if alamat_kirim else None
    # === Update #067 — PIC Supplier & No. HP: snapshot kontak supplier (mirror
    # fallback create_purchase_order — draft restock tidak punya input form). ===
    pic_supplier = (supplier.kontak_person or "").strip() or None
    pic_supplier_telepon = (supplier.telepon or "").strip() or None

    # Validasi mata uang default (mirror create_purchase_order).
    from app.models.master.mata_uang import MataUang

    if db.query(MataUang).filter(MataUang.kode == "IDR").first() is None:
        raise ValueError("Mata uang IDR tidak dikenal — tambahkan dulu di Pengaturan → Profil Perusahaan")

    # Agregasi qty per barang (guard payload duplikat baris — FE normalnya
    # satu baris per part; qty duplikat digabung dulu sebelum dikunci ke sisa).
    qty_per_barang: Dict[UUID, int] = {}
    for it in items:
        bid = it.get("barang_id")
        if bid is None:
            continue
        qty_per_barang[bid] = qty_per_barang.get(bid, 0) + int(it.get("qty") or 0)
    barang_ids = list(qty_per_barang.keys())
    barangs = {
        b.id: b
        for b in db.query(Barang).filter(Barang.id.in_(barang_ids)).all()
    } if barang_ids else {}
    inflight = _inflight_po_per_barang(db, barang_ids)

    skipped: list = []
    details_data: list = []
    baris_kets: list = []

    for bid, qty_diminta in qty_per_barang.items():
        barang = barangs.get(bid)
        if barang is None:
            skipped.append({"kode": None, "nama": None, "alasan": "barang tidak ditemukan di master"})
            continue
        stok = int(barang.stok or 0)
        stok_min = int(barang.stok_minimum or 0)
        kekurangan = stok_min - stok
        if kekurangan < 0:
            kekurangan = 0
        sudah = int((inflight.get(bid) or {}).get("qty") or 0)
        sisa = kekurangan - sudah
        if sisa < 0:
            sisa = 0
        qty_final = min(qty_diminta, sisa)
        if qty_final <= 0:
            skipped.append(
                {
                    "kode": barang.kode,
                    "nama": barang.nama,
                    "alasan": "stok sudah cukup / kekurangan sudah masuk PO lain",
                }
            )
            continue
        harga = safe_decimal(getattr(barang, "harga_pokok", 0)) or Decimal("0")
        details_data.append(
            {
                "barang_id": barang.id,
                "harga": harga,
                "qty": qty_final,
                "diskon": Decimal("0"),
                "sub_total": harga * qty_final,
                "satuan_id": getattr(barang, "satuan_id", None),
                # Nilai valid existing (JENIS_PEMBELIAN_VALUES: CONSUMABLE /
                # PROJECT / ASSET / ATK) — mirror pilihan ROP flow.
                "jenis_pembelian": "PROJECT",
            }
        )
        baris_kets.append(f"Stok menipis: {barang.kode} {barang.nama}")

    if not details_data:
        raise ValueError("Semua part terpilih tidak perlu direstock (stok sudah cukup atau kekurangan sudah masuk PO lain)")

    _validate_po_detail_refs(db, details_data)

    # Keterangan header: catatan user + label restock + rincian per baris
    # (tabel detail PO tidak punya kolom keterangan — mirror gaya ROP
    # "Dari ROP: ..." yang menaut konteks sumber di header).
    ket_parts = []
    catatan_bersih = (catatan or "").strip()
    if catatan_bersih:
        ket_parts.append(catatan_bersih)
    ket_parts.append(f"Restock part stok menipis ({len(details_data)} item)")
    ket = " — ".join(ket_parts) + ": " + ", ".join(baris_kets)
    if skipped:
        ket += f" — {len(skipped)} item terpilih dilewati (stok sudah cukup/sudah dibeli)"

    tanggal_final = tanggal or datetime.now(timezone.utc)
    sub_total, total_diskon = _hitung_total_detail_with_diskon(details_data)
    total_ppn, total_pph23, grand_total = _hitung_grand_total(
        sub_total, total_diskon, Decimal("11"), Decimal("0"),
        ppn_applicable=True,
        pph23_pct=Decimal("2"),
        pph23_applicable=False,
    )

    no_pesanan = get_nomor_dokumen_tahunan(
        db, PurchaseOrder, prefix="PO",
        no_column="no_pesanan", tanggal=tanggal_final.date(),
        doc_type="PURCHASE_ORDER",
    )

    # Bangun PO manual (mirror create_purchase_order) karena alamat pengiriman
    # OPSIONAL di sini — create_purchase_order mewajibkannya (Update ASAHI #3).
    po = PurchaseOrder(
        no_pesanan=no_pesanan,
        tanggal=tanggal_final,
        supplier_id=supplier_id,
        tanggal_kirim=None,
        alamat=None,
        diskon_global=Decimal("0"),
        ppn=Decimal("11"),
        ppn_applicable=True,
        pph23_applicable=False,
        pph23=Decimal("2"),
        total_pph23=total_pph23,
        sub_total=sub_total,
        total_diskon=total_diskon,
        total_ppn=total_ppn,
        total_biaya_tambahan=Decimal("0"),
        grand_total=grand_total,
        auto_post_jurnal=False,
        status=StatusPenjualan.DRAFT,
        keterangan=ket,
        created_by=created_by,
        currency="IDR",
        alamat_pengiriman_id=(alamat_kirim.id if alamat_kirim else None),
        alamat_pengiriman=alamat_pengiriman_snapshot,
        ppic=False,
        pic_gudang=pic_gudang,
        pic_gudang_telepon=pic_gudang_telepon,
        # === Update #067 — PIC Supplier & No. HP (snapshot) ===
        pic_supplier=pic_supplier,
        pic_supplier_telepon=pic_supplier_telepon,
    )
    db.add(po)
    db.flush()

    for d in details_data:
        db.add(
            PurchaseOrderDetail(
                purchase_order_id=po.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=safe_decimal(d.get("sub_total")),
                satuan_id=d.get("satuan_id"),
                jenis_pembelian=(d.get("jenis_pembelian") or "").strip() or None,
            )
        )

    # Hitung ulang total (diskon baris + diskon global) — pola create_purchase_order.
    from app.services.document_totals import refresh_totals

    db.flush()
    refresh_totals(po)

    db.commit()
    db.refresh(po)
    info = {"skipped": skipped, "jumlah_item": len(details_data)}
    logger.info(
        f"PO draft restock stok menipis: {po.no_pesanan} | {len(details_data)} baris "
        f"| supplier {supplier_id} | dilewati: {len(skipped)}"
    )
    return po, info


# ==========================================
# PURCHASE INVOICE
# ==========================================

def get_purchase_invoice_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PurchaseInvoice], int]:
    """Ambil daftar purchase invoice dengan filter & pagination."""
    query = db.query(PurchaseInvoice).options(
        joinedload(PurchaseInvoice.supplier),
        # Update #7 (B-06): eager-load PO sumber — kolom "PO" di UI & form retur
        joinedload(PurchaseInvoice.purchase_order),
        joinedload(PurchaseInvoice.creator),
        joinedload(PurchaseInvoice.details).joinedload(PurchaseInvoiceDetail.barang).joinedload(Barang.satuan),
        joinedload(PurchaseInvoice.details).joinedload(PurchaseInvoiceDetail.satuan),
        joinedload(PurchaseInvoice.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PurchaseInvoice.no_form.ilike(pattern)
            | PurchaseInvoice.no_faktur.ilike(pattern)
            | PurchaseInvoice.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PurchaseInvoice.status == status)
    if supplier_id:
        query = query.filter(PurchaseInvoice.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(PurchaseInvoice.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PurchaseInvoice.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PurchaseInvoice.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_purchase_invoice_by_id(db: Session, inv_id: UUID) -> Optional[PurchaseInvoice]:
    """Ambil 1 purchase invoice berdasarkan ID dengan detail."""
    return (
        db.query(PurchaseInvoice)
        .options(
            joinedload(PurchaseInvoice.supplier),
            # Update #7 (B-06): eager-load PO sumber — kolom "PO" di UI & form retur
            joinedload(PurchaseInvoice.purchase_order),
            joinedload(PurchaseInvoice.creator),
            joinedload(PurchaseInvoice.jurnal),
            joinedload(PurchaseInvoice.details).joinedload(PurchaseInvoiceDetail.barang).joinedload(Barang.satuan),
            joinedload(PurchaseInvoice.details).joinedload(PurchaseInvoiceDetail.satuan),
            joinedload(PurchaseInvoice.biaya_tambahan),
        )
        .filter(PurchaseInvoice.id == inv_id)
        .first()
    )


@atomic_accounting_write
def create_purchase_invoice(
    db: Session,
    tanggal: datetime,
    supplier_id: UUID,
    no_faktur: str,
    details_data: list,
    biaya_data: Optional[list] = None,
    alamat: Optional[str] = None,
    diskon_global: Optional[Decimal] = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: Optional[UUID] = None,
    tanggal_jatuh_tempo=None,
    syarat_bayar_id=None,
    # Update #7 (B-06): link invoice ke Purchase Order sumber (opsional).
    purchase_order_id: UUID | None = None,
    # === Update #052 — pilihan PPh23/PPN saat input Invoice Pembelian
    # (mirror PO & Invoice Penjualan; sebelumnya PPh23 diabaikan backend). ===
    ppn_applicable: bool = True,
    pph23_applicable: bool = False,
    pph23: Decimal = Decimal("2"),
) -> PurchaseInvoice:
    """Buat PurchaseInvoice baru beserta detail + biaya tambahan."""
    try:
        biaya_data = biaya_data or []

        # Validasi supplier
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

        # === Update #052 — normalisasi tarif None (schema optional) ===
        if ppn is None:
            ppn = Decimal("11")
        if pph23 is None:
            pph23 = Decimal("2")

        # Hitung sub_total dan total_diskon dari detail
        sub_total, total_diskon = _hitung_total_detail_with_diskon(details_data)

        total_biaya_tambahan = _hitung_total_biaya(biaya_data)
        # === Update #052 — PPh23/PPN opsional pada Invoice Pembelian ===
        total_ppn, total_pph23, grand_total = _hitung_grand_total(
            sub_total, total_diskon, ppn, total_biaya_tambahan,
            ppn_applicable=ppn_applicable,
            pph23_pct=pph23,
            pph23_applicable=pph23_applicable,
        )

        # Generate nomor form (invoice pembelian)
        # Update ASAHI: pola penomoran seragam "PINV ASI/YYYY/NNN" (reset tahunan).
        # === Update ASAHI #019 — prefix & kode perusahaan via Preferensi ===
        # (doc_type PURCHASE_INVOICE; fallback "PINV" — perilaku lama bila belum diubah).
        no_form = get_nomor_dokumen_tahunan(
            db, PurchaseInvoice, prefix="PINV",
            no_column="no_form", tanggal=tanggal.date(),
            doc_type="PURCHASE_INVOICE",
        )

        # Buat header
        inv = PurchaseInvoice(
            no_form=no_form,
            no_faktur=no_faktur,
            tanggal=tanggal,
            supplier_id=supplier_id,
            alamat=alamat,
            diskon_global=diskon_global,
            ppn=ppn,
            # === Update #052 — pilihan PPh23/PPN tersimpan (mirror PO) ===
            ppn_applicable=ppn_applicable,
            pph23_applicable=pph23_applicable,
            pph23=pph23 if pph23 is not None else Decimal("2"),
            total_pph23=total_pph23,
            sub_total=sub_total,
            total_diskon=total_diskon,
            total_ppn=total_ppn,
            total_biaya_tambahan=total_biaya_tambahan,
            grand_total=grand_total,
            auto_post_jurnal=auto_post_jurnal,
            status=StatusPenjualan.DRAFT,
            keterangan=keterangan,
            created_by=created_by,
            # Update #7 (B-06): persist link ke PO sumber — sebelumnya field
            # schema PurchaseInvoiceBase.purchase_order_id tidak pernah
            # diteruskan ke sini sehingga kolom DB selalu NULL ("-" di UI
            # dan form retur tidak auto-terisi).
            purchase_order_id=purchase_order_id,
        )
        db.add(inv)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PurchaseInvoiceDetail(
                purchase_invoice_id=inv.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=Decimal(str(d["sub_total"])),
                # Phase D: UOM on detail
                satuan_id=d.get("satuan_id"),
                # === Phase 5 — source-line trace (Roadmap §20) ===
                purchase_order_detail_id=d.get("purchase_order_detail_id"),
                penerimaan_barang_detail_id=d.get("penerimaan_barang_detail_id"),
            )
            db.add(detail)

        # Buat biaya tambahan
        _create_biaya_tambahan(db, inv, biaya_data, "purchase_invoice_id")

        # Auto-post jurnal (D: Persediaan + PPN Masukan, K: Utang Dagang)
        # Guard: skip jika supplier belum punya akun hutang (Phase 3 akan ganti mekanisme COA)
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(inv)
        if auto_post_jurnal:
            post_purchase_invoice(db, inv, created_by)

        from app.services.invoice_terms import set_due_date
        if syarat_bayar_id is not None:
            inv.syarat_bayar_id = syarat_bayar_id
        set_due_date(db, inv, tanggal_jatuh_tempo)
        db.commit()
        db.refresh(inv)
        logger.info(f"PurchaseInvoice created: {no_form} | faktur={no_faktur} | grand_total={grand_total}")
        return inv

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseInvoice: {e}")
        raise


@atomic_accounting_write
def update_purchase_invoice(
    db: Session,
    db_obj: PurchaseInvoice,
    tanggal: Optional[datetime] = None,
    supplier_id: Optional[UUID] = None,
    no_faktur: Optional[str] = None,
    alamat: Optional[str] = None,
    diskon_global: Optional[Decimal] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    tanggal_jatuh_tempo=None,
    syarat_bayar_id=None,
    details: list | None = None,
    # Update #7 (B-06): link/ubah PO sumber saat invoice masih draft.
    purchase_order_id: UUID | None = None,
    # === Update #052 — pilihan PPh23/PPN pada update (draft saja) ===
    ppn_applicable: Optional[bool] = None,
    pph23_applicable: Optional[bool] = None,
    pph23: Optional[Decimal] = None,
) -> PurchaseInvoice:
    """Update data purchase invoice (field header + opsional replace detail)."""
    require_unposted(db_obj)
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Purchase Invoice dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if supplier_id is not None:
        db_obj.supplier_id = supplier_id
    if no_faktur is not None:
        db_obj.no_faktur = no_faktur
    if alamat is not None:
        db_obj.alamat = alamat
    if diskon_global is not None:
        db_obj.diskon_global = diskon_global
    if ppn is not None:
        db_obj.ppn = ppn
    # === Update #052 — pilihan pajak PPN/PPh23 ===
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
    # Update #7 (B-06): set link PO bila dikirim (semua pemanggil lama tanpa
    # field ini tidak terpengaruh — default None berarti tidak diubah).
    if purchase_order_id is not None:
        db_obj.purchase_order_id = purchase_order_id

    # Update #3 — penggantian baris detail (persist link source-line
    # purchase_order_detail_id / penerimaan_barang_detail_id + satuan_id)
    # saat invoice masih draft.
    if details is not None:
        db_obj.details.clear()
        db.flush()
        for d in details:
            db_obj.details.append(PurchaseInvoiceDetail(
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=Decimal(str(d.get("sub_total", 0))),
                satuan_id=d.get("satuan_id"),
                purchase_order_detail_id=d.get("purchase_order_detail_id"),
                penerimaan_barang_detail_id=d.get("penerimaan_barang_detail_id"),
            ))
        db.flush()

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    from app.services.invoice_terms import set_due_date
    if syarat_bayar_id is not None:
        db_obj.syarat_bayar_id = syarat_bayar_id
    if tanggal_jatuh_tempo is not None or tanggal is not None or syarat_bayar_id is not None:
        set_due_date(db, db_obj, tanggal_jatuh_tempo)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_purchase_invoice(db: Session, db_obj: PurchaseInvoice, user_id: Optional[UUID] = None) -> PurchaseInvoice:
    """Batalkan purchase invoice."""
    receipt_control.require_no_completed_receipts(db, db_obj)
    from app.services.settlement_service import require_no_settlements
    require_no_settlements(db, db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Invoice sudah dibatalkan")
    # === Update #052 — PO terkait di-capture SEBELUM status diubah supaya
    # sync pasca-pembatalan masih menemukan relasinya. ===
    _po_ids = po_ids_of_invoice(db, db_obj)
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    # === Update #052 — PO SELESAI kembali DIPROSES bila rantainya tidak
    # lagi lengkap (invoice batal). Tanpa commit — transaksi pemanggil. ===
    if _po_ids:
        try:
            sync_po_completion(db, _po_ids)
        except Exception as sync_exc:  # noqa: BLE001 — jangan gagalkan pembatalan
            logger.warning(f"Sync status PO pasca-batal invoice {db_obj.no_form} dilewati: {sync_exc}")
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseInvoice cancelled: {db_obj.no_form}")
    return db_obj


# ==========================================
# === Update #052 — SINKRONISASI STATUS PO ===
# ==========================================
# Mirror sync_so_completion (#046): selama ini TIDAK ADA mekanisme yang
# menaikkan status PO dari DIPROSES — PO "nyangkut" di Diproses selamanya
# walau barang sudah diterima, diinvois, dan dibayar penuh.

def po_ids_of_invoice(db: Session, inv: PurchaseInvoice) -> set:
    """Kumpulkan ID PO yang terhubung ke sebuah invoice pembelian:
    link langsung (purchase_order_id) + PO dari penerimaan yang ter-link
    (penerimaan.purchase_invoice_id) + PO lewat match table."""
    po_ids = set()
    if getattr(inv, "purchase_order_id", None):
        po_ids.add(inv.purchase_order_id)
    receipt_rows = db.query(PenerimaanBarang.purchase_order_id).filter(
        PenerimaanBarang.purchase_invoice_id == inv.id,
        PenerimaanBarang.purchase_order_id.isnot(None),
    ).all()
    po_ids |= {row[0] for row in receipt_rows if row[0]}
    match_rows = (
        db.query(PenerimaanBarang.purchase_order_id)
        .join(PenerimaanBarangDetail,
              PenerimaanBarangDetail.penerimaan_barang_id == PenerimaanBarang.id)
        .join(PurchaseInvoiceReceiptMatch,
              PurchaseInvoiceReceiptMatch.penerimaan_barang_detail_id == PenerimaanBarangDetail.id)
        .join(PurchaseInvoiceDetail,
              PurchaseInvoiceDetail.id == PurchaseInvoiceReceiptMatch.purchase_invoice_detail_id)
        .filter(PurchaseInvoiceDetail.purchase_invoice_id == inv.id,
                PenerimaanBarang.purchase_order_id.isnot(None))
        .all()
    )
    po_ids |= {row[0] for row in match_rows if row[0]}
    return po_ids


def _po_linked_invoices(db: Session, po_id) -> list:
    """Invoice pembelian AKTIF (non-DIBATALKAN) yang terhubung ke PO —
    jalur: link langsung, penerimaan.purchase_invoice_id, atau match table."""
    inv_ids = {
        row[0] for row in db.query(PurchaseInvoice.id).filter(
            PurchaseInvoice.purchase_order_id == po_id,
            PurchaseInvoice.status != StatusPenjualan.DIBATALKAN,
        ).all()
    }
    receipt_ids = [
        row[0] for row in db.query(PenerimaanBarang.id).filter(
            PenerimaanBarang.purchase_order_id == po_id).all()
    ]
    if receipt_ids:
        via_pb = db.query(PenerimaanBarang.purchase_invoice_id).filter(
            PenerimaanBarang.id.in_(receipt_ids),
            PenerimaanBarang.purchase_invoice_id.isnot(None),
        ).all()
        inv_ids |= {row[0] for row in via_pb if row[0]}
        via_match = (
            db.query(PurchaseInvoiceDetail.purchase_invoice_id)
            .join(PurchaseInvoiceReceiptMatch,
                  PurchaseInvoiceReceiptMatch.purchase_invoice_detail_id == PurchaseInvoiceDetail.id)
            .join(PenerimaanBarangDetail,
                  PenerimaanBarangDetail.id == PurchaseInvoiceReceiptMatch.penerimaan_barang_detail_id)
            .filter(PenerimaanBarangDetail.penerimaan_barang_id.in_(receipt_ids))
            .all()
        )
        inv_ids |= {row[0] for row in via_match if row[0]}
    if not inv_ids:
        return []
    return db.query(PurchaseInvoice).filter(
        PurchaseInvoice.id.in_(inv_ids),
        PurchaseInvoice.status != StatusPenjualan.DIBATALKAN,
    ).all()


def _po_fully_received(db: Session, po: PurchaseOrder) -> bool:
    """Setiap baris PO sudah diterima penuh oleh penerimaan berstatus
    SELESAI (ditelusuri lewat purchase_order_detail_id; over-receipt OK)."""
    lines = list(po.details)
    if not lines:
        return False
    for line in lines:
        received = (
            db.query(func.sum(PenerimaanBarangDetail.qty))
            .join(PenerimaanBarang,
                  PenerimaanBarangDetail.penerimaan_barang_id == PenerimaanBarang.id)
            .filter(
                PenerimaanBarangDetail.purchase_order_detail_id == line.id,
                PenerimaanBarang.status == StatusPenjualan.SELESAI,
            ).scalar()
            or 0
        )
        if int(received) < int(line.qty or 0):
            return False
    return True


def sync_po_completion(db: Session, po_ids) -> None:
    """=== Update #052 — PO ikut SELESAI saat rantainya tuntas. ===

    Dipanggil setelah titik yang mengubah kelengkapan rantai PO (penerimaan
    selesai, invoice diposting/dibatalkan, pembayaran hutang diposting /
    dibatalkan). Aturan:

    - PO DIPROSES dinaikkan ke SELESAI bila SEMUA terpenuhi:
      (1) barang diterima penuh (per baris, lewat penerimaan SELESAI)
          ATAU invoice terhubung menutup nilai PO (Σ grand_total ≥
          grand_total PO − 0,005 — menutup alur belanja jasa tanpa
          penerimaan), dan
      (2) ada minimal satu invoice aktif yang terhubung, dan
      (3) SEMUA invoice terhubung sudah terpelunasi penuh
          (status pembayaran LUNAS / LEBIH_BAYAR).
    - Sebaliknya PO yang sudah SELESAI dikembalikan ke DIPROSES bila
      rantainya tidak lagi tuntas (invoice dibatalkan / pelunasan dibatalkan)
      — selama PO belum DIBATALKAN.

    Idempotent; tidak melakukan commit (pemanggil memiliki transaksi).
    """
    from app.services import settlement_service as settlement

    for po_id in {p for p in po_ids if p}:
        po = db.get(PurchaseOrder, po_id)
        if po is None:
            continue
        current = getattr(po.status, "value", po.status)
        if current == StatusPenjualan.DIBATALKAN.value:
            continue
        invoices = _po_linked_invoices(db, po_id)
        billed = sum((safe_decimal(inv.grand_total or 0) for inv in invoices), Decimal("0"))
        fully_billed = (
            bool(invoices)
            and safe_decimal(po.grand_total or 0) - billed <= Decimal("0.005")
        )
        fully_received = _po_fully_received(db, po)
        all_settled = bool(invoices) and all(
            b["status_pembayaran"] in ("LUNAS", "LEBIH_BAYAR")
            for b in settlement.balances(db, invoices).values()
        )
        complete = (fully_received or fully_billed) and all_settled
        if complete:
            # Hanya DIPROSES yang dinaikkan (DRAFT belum difinalisasi —
            # tidak lompat ke SELESAI; SELESAI tetap).
            if current == StatusPenjualan.DIPROSES.value:
                po.status = StatusPenjualan.SELESAI
                db.add(po)
                logger.info(
                    f"Update #052 — PO {po.no_pesanan} → SELESAI "
                    f"(diterima/ditagih penuh & terpelunasi)"
                )
        elif current == StatusPenjualan.SELESAI.value:
            po.status = StatusPenjualan.DIPROSES
            db.add(po)
            logger.info(
                f"Update #052 — PO {po.no_pesanan} SELESAI → DIPROSES "
                f"(rantai penerimaan/invoice/pelunasan tidak lagi tuntas)"
            )


# ==========================================
# PURCHASE RETUR
# ==========================================

def get_purchase_retur_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PurchaseRetur], int]:
    """Ambil daftar purchase retur dengan filter & pagination."""
    query = db.query(PurchaseRetur).options(
        joinedload(PurchaseRetur.purchase_order),
        joinedload(PurchaseRetur.supplier),
        joinedload(PurchaseRetur.creator),
        joinedload(PurchaseRetur.details).joinedload(PurchaseReturDetail.barang).joinedload(Barang.satuan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PurchaseRetur.no_retur.ilike(pattern)
            | PurchaseRetur.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PurchaseRetur.status == status)
    if supplier_id:
        query = query.filter(PurchaseRetur.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(PurchaseRetur.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PurchaseRetur.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PurchaseRetur.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_purchase_retur_by_id(db: Session, retur_id: UUID) -> Optional[PurchaseRetur]:
    """Ambil 1 purchase retur berdasarkan ID dengan detail."""
    return (
        db.query(PurchaseRetur)
        .options(
            joinedload(PurchaseRetur.purchase_order),
            joinedload(PurchaseRetur.supplier),
            joinedload(PurchaseRetur.creator),
            joinedload(PurchaseRetur.jurnal),
            joinedload(PurchaseRetur.details).joinedload(PurchaseReturDetail.barang).joinedload(Barang.satuan),
        )
        .filter(PurchaseRetur.id == retur_id)
        .first()
    )


@atomic_accounting_write
def create_purchase_retur(
    db: Session,
    tanggal: datetime,
    purchase_order_id: UUID,
    supplier_id: UUID,
    details_data: list,
    alamat: Optional[str] = None,
    ppn: Decimal = Decimal("11"),
    keterangan: Optional[str] = None,
    auto_post_jurnal: bool = False,
    created_by: Optional[UUID] = None,
    purchase_invoice_id=None,
    gudang_id: Optional[UUID] = None,
) -> PurchaseRetur:
    """Buat PurchaseRetur baru beserta detail.
    Jurnal: D - Utang Dagang, K - Retur Pembelian / Persediaan
    """
    try:
        # Validasi supplier
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

        # Hitung sub_total dari detail (tanpa diskon)
        sub_total = _hitung_total_detail_no_diskon(details_data)

        total_ppn, _total_pph23, grand_total = _hitung_grand_total(
            sub_total, Decimal("0"), ppn, Decimal("0")
        )

        # Generate nomor retur
        # Update ASAHI: pola penomoran seragam "RET-B ASI/YYYY/NNN" (reset tahunan).
        # === Update ASAHI #019 — prefix & kode perusahaan via Preferensi ===
        # (doc_type RETUR_PEMBELIAN; fallback "RET-B" — perilaku lama bila belum diubah).
        no_retur = get_nomor_dokumen_tahunan(
            db, PurchaseRetur, prefix="RET-B",
            no_column="no_retur", tanggal=tanggal.date(),
            doc_type="RETUR_PEMBELIAN",
        )

        # Buat header
        retur = PurchaseRetur(
            gudang_id=gudang_id,
            no_retur=no_retur,
            tanggal=tanggal,
            purchase_order_id=purchase_order_id,
            supplier_id=supplier_id,
            alamat=alamat,
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
            detail = PurchaseReturDetail(
                purchase_retur_id=retur.id,
                barang_id=d["barang_id"],
                harga=Decimal(str(d["harga"])),
                qty=int(d["qty"]),
                sub_total=Decimal(str(d["sub_total"])),
                purchase_invoice_detail_id=d.get("purchase_invoice_detail_id"),
                purchase_order_detail_id=d.get("purchase_order_detail_id"),
            )
            db.add(detail)

        # Auto-post jurnal (D: Utang Dagang, K: Retur Pembelian)
        # Guard: skip jika supplier belum punya akun hutang (Phase 3 akan ganti mekanisme COA)
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(retur)
        if purchase_invoice_id is not None:
            retur.purchase_invoice_id = purchase_invoice_id
            from app.services.settlement_service import validate_return
            validate_return(db, retur)
        if auto_post_jurnal:
            post_purchase_retur(db, retur, created_by)

        if purchase_invoice_id is not None:
            retur.purchase_invoice_id = purchase_invoice_id
        db.commit()
        db.refresh(retur)
        logger.info(f"PurchaseRetur created: {no_retur} | grand_total={grand_total}")
        return retur

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PurchaseRetur: {e}")
        raise


@atomic_accounting_write
def update_purchase_retur(
    db: Session,
    db_obj: PurchaseRetur,
    tanggal: Optional[datetime] = None,
    purchase_order_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    alamat: Optional[str] = None,
    ppn: Optional[Decimal] = None,
    keterangan: Optional[str] = None,
    auto_post_jurnal: Optional[bool] = None,
    purchase_invoice_id=None,
    gudang_id: Optional[UUID] = None,
) -> PurchaseRetur:
    """Update data purchase retur (hanya field header)."""
    require_unposted(db_obj)
    if gudang_id is not None:
        db_obj.gudang_id = gudang_id
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Purchase Retur dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if purchase_order_id is not None:
        db_obj.purchase_order_id = purchase_order_id
    if supplier_id is not None:
        db_obj.supplier_id = supplier_id
    if alamat is not None:
        db_obj.alamat = alamat
    if ppn is not None:
        db_obj.ppn = ppn
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if auto_post_jurnal is not None:
        db_obj.auto_post_jurnal = auto_post_jurnal

    db.add(db_obj)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)
    if purchase_invoice_id is not None:
        db_obj.purchase_invoice_id = purchase_invoice_id
    if db_obj.purchase_invoice_id:
        from app.services.settlement_service import validate_return
        validate_return(db, db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_purchase_retur(db: Session, db_obj: PurchaseRetur, user_id: Optional[UUID] = None) -> PurchaseRetur:
    """Batalkan purchase retur."""
    require_no_stock_movement(db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Retur sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseRetur cancelled: {db_obj.no_retur}")
    return db_obj


# ==========================================
# PENERIMAAN BARANG
# ==========================================

def get_penerimaan_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[str] = None,
    supplier_id: Optional[UUID] = None,
    tanggal_from: Optional[date] = None,
    tanggal_to: Optional[date] = None,
) -> Tuple[List[PenerimaanBarang], int]:
    """Ambil daftar penerimaan barang dengan filter & pagination."""
    query = db.query(PenerimaanBarang).options(
        joinedload(PenerimaanBarang.purchase_order),
        joinedload(PenerimaanBarang.supplier),
        joinedload(PenerimaanBarang.creator),
        joinedload(PenerimaanBarang.details).joinedload(PenerimaanBarangDetail.barang),
        joinedload(PenerimaanBarang.details).joinedload(PenerimaanBarangDetail.satuan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            PenerimaanBarang.no_form.ilike(pattern)
            | PenerimaanBarang.keterangan.ilike(pattern)
        )
    if status:
        query = query.filter(PenerimaanBarang.status == status)
    if supplier_id:
        query = query.filter(PenerimaanBarang.supplier_id == supplier_id)
    if tanggal_from:
        query = query.filter(PenerimaanBarang.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(PenerimaanBarang.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(PenerimaanBarang.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_penerimaan_by_id(db: Session, pb_id: UUID) -> Optional[PenerimaanBarang]:
    """Ambil 1 penerimaan barang berdasarkan ID dengan detail."""
    return (
        db.query(PenerimaanBarang)
        .options(
            joinedload(PenerimaanBarang.purchase_order),
            joinedload(PenerimaanBarang.supplier),
            joinedload(PenerimaanBarang.creator),
            joinedload(PenerimaanBarang.details).joinedload(PenerimaanBarangDetail.barang),
            joinedload(PenerimaanBarang.details).joinedload(PenerimaanBarangDetail.satuan),
        )
        .filter(PenerimaanBarang.id == pb_id)
        .first()
    )


@atomic_accounting_write
def create_penerimaan(
    db: Session,
    tanggal: datetime,
    purchase_order_id: UUID,
    supplier_id: UUID,
    details_data: list,
    alamat: Optional[str] = None,
    keterangan: Optional[str] = None,
    created_by: Optional[UUID] = None,
    gudang_id: Optional[UUID] = None,
    purchase_invoice_id: Optional[UUID] = None,
) -> PenerimaanBarang:
    """Buat PenerimaanBarang baru beserta detail.

    Tahap 2: `purchase_invoice_id` opsional untuk anti double-record Persediaan.
    Jika diisi, selesaikan penerimaan sebelum invoice dipost. Sistem
    akan D: PENERIMAAN_DALAM_PROSES (clearing) alih-alih D: Pembelian.

    Tidak ada jurnal posting saat create; jurnal di-post saat `finish_penerimaan`.
    """
    try:
        # Validasi supplier
        supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise ValueError(f"Supplier dengan ID {supplier_id} tidak ditemukan")

        receipt_control.validate_link(db, purchase_invoice_id, supplier_id, tanggal)

        # Generate nomor form penerimaan
        # Update ASAHI: pola penomoran seragam "PB ASI/YYYY/NNN" (reset tahunan).
        # === Update ASAHI #019 — prefix & kode perusahaan via Preferensi ===
        # (doc_type PENERIMAAN_BARANG; fallback "PB" — perilaku lama bila belum diubah).
        no_form = get_nomor_dokumen_tahunan(
            db, PenerimaanBarang, prefix="PB",
            no_column="no_form", tanggal=tanggal.date(),
            doc_type="PENERIMAAN_BARANG",
        )

        # Buat header
        pb = PenerimaanBarang(
            gudang_id=gudang_id,
            no_form=no_form,
            tanggal=tanggal,
            purchase_order_id=purchase_order_id,
            supplier_id=supplier_id,
            alamat=alamat,
            keterangan=keterangan,
            purchase_invoice_id=purchase_invoice_id,
            status=StatusPenjualan.DIPROSES,
            created_by=created_by,
        )
        db.add(pb)
        db.flush()

        # Buat detail
        for d in details_data:
            detail = PenerimaanBarangDetail(
                penerimaan_barang_id=pb.id,
                harga_perolehan=d.get("harga_perolehan"),
                tanggal_kedaluwarsa=d.get("tanggal_kedaluwarsa"),
                barang_id=d["barang_id"],
                qty=int(d["qty"]),
                satuan_id=d["satuan_id"],
                # === Phase 5 — source-line trace (Roadmap §19) ===
                purchase_order_detail_id=d.get("purchase_order_detail_id"),
            )
            db.add(detail)

        db.commit()
        db.refresh(pb)
        logger.info(f"PenerimaanBarang created: {no_form}")
        return pb

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating PenerimaanBarang: {e}")
        raise


@atomic_accounting_write
def update_penerimaan(
    db: Session,
    db_obj: PenerimaanBarang,
    tanggal: Optional[datetime] = None,
    purchase_order_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    alamat: Optional[str] = None,
    keterangan: Optional[str] = None,
    gudang_id: Optional[UUID] = None,
    purchase_invoice_id=_RECEIPT_LINK_UNSET,
) -> PenerimaanBarang:
    """Update data penerimaan barang (hanya field header).

    Tahap 2: `purchase_invoice_id` bisa diisi untuk link ke invoice (clearing).
    Kirim `None` eksplisit untuk mengosongkan link.

    === Update #051 — penerimaan FINAL (SELESAI / sudah berjurnal GRNI) tetap
    boleh diubah TERBATAS: link invoice (purchase_invoice_id) + keterangan +
    alamat. Ketiganya murni relasi/informasi — tidak menyentuh stok, jurnal,
    maupun periode — sehingga aman diubah kapan pun setelah dokumen final.
    Sebelumnya SEMUA perubahan ditolak dengan "Dokumen sudah diposting.
    Batalkan dengan jurnal pembalik sebelum membuat koreksi." — pesan yang
    menyesatkan: mengubah link invoice BUKAN koreksi jurnal. ===
    """
    posted = bool(getattr(db_obj, "jurnal_umum_id", None)) or db_obj.status == StatusPenjualan.SELESAI
    if posted:
        if db_obj.status == StatusPenjualan.DIBATALKAN:
            raise ValueError("Penerimaan yang sudah dibatalkan tidak bisa diubah.")
        # Field terlindungi: nilai yang SAMA diabaikan (form edit mengirim body
        # utuh dengan nilai readOnly), PERUBAHAN nilai ditolak dengan pesan jelas.
        if tanggal is not None and (db_obj.tanggal is None or tanggal.date() != db_obj.tanggal.date()):
            raise ValueError("Penerimaan sudah final (stok masuk & jurnal tercatat) — tanggal tidak bisa diubah.")
        if purchase_order_id is not None and purchase_order_id != db_obj.purchase_order_id:
            raise ValueError("Penerimaan sudah final (stok masuk & jurnal tercatat) — PO sumber tidak bisa diubah.")
        if supplier_id is not None and supplier_id != db_obj.supplier_id:
            raise ValueError("Penerimaan sudah final (stok masuk & jurnal tercatat) — supplier tidak bisa diubah.")
        if gudang_id is not None and gudang_id != db_obj.gudang_id:
            raise ValueError("Penerimaan sudah final (stok masuk & jurnal tercatat) — gudang tidak bisa diubah.")
        # Perubahan yang diizinkan pada dokumen final:
        if alamat is not None:
            db_obj.alamat = alamat
        if keterangan is not None:
            db_obj.keterangan = keterangan
        if purchase_invoice_id is not _RECEIPT_LINK_UNSET:
            db_obj.purchase_invoice_id = purchase_invoice_id
        receipt_control.validate_link(db, db_obj.purchase_invoice_id, db_obj.supplier_id, db_obj.tanggal)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        logger.info(f"PenerimaanBarang (final) limited update: {db_obj.no_form}")
        return db_obj

    require_unposted(db_obj)
    if gudang_id is not None:
        db_obj.gudang_id = gudang_id
    if db_obj.status in (StatusPenjualan.SELESAI, StatusPenjualan.DIBATALKAN):
        raise ValueError(f"Penerimaan Barang dengan status {db_obj.status.value} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if purchase_order_id is not None:
        db_obj.purchase_order_id = purchase_order_id
    if supplier_id is not None:
        db_obj.supplier_id = supplier_id
    if alamat is not None:
        db_obj.alamat = alamat
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if purchase_invoice_id is not _RECEIPT_LINK_UNSET:
        db_obj.purchase_invoice_id = purchase_invoice_id
    receipt_control.validate_link(db, db_obj.purchase_invoice_id, db_obj.supplier_id, db_obj.tanggal)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def cancel_penerimaan(db: Session, db_obj: PenerimaanBarang, user_id: Optional[UUID] = None) -> PenerimaanBarang:
    """Batalkan penerimaan barang."""
    require_no_stock_movement(db_obj)
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Penerimaan Barang sudah dibatalkan")
    if getattr(db_obj, "jurnal_umum_id", None):
        reverse_journal(db, db_obj.jurnal_umum_id, user_id or db_obj.created_by)
    db_obj.status = StatusPenjualan.DIBATALKAN
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenerimaanBarang cancelled: {db_obj.no_form}")
    return db_obj


@atomic_accounting_write
def finish_penerimaan(db: Session, db_obj: PenerimaanBarang) -> PenerimaanBarang:
    """Finalisasi penerimaan barang — status SELESAI + tambah stok barang.

    === Update #069 — PENERIMAAN TIDAK LAGI MEMPOST JURNAL GL. ===
    Sebelum #069 penerimaan mempost jurnal D Persediaan / K Pembelian (HPP)
    yang otomatis DI-NET-OUT oleh debit akun Pembelian di jurnal invoice
    (clearing #066) — akibatnya baris HPP di Laba Rugi selalu NOL setelah
    pembelian (temuan test user #068). Model akuntansi kini PERIODIK:

      - Penerimaan barang = operasi GUDANG saja (StokMutasi + snapshot akun).
      - Invoice Pembelian = satu-satunya pencatatan GL pembelian:
        D Pembelian (HPP) / K Utang — biaya pembelian LANGSUNG tampak di
        Jurnal Umum & baris HPP Laba Rugi.
      - Setting GRNI (PENERIMAAN_DALAM_PROSES) tetap WAJIB sebagai gate
        alur tiga-arah (penerimaan wajib selesai sebelum invoice dipost).

    Penerimaan LAMA yang sudah berjurnal (pra-#069) tetap dipertahankan
    historisnya; jurnalnya tidak pernah di-rewrite.
    """
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Penerimaan yang sudah dibatalkan tidak bisa difinalisasi")
    if db_obj.status == StatusPenjualan.SELESAI:
        raise ValueError("Penerimaan sudah selesai")

    # Phase C: GRNI sekarang MANDATORY (bukan optional)
    from app.services.persediaan_service import (
        _get_akun_persediaan_id,
        _get_akun_penerimaan_dalam_proses_id,
    )
    grni_account_id = _get_akun_penerimaan_dalam_proses_id(db)
    if not grni_account_id:
        raise ValueError(
            'Akun GRNI (PENERIMAAN_DALAM_PROSES) belum di-configure. '
            'Goods Receipt wajib menggunakan GRNI architecture. '
            'Configure via PUT /master/setting-akun/PENERIMAAN_DALAM_PROSES.'
        )
    receipt_control.validate_execution(db, db_obj, grni_account_id)
    # === Update #069 — TIDAK ADA lagi posting jurnal di penerimaan.
    #     Akun Pembelian (HPP) cukup di-ensure ter-config karena akun tsb
    #     dipakai sebagai snapshot "akun lawan" di StokMutasi (dipakai
    #     retur/rekonsiliasi) dan sebagai akun debit jurnal invoice nanti. ===
    pembelian_account_id = get_akun_id_or_raise(
        db, KEY_PEMBELIAN, context=f"Penerimaan {db_obj.no_form}"
    )

    # Tambah stok untuk setiap detail barang yang diterima
    for detail in db_obj.details:
        cost = detail.harga_perolehan
        if cost is None:
            lines = [r for r in db_obj.purchase_order.details if r.barang_id == detail.barang_id]
            if len(lines) != 1:
                raise ValueError('Isi hargaPerolehan; harga dari PO tidak dapat ditentukan secara unik')
            cost = lines[0].harga * (Decimal(100) - lines[0].diskon) / Decimal(100)
        if cost < 0:
            raise ValueError('Harga perolehan tidak boleh negatif')
        detail.harga_perolehan = cost
        movement = update_stok_barang(
            db=db,
            barang_id=detail.barang_id,
            qty_change=detail.qty,
            mode="TAMBAH",
            deskripsi=f"Penerimaan Barang {db_obj.no_form}",
            ref_module=RefModule.PURCHASE_RECEIPT,
            ref_no=db_obj.no_form,
            ref_id=db_obj.id,
            gudang_id=db_obj.gudang_id,
            harga_satuan=cost,
            tanggal=db_obj.tanggal,
            tanggal_kedaluwarsa=detail.tanggal_kedaluwarsa,
        )

        # Snapshot akun di StokMutasi (dipakai retur stok / rekonsiliasi)
        if grni_account_id:
            movement['mutasi'].inventory_account_id = _get_akun_persediaan_id(db, detail.barang)
            movement['mutasi'].expense_account_id = pembelian_account_id

    # === Update #069 — jurnal GL penerimaan DIHAPUS (lihat docstring):
    #     biaya pembelian dibukukan SEKALI di jurnal invoice (D Pembelian /
    #     K Utang) supaya baris HPP Laba Rugi tidak pernah ter-net-out. ===

    db_obj.status = StatusPenjualan.SELESAI
    db.add(db_obj)
    # === Update #052 — PO sumber ikut disinkronkan saat penerimaan selesai
    # (bila invoice terhubung sudah ada & lunas → PO bisa naik SELESAI).
    # Tanpa commit — commit tetap milik alur ini di bawah. ===
    if db_obj.purchase_order_id is not None:
        try:
            sync_po_completion(db, {db_obj.purchase_order_id})
        except Exception as sync_exc:  # noqa: BLE001 — jangan gagalkan finalisasi
            logger.warning(
                f"Sync status PO pasca-selesai penerimaan {db_obj.no_form} dilewati: {sync_exc}"
            )
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PenerimaanBarang finished: {db_obj.no_form} | stok ditambahkan")
    return db_obj


@atomic_accounting_write
def finish_purchase_retur(db: Session, db_obj: PurchaseRetur) -> PurchaseRetur:
    """Finalisasi purchase retur — status SELESAI + kurangi stok barang."""
    if db_obj.status == StatusPenjualan.DIBATALKAN:
        raise ValueError("Purchase Retur yang sudah dibatalkan tidak bisa difinalisasi")
    if db_obj.status == StatusPenjualan.SELESAI:
        raise ValueError("Purchase Retur sudah selesai")

    if not db_obj.purchase_invoice_id:
        raise ValueError('Retur stok memerlukan purchaseInvoiceId')
    prior_returns = db.query(PurchaseRetur).filter_by(purchase_invoice_id=db_obj.purchase_invoice_id, status=StatusPenjualan.SELESAI).all()
    quantities = {}
    for row in db_obj.details:
        quantities[row.barang_id] = quantities.get(row.barang_id, 0) + row.qty
    for item_id, quantity in quantities.items():
        purchased = sum(row.qty for row in db_obj.purchase_invoice.details if row.barang_id == item_id)
        returned = sum(row.qty for retur in prior_returns for row in retur.details if row.barang_id == item_id)
        if quantity <= 0 or quantity + returned > purchased:
            raise ValueError('Qty retur melebihi qty invoice pembelian yang belum diretur')

    # Kurangi stok untuk setiap detail barang yang dikembalikan ke supplier
    # === Update #069 — jurnal lapis-stok RETUR_STOCK (D Retur Pembelian /
    #     K Persediaan) DIHAPUS: jurnal GL retur pembelian cukup SATU di
    #     post_purchase_retur (D Utang / K Retur Pembelian) sehingga akun
    #     Retur Pembelian (baris HPP Laba Rugi) tidak ter-net-out dan
    #     Persediaan GL tidak digerakkan oleh siklus beli-jual (model
    #     periodik). Stok tetap dikurangi di subledger (StokMutasi). ===
    for detail in db_obj.details:
        update_stok_barang(
            db=db,
            barang_id=detail.barang_id,
            qty_change=detail.qty,
            mode="KURANGI",
            deskripsi=f"Retur Pembelian {db_obj.no_retur}",
            ref_module=RefModule.PURCHASE_RETUR,
            ref_no=db_obj.no_retur,
            ref_id=db_obj.id,
            gudang_id=db_obj.gudang_id,
        )

    db_obj.status = StatusPenjualan.SELESAI
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    logger.info(f"PurchaseRetur finished: {db_obj.no_retur} | stok dikurangi")
    return db_obj

def post_purchase_invoice(db: Session, inv, created_by):
    """Post jurnal GL invoice pembelian — D Pembelian (HPP) / K Utang Dagang.

    === Update #069 — MODEL PERIODIK: ===
    - Debit akun PEMBELIAN (HPP (Pembelian)) sebesar dasar_pajak PENUH —
      biaya pembelian LANGSUNG tampak di Jurnal Umum & baris HPP Laba Rugi
      (dulu #066: debit diarahkan ke akun kredit jurnal penerimaan sehingga
      HPP selalu ter-net-out menjadi NOL).
    - Penerimaan barang (#069) tidak lagi mempost jurnal GL, jadi tidak ada
      lawan yang menetralkan debit Pembelian ini.
    - Penerapan LEGACY (pra-#069): invoice yang di-post atas penerimaan LAMA
      yang masih menyimpan jurnal D Persediaan / K Pembelian akan tetap
      di-clearing ke akun kredit jurnal penerimaan tsb (netral historis,
      perilaku identik sebelum #069). Matching + persist bridge table tetap
      berjalan untuk sinkronisasi status PO.
    - Tanpa receipt sama sekali (GRNI tidak configured): tetap direct
      D Pembelian / K Utang (legacy path).

    Existing posted journals are never rewritten.
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, inv)
    tanggal = inv.tanggal
    supplier = db.get(Supplier, inv.supplier_id)
    no_form = inv.no_form
    no_faktur = inv.no_faktur
    sub_total = inv.sub_total
    total_diskon = inv.total_diskon
    total_ppn = inv.total_ppn
    total_biaya_tambahan = inv.total_biaya_tambahan
    grand_total = inv.grand_total
    if not supplier.akun_hutang_id:
        raise ValueError("Mapping akun akun_hutang_id belum diisi; posting dibatalkan")
    dasar_pajak = sub_total - total_diskon

    pembelian_account_id = get_akun_id_or_raise(db, KEY_PEMBELIAN, context=f"PINV {no_form}")
    clearing = receipt_control.clearing_entries(db, inv, dasar_pajak)
    if clearing is None:
        # Legacy path: tidak ada GRNI & tidak ada receipts sama sekali
        clearing = {pembelian_account_id: dasar_pajak}

    # === Update #069 — keterangan per-baris: porsi yang mendarat di akun
    #     PEMBELIAN (HPP) berlabel "Pembelian", porsi legacy yang di-clearing
    #     ke akun kredit jurnal penerimaan lama berlabel "Clearing". ===
    entries = [
        JurnalEntryItem(
            akun_perkiraan_id=account_id,
            debit=value,
            keterangan=(
                f"Pembelian PINV {no_form} - {supplier.nama}"
                if account_id == pembelian_account_id
                else f"Clearing GRNI PINV {no_form}"
            ),
        )
        for account_id, value in clearing.items()
    ]

    # P0-01: Handle unmatched portion (partial three-way match).
    # Kalau total clearing < dasar_pajak, berarti ada invoice lines yang tidak
    # dapat match ke receipt manapun (misalnya: barang yang belum diterima, atau
    # supplier invoice datang sebelum receipt). Sisa tersebut di-posting sebagai
    # direct purchase debit ke akun PEMBELIAN.
    # (Sejak #069 penerimaan baru tidak berjurnal — matching otomatis
    # mengarahkan seluruh nilai ke akun PEMBELIAN, jadi porsi unmatched
    # umumnya hanya muncul untuk invoice tanpa receipt sama sekali.)
    total_clearing = sum(clearing.values(), Decimal(0))
    unmatched_portion = Decimal(dasar_pajak) - total_clearing
    if unmatched_portion > 0:
        # Cek apakah akun PEMBELIAN sudah ada di clearing (kasus: ada direct + clearing mix)
        existing_pembelian = next(
            ((akun_id, val) for akun_id, val in clearing.items()
             if akun_id == pembelian_account_id),
            None,
        )
        if existing_pembelian:
            # Sudah ada — tambahkan unmatched portion ke akun yang sama
            # (rebuild entry list dengan nilai yang di-update)
            akun_pembelian, _ = existing_pembelian
            entries = [
                JurnalEntryItem(
                    akun_perkiraan_id=akun_pembelian,
                    debit=(value + unmatched_portion) if akun_id == akun_pembelian else value,
                    keterangan=f"Clearing GRNI PINV {no_form}" if akun_id != akun_pembelian
                               else f"Pembelian PINV {no_form} - {supplier.nama} (sebagian)",
                )
                for akun_id, value in clearing.items()
            ]
        else:
            # Tambah entry baru untuk akun PEMBELIAN
            entries.append(JurnalEntryItem(
                akun_perkiraan_id=pembelian_account_id,
                debit=unmatched_portion,
                keterangan=f"Pembelian PINV {no_form} - {supplier.nama} (tanpa receipt)",
            ))

    entries.append(JurnalEntryItem(akun_perkiraan_id=supplier.akun_hutang_id,
                                  kredit=grand_total, keterangan=f"Utang PINV {no_form}"))
    if total_ppn > 0:
        entries.insert(
            1,
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_PPN_MASUKAN, context=f"PINV {no_form}"
                ),
                debit=total_ppn,
                keterangan=f"PPN Masukan PINV {no_form}",
            ),
        )

    if total_biaya_tambahan > 0:
        # Debit: Beban Angkut Pembelian — mengimbangi grand_total (Kredit
        # Utang Dagang) yang sudah termasuk biaya tambahan, supaya jurnal balance.
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_BEBAN_ANGKUT_PEMBELIAN, context=f"PINV {no_form}"
                ),
                debit=total_biaya_tambahan,
                keterangan=f"Biaya tambahan PINV {no_form}",
            )
        )

    # === Update #052 — PPh23 opsional pada Invoice Pembelian (mirror SI).
    # Grand total sudah dipotong total_pph23 (Kredit Utang lebih kecil —
    # PPh23 ditahan dari pembayaran supplier), sehingga perlu KREDIT akun
    # "Hutang PPh 23" (setting HUTANG_PPH23) agar jurnal balance:
    # D(Pembelian/GRNI + PPN + Biaya) = K(Utang + Hutang PPh23). ===
    total_pph23 = safe_decimal(getattr(inv, "total_pph23", 0))
    if total_pph23 > 0:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_HUTANG_PPH23, context=f"PINV {no_form}"
                ),
                kredit=total_pph23,
                keterangan=f"PPh23 ditahan supplier PINV {no_form}",
            )
        )

    try:
        jurnal = auto_posting_jurnal(
            db=db,
            ref_module=RefModule.PURCHASE_INVOICE,
            ref_no=no_form,
            entries=entries,
            keterangan=f"Purchase Invoice {no_form} (Faktur: {no_faktur})",
            ref_id=inv.id,
            tanggal=tanggal,
            created_by=created_by,
        )
        inv.jurnal_umum_id = jurnal.id
        inv.akun_kontrol_id = supplier.akun_hutang_id
        inv.status = StatusPenjualan.SELESAI
    except Exception as e:
        raise ValueError(f"Jurnal PINV gagal diposting: {e}")
    # === Update #052 — PO sumber ikut disinkronkan setelah invoice final
    # (bila barang sudah diterima penuh / nilai PO tertutup dan semua invoice
    # terhubung sudah lunas → PO DIPROSES naik SELESAI). Tanpa commit —
    # pemanggil (workflow) yang memiliki transaksi. ===
    try:
        sync_po_completion(db, po_ids_of_invoice(db, inv))
    except Exception as sync_exc:  # noqa: BLE001 — jangan gagalkan posting
        logger.warning(f"Sync status PO pasca-posting PINV {no_form} dilewati: {sync_exc}")
    return inv


def post_purchase_retur(db: Session, retur, created_by):
    """Post the existing document; caller owns commit/rollback and workflow checks.

    Phase E fix (Catatan Purchase Return):
    - Auto-call validate_returnable_qty_purchase per detail (prevent over-return)
    """
    from app.services.document_totals import validate_postable
    validate_postable(db, retur)
    from app.services.settlement_service import validate_return, control_account
    validate_return(db, retur)

    # === Phase E: validate returnable qty per detail ===
    from app.services.purchase_validation import validate_returnable_qty_purchase
    for detail in retur.details:
        if detail.penerimaan_barang_detail_id:
            try:
                validate_returnable_qty_purchase(
                    db, detail.penerimaan_barang_detail_id, detail.qty,
                    exclude_retur_detail_id=detail.id,
                    context=f"Purchase Retur {retur.no_retur}",
                )
            except Exception as e:
                raise ValueError(str(getattr(e, 'detail', str(e))))

    tanggal = retur.tanggal
    supplier = db.get(Supplier, retur.supplier_id)
    no_retur = retur.no_retur
    sub_total = retur.sub_total
    total_ppn = retur.total_ppn
    grand_total = retur.grand_total
    if not supplier.akun_hutang_id:
        raise ValueError("Mapping akun akun_hutang_id belum diisi; posting dibatalkan")
    entries = [
        # Debit: Utang Dagang
        JurnalEntryItem(
            akun_perkiraan_id=control_account(db, retur.purchase_invoice) if retur.purchase_invoice else supplier.akun_hutang_id,
            debit=grand_total,
            keterangan=f"Kurangi utang retur {no_retur}",
        ),
        # Kredit: Retur Pembelian
        JurnalEntryItem(
            akun_perkiraan_id=get_akun_id_or_raise(
                db, KEY_RETUR_PEMBELIAN, context=f"Retur {no_retur}"
            ),
            kredit=sub_total,
            keterangan=f"Retur Pembelian {no_retur}",
        ),
    ]
    if total_ppn > 0:
        entries.append(
            JurnalEntryItem(
                akun_perkiraan_id=get_akun_id_or_raise(
                    db, KEY_PPN_MASUKAN, context=f"Retur {no_retur}"
                ),
                kredit=total_ppn,
                keterangan=f"PPN Retur {no_retur}",
            )
        )

    try:
        jurnal = auto_posting_jurnal(
            db=db,
            ref_module=RefModule.PURCHASE_RETUR,
            ref_no=no_retur,
            entries=entries,
            keterangan=f"Purchase Retur {no_retur}",
            ref_id=retur.id,
            tanggal=tanggal,
            created_by=created_by,
        )
        retur.jurnal_umum_id = jurnal.id
    except Exception as e:
        raise ValueError(f"Jurnal Purchase Retur gagal diposting: {e}")
    return retur
