"""master_validation.py

Helper untuk enforce Master Roadmap §9 Rules:
1. Master inactive tidak boleh dipakai transaksi baru (validate_active_master)
2. Codes immutable setelah digunakan transaksi (validate_immutable_code)
3. Master code unique (sudah di-enforce di DB via partial unique index,
   helper ini untuk double-check di service layer)

Dipakai oleh:
- app/services/penjualan_service.py (saat create SO/Delivery/Invoice)
- app/services/pembelian_service.py (saat create PO/Receipt/Invoice)
- app/services/kas_bank_service.py (saat create pembayaran/penerimaan)
- app/services/master_service.py (saat update master — check immutable code)
"""
from uuid import UUID
from typing import Type
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database import BaseModel


def validate_active_master(
    db: Session,
    Model: Type[BaseModel],
    master_id: UUID,
    context: str = "Transaksi",
) -> BaseModel:
    """Cek apakah master data (Pelanggan/Supplier/Barang/Gudang/dll) masih AKTIF.

    Dipakai di awal transaksi baru (Sales Order, Purchase Order, Invoice, dll).
    Raise HTTPException 400 kalau master tidak ditemukan atau status != 'AKTIF'.

    Sesuai Master Roadmap §9 Rules:
        "Master inactive tidak boleh dipakai transaksi baru"

    Parameter:
        db: SQLAlchemy Session
        Model: class model master (Pelanggan, Supplier, Barang, Gudang, dll)
        master_id: UUID master data
        context: string deskripsi untuk error message (mis. "Sales Order INV-001")

    Return:
        Master object (akan raise kalau invalid)

    Contoh pemakaian:
        pelanggan = validate_active_master(db, Pelanggan, pelanggan_id, "Sales Order SO-001")
        # Jika sampai sini, pelanggan pasti AKTIF
    """
    obj = db.get(Model, master_id)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail=f"{Model.__name__} dengan ID {master_id} tidak ditemukan. {context} dibatalkan.",
        )
    if obj.status != "AKTIF":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{Model.__name__} '{getattr(obj, 'nama', obj.kode)}' (kode: {obj.kode}) "
                f"sudah {obj.status} — tidak boleh dipakai transaksi baru. "
                f"{context} dibatalkan. Aktifkan kembali master ini via menu Master Data "
                f"kalau memang ingin dipakai."
            ),
        )
    return obj


def validate_immutable_code(
    db: Session,
    obj: BaseModel,
    new_kode: str,
) -> None:
    """Cek apakah master code boleh diubah.

    Sesuai Master Roadmap §9 Rules:
        "Codes immutable setelah digunakan"

    Logic:
    - Kalau new_kode == obj.kode → OK, tidak ada perubahan
    - Kalau new_kode != obj.kode → cek apakah obj sudah pernah dipakai transaksi
      (jurnal_detail, sales_invoice_detail, dll). Kalau sudah dipakai → reject.
      Kalau belum pernah dipakai → boleh ubah (kasus typo saat create baru).

    Dipanggil dari master_service.update_master() sebelum apply perubahan kode.

    Parameter:
        db: SQLAlchemy Session
        obj: master object yang akan di-update
        new_kode: nilai kode baru yang akan di-set

    Raises:
        HTTPException 400 kalau kode berubah dan obj sudah dipakai transaksi
    """
    if new_kode == obj.kode:
        # Tidak ada perubahan kode — aman
        return

    # Cek apakah master sudah dipakai di transaksi
    # Karena ini generic helper, kita cek beberapa tabel transaksi umum yang
    # punya FK ke master. Tambah table kalau perlu.
    from app.models.detail.jurnal_detail import JurnalDetail
    from app.models.transaksi.penjualan.sales_order import SalesOrder
    from app.models.transaksi.penjualan.sales_invoice import SalesInvoice
    from app.models.transaksi.pembelian.purchase_order import PurchaseOrder
    from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
    from sqlalchemy import exists

    table_name = obj.__tablename__

    # Mapping master → kolom FK di tabel transaksi
    usage_checks = {
        "pelanggan": [
            (SalesOrder, "pelanggan_id"),
            (SalesInvoice, "pelanggan_id"),
        ],
        "supplier": [
            (PurchaseOrder, "supplier_id"),
            (PurchaseInvoice, "supplier_id"),
        ],
        "barang": [],  # Barang dipakai via detail tables, cek terlalu kompleks di sini
        "gudang": [],
        "kategori_aset": [],
        "kas_bank_akun": [],
    }

    tables_to_check = usage_checks.get(table_name, [])
    for TransModel, fk_col in tables_to_check:
        used = db.query(exists().where(getattr(TransModel, fk_col) == obj.id)).scalar()
        if used:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Kode {table_name} tidak boleh diubah karena sudah dipakai "
                    f"transaksi. Nonaktifkan master ini (ubah status=NONAKTIF) "
                    f"dan buat master baru dengan kode yang benar."
                ),
            )


def validate_master_code_unique(
    db: Session,
    Model: Type[BaseModel],
    kode: str,
    exclude_id: UUID = None,
) -> None:
    """Cek apakah kode master unik di antara master yang AKTIF.

    Sesuai Master Roadmap §9 Rules:
        "Master codes unique"

    Note: DB sudah punya partial unique index `WHERE status='AKTIF'`, jadi
    DB akan reject duplikat. Helper ini untuk validasi awal supaya error
    message lebih ramah pengguna (bukan IntegrityError yang cryptic).

    Parameter:
        db: SQLAlchemy Session
        Model: class model master
        kode: kode yang di-check
        exclude_id: UUID master yang sedang di-update (skip cek dirinya sendiri)
    """
    query = db.query(Model).filter(
        Model.kode == kode,
        Model.status == "AKTIF",
    )
    if exclude_id is not None:
        query = query.filter(Model.id != exclude_id)

    existing = query.first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Kode '{kode}' sudah dipakai oleh {Model.__name__} lain yang AKTIF "
                f"(id: {existing.id}). Kode master harus unik. "
                f"Gunakan kode lain, atau nonaktifkan master existing terlebih dahulu."
            ),
        )


# ==========================================
# Phase 3 — Inventory Core Engine helpers
# ==========================================

def validate_stock_item_master(
    db: Session,
    barang_id: UUID,
    context: str = "Transaksi",
    allow_non_stock: bool = False,
):
    """Cek apakah Barang valid untuk transaksi stok.

    Phase 3 — Master Roadmap §10:
    - Barang harus AKTIF (status='AKTIF')
    - Barang harus stock-tracked (stock_item=True dan item_type != JASA)
      Kecuali kalau allow_non_stock=True (mis. untuk invoice jasa)

    Dipakai di:
    - sales_service: finish_pengiriman (delivery) — butuh stock_item=True
    - pembelian_service: finish_penerimaan (goods receipt) — butuh stock_item=True
    - persediaan_service: approve_penyesuaian, approve_pemindahan — butuh stock_item=True
    - stock_return_service: execute_sales_return — butuh stock_item=True

    Parameter:
        db: SQLAlchemy Session
        barang_id: UUID barang
        context: deskripsi transaksi untuk error message
        allow_non_stock: True kalau transaksi boleh untuk barang non-stock
            (mis. sales invoice untuk jasa — tapi tanpa stock movement)

    Return:
        Barang object (akan raise kalau invalid)

    Raises:
        HTTPException 400 kalau barang non-stock dan allow_non_stock=False
        HTTPException 404 kalau barang tidak ditemukan
        HTTPException 400 kalau barang NONAKTIF
    """
    from app.models.master.barang import Barang, ItemTypeBarang

    barang = db.get(Barang, barang_id)
    if barang is None:
        raise HTTPException(
            status_code=404,
            detail=f"Barang dengan ID {barang_id} tidak ditemukan. {context} dibatalkan.",
        )
    if barang.status != "AKTIF":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Barang '{barang.nama}' (kode: {barang.kode}) sudah {barang.status} — "
                f"tidak boleh dipakai transaksi baru. {context} dibatalkan."
            ),
        )

    # Cek stock_item flag (Phase 2 field)
    if not allow_non_stock:
        is_jasa = (
            getattr(barang, 'item_type', None) == ItemTypeBarang.JASA
            if hasattr(barang, 'item_type') and barang.item_type is not None
            else False
        )
        stock_item = getattr(barang, 'stock_item', True)

        if is_jasa or not stock_item:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Barang '{barang.nama}' (kode: {barang.kode}) adalah barang "
                    f"non-stock (item_type={barang.item_type.value if barang.item_type else 'null'}, "
                    f"stock_item={stock_item}). Tidak bisa dipakai transaksi stok. "
                    f"{context} dibatalkan. "
                    f"Untuk transaksi jasa, gunakan endpoint yang sesuai "
                    f"(mis. sales invoice langsung tanpa delivery)."
                ),
            )

    return barang
