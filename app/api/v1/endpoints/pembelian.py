"""
Pembelian Endpoints.
PurchaseOrder, PurchaseInvoice, PurchaseRetur, PenerimaanBarang, LPPB.
"""

from datetime import date, datetime
from typing import Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.deps import get_current_db, get_current_user
from app.models.master.pengguna import Pengguna
from app.models.transaksi.penjualan.sales_order import StatusPenjualan
# === Update #063 — model untuk endpoint "tersedia" (dokumen sumber yang
# masih punya sisa — mirror /penjualan/sales-order/tersedia #047). ===
from app.models.transaksi.pembelian.purchase_order import PurchaseOrder
from app.models.detail.purchase_order_detail import PurchaseOrderDetail
from app.models.transaksi.pembelian.penerimaan_barang import PenerimaanBarang
from app.models.detail.penerimaan_barang_detail import PenerimaanBarangDetail
from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
from app.models.detail.purchase_invoice_detail import PurchaseInvoiceDetail
from app.models.transaksi.pembelian.purchase_retur import PurchaseRetur
from app.models.detail.purchase_retur_detail import PurchaseReturDetail
# === Update #064 — LPPB & Supplier untuk mode untuk=lppb (PO yang belum
# dipakai LPPB mana pun) + supplierNama di response tersedia. ===
from app.models.transaksi.pembelian.lppb import Lppb
from app.models.master.supplier import Supplier
from app.schemas.base import PaginatedResponse
from app.schemas.pembelian import (
    PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrderResponse,
    PurchaseInvoiceCreate, PurchaseInvoiceUpdate, PurchaseInvoiceResponse,
    PurchaseReturCreate, PurchaseReturUpdate, PurchaseReturResponse,
    PenerimaanBarangCreate, PenerimaanBarangUpdate, PenerimaanBarangResponse,
    PurchaseOrderSisaResponse,
    LppbCreate, LppbUpdate, LppbResponse, LppbListResponse,
    # === Update #037 — Detail ROP (kolam belanja PIC Pembelian) ===
    RopItemBelanjaResponse, RopItemPoDraftCreate, RopPoDraftResponse,
    # === Update #043 — Part Stok Menipis (restock PO draft) ===
    PartStokMenipisItemResponse, PartStokMenipisPoDraftCreate,
    PartStokMenipisPoDraftResponse,
)
from app.services import pembelian_service as svc
from app.services import lppb_service as lppb_svc
from app.services import workflow_service
from app.services.cancel_document_service import cancel_document
from app.services.purchase_validation import (
    get_qty_received_so_far,
    get_qty_invoiced_for_po_detail,
)
from app.schemas.workflow import HardDeleteRequest


def _service_kwargs(fn, data: dict) -> dict:
    """Saring field payload agar hanya parameter yang diterima service yang diteruskan.

    Mencegah HTTP 500 (TypeError) ketika schema menerima field yang tidak
    didukung signature service (mis. details/syarat_bayar_id/currency pada PUT).
    Field tak didukung diabaikan secara diam-diam (update header-only).
    """
    import inspect
    params = inspect.signature(fn).parameters
    return {k: v for k, v in data.items() if k in params}

router = APIRouter()


# ==========================================
# PURCHASE ORDER
# ==========================================

@router.get("/purchase-order", response_model=PaginatedResponse[PurchaseOrderResponse])
def get_purchase_order_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no pesanan, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Purchase Order dengan filter dan pagination."""
    data, total = svc.get_purchase_order_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, supplier_id=supplier_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/purchase-order", response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED)
def create_purchase_order(
    data_in: PurchaseOrderCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Purchase Order baru."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        biaya_data = [b.model_dump() for b in data_in.biaya_tambahan]
        obj = svc.create_purchase_order(
            db=db,
            tanggal=data_in.tanggal,
            supplier_id=data_in.supplier_id,
            details_data=details_data,
            biaya_data=biaya_data,
            tanggal_kirim=data_in.tanggal_kirim,
            alamat=data_in.alamat,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            # === Update ASAHI — pilihan PPh23/PPN saat input PO ===
            ppn_applicable=data_in.ppn_applicable,
            pph23_applicable=data_in.pph23_applicable,
            pph23=data_in.pph23,
            keterangan=data_in.keterangan,
            auto_post_jurnal=False,
            created_by=current_user.id,
            syarat_bayar_id=data_in.syarat_bayar_id,
            currency=data_in.currency,
            # === Update ASAHI #3 — alamat pengiriman (wajib) + PPIC ===
            alamat_pengiriman_id=data_in.alamat_pengiriman_id,
            ppic=data_in.ppic,
            # === Update ASAHI #002 — PIC Gudang (snapshot; None → dari master) ===
            pic_gudang=data_in.pic_gudang,
            pic_gudang_telepon=data_in.pic_gudang_telepon,
            # === Update #067 — PIC Supplier & No. HP (field aktif di form PO;
            # None → backend snapshot dari Kontak Person/Telepon master). ===
            pic_supplier=data_in.pic_supplier,
            pic_supplier_telepon=data_in.pic_supplier_telepon,
        )
        # Update #043 — langsung final tanpa langkah approval untuk SEMUA user
        # (workflow maker-checker di modul penjualan/pembelian di-takedown).
        workflow_service.direct_complete(db, current_user, 'purchase_order', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/purchase-order/tersedia")
def get_purchase_order_tersedia(
    untuk: str = Query("penerimaan", description="Tujuan tarik data: 'penerimaan' (sisa qty terima), 'invoice' (sisa qty faktur), atau 'lppb' (belum dipakai LPPB mana pun)"),
    search: Optional[str] = Query(None, description="Cari no pesanan"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """=== Update #063 — PO yang MASIH PUNYA SISA untuk narik data. ===

    Mirror /penjualan/sales-order/tersedia (#047) untuk modul Pembelian
    (permintaan user: "yang sudah terpakai sebelumnya jangan ditampilkan
    lagi" — diterapkan ke semua modul yang alurnya menarik data):

    - untuk=penerimaan : PO non-DIBATALKAN yang masih ada baris dengan
      sisa terima = qty − Σ qty penerimaan DIPROSES/SELESAI > 0
      (paritas get_qty_received_so_far).
    - untuk=invoice    : PO non-DIBATALKAN yang masih ada baris dengan
      sisa faktur = qty − Σ qty invoice DIPROSES/SELESAI (lewat dua jalur
      link: purchase_order_detail_id langsung ATAU penerimaan barang
      detail baris PO tersebut — baris dengan kedua link dihitung SEKALI
      via COALESCE(pod langsung, pod dari penerimaan))
      (paritas get_qty_invoiced_for_po_detail).
    - untuk=lppb       : PO non-DIBATALKAN yang belum pernah dipakai LPPB
      mana pun (Update #064 — LPPB = laporan pemeriksaan per PO, satu PO
      cukup satu LPPB; cancel LPPB = hard delete sehingga LPPB yang
      dihapus tidak memblokir PO). Dipakai dropdown "Nomor PO" di form
      LPPB (create & edit) — sebelumnya semua PO tampil termasuk yang
      sudah ada LPPB-nya.

    Response ringan {id, noPesanan, supplierId, supplierNama, sisaNilai} —
    tanpa detail baris. supplierNama ditambahkan di Update #064 (dipakai form
    LPPB untuk field Supplier read-only "otomatis dari PO"; field ini
    opsional bagi konsumen lama). sisaNilai ditambahkan di Update #065
    (hanya mode invoice — estimasi nilai belum terfaktur; mode lain null).
    Form tetap menarik sisa per baris lewat /purchase-order/{id}/sisa.

    === Update #068 — PO DRAFT tidak ditawarkan lagi (semua mode). PO hasil
    "Buat PO Draft" dari Detail ROP / Stok Menipis memang sengaja berhenti di
    DRAFT sampai diedit & disimpan (keputusan #043) — selama belum final,
    datanya belum layak ditarik ke Penerimaan/Invoice/LPPB. PO yang sedang
    dipakai form edit tetap tampil lewat pola re-insert existing di FE. ===
    """
    base = db.query(
        PurchaseOrder.id,
        PurchaseOrder.no_pesanan,
        PurchaseOrder.supplier_id,
        Supplier.nama.label("supplier_nama"),
    ).outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id).filter(
        PurchaseOrder.status.notin_([StatusPenjualan.DIBATALKAN, StatusPenjualan.DRAFT])
    )
    if search:
        base = base.filter(PurchaseOrder.no_pesanan.ilike(f"%{search}%"))

    if untuk == "invoice":
        # Jalur link invoice detail → PO detail: langsung ATAU via penerimaan.
        # COALESCE(memilih link langsung bila ada) menjaga baris dengan kedua
        # link tetap dihitung sekali untuk PO detail tujuannya.
        pbd_pod_sq = (
            db.query(
                PenerimaanBarangDetail.id.label("pbd_id"),
                PenerimaanBarangDetail.purchase_order_detail_id.label("pod_id"),
            )
            .filter(PenerimaanBarangDetail.purchase_order_detail_id.isnot(None))
            .subquery()
        )
        terfaktur_sq = (
            db.query(
                func.coalesce(
                    PurchaseInvoiceDetail.purchase_order_detail_id,
                    pbd_pod_sq.c.pod_id,
                ).label("pod_id"),
                func.coalesce(func.sum(PurchaseInvoiceDetail.qty), 0).label("terfaktur"),
            )
            .join(PurchaseInvoice, PurchaseInvoice.id == PurchaseInvoiceDetail.purchase_invoice_id)
            .outerjoin(pbd_pod_sq, pbd_pod_sq.c.pbd_id == PurchaseInvoiceDetail.penerimaan_barang_detail_id)
            .filter(PurchaseInvoice.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]))
            .group_by(
                func.coalesce(
                    PurchaseInvoiceDetail.purchase_order_detail_id,
                    pbd_pod_sq.c.pod_id,
                )
            )
            .subquery()
        )
        sisa_exists = (
            db.query(PurchaseOrderDetail.id)
            .outerjoin(terfaktur_sq, terfaktur_sq.c.pod_id == PurchaseOrderDetail.id)
            .filter(
                PurchaseOrderDetail.purchase_order_id == PurchaseOrder.id,
                PurchaseOrderDetail.qty - func.coalesce(terfaktur_sq.c.terfaktur, 0) > 0,
            )
            .exists()
        )
    elif untuk == "lppb":
        # === Update #064 — PO belum dipakai LPPB mana pun. Guard status
        # DIBATALKAN hanya untuk keamanan (cancel LPPB saat ini hard delete);
        # LPPB DRAFT pun sudah dihitung "dipakai" supaya tidak dobel LPPB. ===
        lppb_exists = (
            db.query(Lppb.id)
            .filter(
                Lppb.purchase_order_id == PurchaseOrder.id,
                Lppb.status != StatusPenjualan.DIBATALKAN,
            )
            .exists()
        )
        sisa_exists = ~lppb_exists
    else:
        # penerimaan — masih ada baris yang belum diterima penuh.
        diterima_sq = (
            db.query(
                PenerimaanBarangDetail.purchase_order_detail_id.label("pod_id"),
                func.coalesce(func.sum(PenerimaanBarangDetail.qty), 0).label("diterima"),
            )
            .join(PenerimaanBarang, PenerimaanBarang.id == PenerimaanBarangDetail.penerimaan_barang_id)
            .filter(PenerimaanBarang.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]))
            .group_by(PenerimaanBarangDetail.purchase_order_detail_id)
            .subquery()
        )
        sisa_exists = (
            db.query(PurchaseOrderDetail.id)
            .outerjoin(diterima_sq, diterima_sq.c.pod_id == PurchaseOrderDetail.id)
            .filter(
                PurchaseOrderDetail.purchase_order_id == PurchaseOrder.id,
                PurchaseOrderDetail.qty - func.coalesce(diterima_sq.c.diterima, 0) > 0,
            )
            .exists()
        )

    rows = (
        base.filter(sisa_exists)
        .order_by(PurchaseOrder.created_at.desc())
        .limit(limit)
        .all()
    )

    # === Update #065 — sisa NILAI tagihan per PO (hanya untuk=invoice):
    # estimasi nilai yang masih bisa difakturkan = Σ per baris PO
    # (qty − terfaktur, min 0) × harga × (1 − diskon%). Dipakai dropdown PO
    # di form Invoice Pembelian untuk label "sisa Rp …" — memperjelas bahwa
    # PO boleh difakturkan BERKALI-KALI (potong sebagian qty per faktur):
    # PO tetap tampil selama masih ada baris belum terfaktur penuh, dan
    # hilang setelah seluruh qty terfaktur (visibility tetap berbasis sisa
    # qty #063 — tidak berubah; sisaNilai murni informasi). Mode lain → null. ===
    sisa_nilai_map: dict = {}
    if untuk == "invoice" and rows:
        po_ids = [r.id for r in rows]
        sisa_rows = (
            db.query(
                PurchaseOrderDetail.purchase_order_id.label("po_id"),
                func.sum(
                    func.greatest(PurchaseOrderDetail.qty - func.coalesce(terfaktur_sq.c.terfaktur, 0), 0)
                    * PurchaseOrderDetail.harga
                    * (1 - func.coalesce(PurchaseOrderDetail.diskon, 0) / 100)
                ).label("sisa_nilai"),
            )
            .outerjoin(terfaktur_sq, terfaktur_sq.c.pod_id == PurchaseOrderDetail.id)
            .filter(PurchaseOrderDetail.purchase_order_id.in_(po_ids))
            .group_by(PurchaseOrderDetail.purchase_order_id)
            .all()
        )
        sisa_nilai_map = {str(r.po_id): float(r.sisa_nilai or 0) for r in sisa_rows}

    return [
        {
            "id": str(r.id),
            "noPesanan": r.no_pesanan,
            "supplierId": str(r.supplier_id),
            "supplierNama": r.supplier_nama,
            # === Update #065 — sisa nilai (null di luar mode invoice). ===
            "sisaNilai": sisa_nilai_map.get(str(r.id)) if untuk == "invoice" else None,
        }
        for r in rows
    ]


@router.get("/purchase-order/{po_id}", response_model=PurchaseOrderResponse)
def get_purchase_order_detail(
    po_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Purchase Order."""
    item = svc.get_purchase_order_by_id(db, po_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Order tidak ditemukan")
    return item


# ==========================================
# === Update #037 — DETAIL ROP (kolam belanja PIC Pembelian) ===
# Nama fungsi memuat 'rop_item' → prefix RBAC purchase.purchase_order
# (view untuk GET, create untuk POST) — tidak ada kode permission baru.
# ==========================================

@router.get("/rop-item", response_model=list[RopItemBelanjaResponse])
def get_rop_item_belanja_list(
    search: Optional[str] = Query(None, description="Cari ID PART / description / no ROP / project"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier (kolom SUPPLIER di ROP)"),
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="Filter status belanja: BELUM / SEBAGIAN / LENGKAP / STOK_CUKUP",
    ),
    need_purchase: bool = Query(False, description="True = hanya baris dengan sisa kekurangan > 0"),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar seluruh item ROP berstatus DIBUAT — menu Pembelian → Detail ROP.

    Satu baris = satu item ROP (mis. 4 ROP × 10 item = 40 baris). Per baris
    disertakan: qty diminta, stok gudang tersedia (master barang; tanpa link
    master = 0), kekurangan (qty − stok), sudah dibelanjakan (SUM qty PO
    aktif yang menaut baris — PO DIBATALKAN tidak dihitung), sisa, status,
    supplier, serta nomor PO DRAFT yang sedang menaut (bila ada).
    """
    return svc.get_rop_item_belanja_list(
        db,
        search=search,
        supplier_id=supplier_id,
        status_filter=status_filter,
        need_purchase=need_purchase,
        limit=limit,
    )


@router.post("/rop-item/po-draft", response_model=RopPoDraftResponse, status_code=status.HTTP_201_CREATED)
def create_rop_item_po_draft(
    data_in: RopItemPoDraftCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat PO DRAFT dari seleksi item ROP (alur Detail ROP).

    - Item boleh dari beberapa ROP sekaligus → satu PO (item digelondongkan
      ke supplier yang dipilih).
    - Qty per baris otomatis = sisa kekurangan (qty diminta − stok tersedia −
      yang sudah masuk PO aktif lain).
    - Baris menyimpan jejak ROP + SO asal; jenis belanja default PROJECT
      (editabel per baris di PO draft).
    - Part yang belum ada di master barang → barang master dibuat OTOMATIS.
    - PO disimpan berstatus DRAFT (TIDAK auto-final meski dibuat Administrator)
      supaya bisa dikoreksi dulu di menu Pesanan — finalisasi lewat alur biasa.
    """
    try:
        res = svc.create_po_draft_from_rop_items(
            db,
            item_ids=data_in.item_ids,
            supplier_id=data_in.supplier_id,
            alamat_pengiriman_id=data_in.alamat_pengiriman_id,
            tanggal=data_in.tanggal,
            created_by=current_user.id,
            keterangan=data_in.keterangan,
        )
        # Fix #042: hasil service = tuple (po, info); replay Idempotency-Key
        # (retry request gagal kirim) hanya mengembalikan model PO utama —
        # unpack toleran supaya replay tetap valid tanpa 500.
        if isinstance(res, tuple):
            po, info = res
        else:
            po, info = res, {}
        return {
            "purchase_order": po,
            "skipped": info.get("skipped", []),
            "auto_created_barang": info.get("auto_created_barang", []),
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ==========================================
# === Update #043 — PART STOK MENIPIS (restock) ===
# Nama fungsi memuat 'part_stok_menipis' → prefix RBAC purchase.purchase_order
# (view untuk GET, create untuk POST) — mirror pemetaan ROP #037 di
# access_registry._ROUTER_PREFIXES, tanpa kode permission baru.
# ==========================================

@router.get("/part-stok-menipis", response_model=list[PartStokMenipisItemResponse])
def get_part_stok_menipis_list(
    need_purchase: bool = Query(False, description="True = hanya part dengan sisa kekurangan > 0"),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Daftar part master dengan stok di bawah minimum — menu Pembelian →
    Part Stok Menipis (restock, Update #043).

    Satu baris = satu barang master (AKTIF, stok_minimum > 0, stok <
    stok_minimum). Per baris disertakan: stok, stok minimum, kekurangan,
    sudah dibeli (Σ qty PO DRAFT/DIPROSES yang menaut barang — belum
    diterima), sisa, status BELUM/SEBAGIAN/LENGKAP/STOK_CUKUP, serta nomor
    PO aktif penyumbang (draftPoNos).
    """
    return svc.get_part_stok_menipis_list(
        db,
        need_purchase=need_purchase,
        limit=limit,
    )


@router.post("/part-stok-menipis/po-draft", response_model=PartStokMenipisPoDraftResponse, status_code=status.HTTP_201_CREATED)
def create_part_stok_menipis_po_draft(
    data_in: PartStokMenipisPoDraftCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat PO DRAFT restock dari seleksi part stok menipis (alur Part Stok
    Menipis, Update #043).

    - Part digelondongkan ke supplier yang dipilih; gudang tujuan
      (master alamat pengiriman) OPSIONAL — kosong → kolom alamat pengiriman
      PO dibiarkan kosong.
    - Qty per baris otomatis dikunci ke sisa kekurangan saat ini (stok
      minimum − stok − yang sudah masuk PO aktif lain) bila qty diminta
      lebih besar; harga = harga pokok master (boleh 0, dilengkapi di draft).
    - PO disimpan berstatus DRAFT (TIDAK auto-final) supaya bisa dikoreksi
      dulu di menu Pesanan — finalisasi saat PO draft diedit & disimpan.
    """
    try:
        res = svc.create_po_draft_from_stok_menipis(
            db,
            supplier_id=data_in.supplier_id,
            gudang_id=data_in.gudang_id,
            tanggal=data_in.tanggal,
            catatan=data_in.catatan,
            items=[i.model_dump() for i in data_in.items],
            created_by=current_user.id,
        )
        # Fix #042 (mirror ROP): hasil service = tuple (po, info); replay
        # Idempotency-Key hanya mengembalikan model PO utama — unpack toleran
        # supaya replay tetap valid tanpa 500.
        if isinstance(res, tuple):
            po, info = res
        else:
            po, info = res, {}
        skipped = info.get("skipped", []) or []
        jumlah_item = int(info.get("jumlah_item") or 0)
        pesan = (
            f"{jumlah_item} part restock dibuat sebagai PO DRAFT "
            f"— koreksi harga/detail di menu Pesanan sebelum finalisasi"
        )
        if skipped:
            alasan = ", ".join(sorted({str(s.get("alasan") or "tidak perlu dibeli") for s in skipped}))
            pesan += f"; {len(skipped)} item dilewati ({alasan})"
        return {"po": po, "info": pesan}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/purchase-order/{po_id}/sisa", response_model=PurchaseOrderSisaResponse)
def get_purchase_order_sisa(
    po_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Sisa qty per baris Purchase Order (fitur tarik data — Update #3).

    - qtyDiterima = SUM qty penerimaan berstatus DIPROSES/SELESAI untuk baris
      PO tersebut (mirror purchase_validation.get_qty_received_so_far).
    - qtyTerfaktur = SUM qty invoice berstatus DIPROSES/SELESAI lewat dua
      jalur link: purchase_order_detail_id langsung ATAU penerimaan_barang_detail_id
      dari penerimaan baris PO tersebut.
    - sisaTerima = qtyPesanan - qtyDiterima (min 0);
      sisaFaktur = qtyPesanan - qtyTerfaktur (min 0).
    """
    po = svc.get_purchase_order_by_id(db, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase Order tidak ditemukan")

    details = []
    for d in po.details:
        qty_diterima = get_qty_received_so_far(db, d.id)
        qty_terfaktur = get_qty_invoiced_for_po_detail(db, d.id)
        details.append({
            "purchase_order_detail_id": d.id,
            "barang_id": d.barang_id,
            "kode_barang": d.barang.kode if d.barang else None,
            "nama_barang": d.barang.nama if d.barang else None,
            "satuan_id": d.satuan_id,
            "satuan_nama": d.satuan.nama if d.satuan else None,
            "qty_pesanan": d.qty,
            "qty_diterima": qty_diterima,
            "qty_terfaktur": qty_terfaktur,
            "sisa_terima": max(d.qty - qty_diterima, 0),
            "sisa_faktur": max(d.qty - qty_terfaktur, 0),
            "harga": float(d.harga or 0),
            "diskon": float(d.diskon or 0),
        })

    return {
        "purchase_order_id": po.id,
        "supplier_id": po.supplier_id,
        "no_pesanan": po.no_pesanan,
        "alamat": po.alamat,
        "syarat_bayar_id": po.syarat_bayar_id,
        "details": details,
    }


@router.put("/purchase-order/{po_id}", response_model=PurchaseOrderResponse)
def update_purchase_order(
    po_id: UUID,
    data_in: PurchaseOrderUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Purchase Order (header + opsional replace detail).

    Field header (termasuk currency, syarat_bayar_id, alamat pengiriman,
    PPIC — update ASAHI #3, dan PIC Gudang — update ASAHI #002) diteruskan ke
    service. ``details`` (update ASAHI #002) kini DIDUKUNG: mengganti seluruh
    baris barang termasuk relasi SO per baris & jenis pembelian — hanya
    untuk PO DRAFT yang belum ditaut penerimaan/invoice/retur; total
    dihitung ulang server setelah replace.

    Update #043 — SETELAH update tersimpan, PO difinalisasi (force=True)
    untuk SEMUA user: PO draft hasil ROP/restock "tetap DRAFT sampai diedit
    & disimpan baru final" (keputusan user); PO terminal berhenti di
    APPROVED seperti perilaku create (direct_complete idempotent terhadap
    dokumen yang sudah bergerak). Gagal finalisasi → 400 dengan pesan
    errornya — hasil update TIDAK ditelan, dokumen tetap tersimpan DRAFT.
    """
    item = svc.get_purchase_order_by_id(db, po_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Order tidak ditemukan")

    update_data = _service_kwargs(svc.update_purchase_order, data_in.model_dump(exclude_unset=True))
    try:
        obj = svc.update_purchase_order(db, db_obj=item, **update_data)
        # Update #043 — langsung final tanpa langkah approval untuk SEMUA user
        # (workflow maker-checker di modul penjualan/pembelian di-takedown).
        workflow_service.direct_complete(db, current_user, 'purchase_order', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/purchase-order/{po_id}/cancel")
def cancel_purchase_order(
    po_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Purchase Order (hard delete).

    Dokumen + rincian dihapus dari database (guard: masih dirujuk dokumen lanjutan → tolak);
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'purchase_order', po_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PURCHASE INVOICE
# ==========================================

@router.get("/purchase-invoice", response_model=PaginatedResponse[PurchaseInvoiceResponse])
def get_purchase_invoice_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no form, no faktur, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Purchase Invoice dengan filter dan pagination."""
    data, total = svc.get_purchase_invoice_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, supplier_id=supplier_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/purchase-invoice", response_model=PurchaseInvoiceResponse, status_code=status.HTTP_201_CREATED)
def create_purchase_invoice(
    data_in: PurchaseInvoiceCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Purchase Invoice baru (auto-generate no form + auto-post jurnal)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        biaya_data = [b.model_dump() for b in data_in.biaya_tambahan]
        obj = svc.create_purchase_invoice(
            db=db,
            tanggal_jatuh_tempo=data_in.tanggal_jatuh_tempo,
            syarat_bayar_id=data_in.syarat_bayar_id,
            tanggal=data_in.tanggal,
            supplier_id=data_in.supplier_id,
            no_faktur=data_in.no_faktur,
            details_data=details_data,
            biaya_data=biaya_data,
            alamat=data_in.alamat,
            diskon_global=data_in.diskon_global,
            ppn=data_in.ppn,
            # === Update #052 — pilihan PPh23/PPN kini diteruskan ke service
            # (sebelumnya diabaikan → grand total tersimpan ≠ tampilan form). ===
            ppn_applicable=data_in.ppn_applicable,
            pph23_applicable=data_in.pph23_applicable,
            pph23=data_in.pph23,
            keterangan=data_in.keterangan,
            auto_post_jurnal=False,  # Posting requires approved workflow.
            created_by=current_user.id,
            # Update #7 (B-06): teruskan link PO sumber — sebelumnya field schema
            # ini diabaikan sehingga purchase_invoice.purchase_order_id selalu NULL.
            purchase_order_id=data_in.purchase_order_id,
        )
        # Update #043 — langsung final tanpa langkah approval untuk SEMUA user
        # (workflow maker-checker di modul penjualan/pembelian di-takedown).
        workflow_service.direct_complete(db, current_user, 'purchase_invoice', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/purchase-invoice/tersedia-retur", response_model=list[PurchaseInvoiceResponse])
def get_purchase_invoice_tersedia_retur(
    supplier_id: UUID = Query(..., description="Filter supplier (form retur menyaring per supplier)"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """=== Update #063 — invoice SELESAI yang BELUM ter-retur penuh. ===

    Dipakai dropdown "Invoice" di form Retur Pembelian (create & edit).
    Sebelumnya daftar = SEMUA invoice SELESAI supplier → invoice yang
    barisnya sudah diretur penuh tetap muncul (permintaan user: "yang
    sudah terpakai sebelumnya jangan ditampilkan lagi").

    Definisi "belum terpakai penuh": masih ada sisa retur agregat —
    Σ qty retur (status DIPROSES/SELESAI, via link purchase_invoice_detail_id)
    < Σ qty detail invoice. Retur DRAFT tidak dihitung (bisa jadi batal);
    paritas perhitungan over-return post_purchase_retur yang memakai qty
    invoice sebagai batas baris di form (mirror sales_validation
    get_qty_returned_so_far).

    Response = bentuk PurchaseInvoiceResponse (sama seperti daftar lama)
    supaya hook frontend tinggal ganti URL.
    """
    qty_sq = (
        db.query(
            PurchaseInvoiceDetail.purchase_invoice_id.label("inv_id"),
            func.coalesce(func.sum(PurchaseInvoiceDetail.qty), 0).label("qty_inv"),
        )
        .group_by(PurchaseInvoiceDetail.purchase_invoice_id)
        .subquery()
    )
    diretur_det_sq = (
        db.query(
            PurchaseReturDetail.purchase_invoice_detail_id.label("inv_detail_id"),
            func.coalesce(func.sum(PurchaseReturDetail.qty), 0).label("diretur"),
        )
        .join(PurchaseRetur, PurchaseRetur.id == PurchaseReturDetail.purchase_retur_id)
        .filter(PurchaseRetur.status.in_([StatusPenjualan.DIPROSES, StatusPenjualan.SELESAI]))
        .group_by(PurchaseReturDetail.purchase_invoice_detail_id)
        .subquery()
    )
    diretur_sq = (
        db.query(
            PurchaseInvoiceDetail.purchase_invoice_id.label("inv_id"),
            func.coalesce(func.sum(func.coalesce(diretur_det_sq.c.diretur, 0)), 0).label("diretur_inv"),
        )
        .outerjoin(diretur_det_sq, diretur_det_sq.c.inv_detail_id == PurchaseInvoiceDetail.id)
        .group_by(PurchaseInvoiceDetail.purchase_invoice_id)
        .subquery()
    )
    rows = (
        db.query(PurchaseInvoice)
        .filter(
            PurchaseInvoice.supplier_id == supplier_id,
            PurchaseInvoice.status == StatusPenjualan.SELESAI,
        )
        .outerjoin(qty_sq, qty_sq.c.inv_id == PurchaseInvoice.id)
        .outerjoin(diretur_sq, diretur_sq.c.inv_id == PurchaseInvoice.id)
        .filter(func.coalesce(diretur_sq.c.diretur_inv, 0) < func.coalesce(qty_sq.c.qty_inv, 0))
        .order_by(PurchaseInvoice.created_at.desc())
        .limit(limit)
        .all()
    )
    return rows


@router.get("/purchase-invoice/tersedia-link", response_model=list[PurchaseInvoiceResponse])
def get_purchase_invoice_tersedia_link(
    supplier_id: UUID = Query(..., description="Filter supplier (form penerimaan menyaring per supplier)"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """=== Update #064 — invoice supplier yang BELUM dipakai penerimaan mana pun. ===

    Dipakai dropdown "Invoice" di form Penerimaan Barang (create & edit).
    Kelanjutan permintaan user: "alur yang menarik data — yang sudah
    terpakai sebelumnya jangan ditampilkan lagi" untuk dokumen LPPB,
    Penerimaan, dan Invoice.

    Definisi "sudah dipakai": invoice sudah terhubung ke ≥ 1 penerimaan
    (penerimaan.purchase_invoice_id terisi, penerimaan non-DIBATALKAN —
    reverse penerimaan me-set status DIBATALKAN sehingga linknya tidak
    lagi mengunci invoice). Invoice yang baru dibuat (belum di-link)
    tetap tampil — mempertahankan tujuan update #049 (invoice baru bisa
    langsung dipilih saat menghubungkan penerimaan).

    Catatan alur GRNI (#048): satu invoice memang boleh terhubung ke
    beberapa penerimaan — penghubungan multi itu tetap tersedia lewat
    panel "Penerimaan Barang" di form Invoice (centang beberapa
    penerimaan); endpoint ini hanya menyaring PENAWARAN di sisi form
    Penerimaan agar invoice yang sudah terpakai tidak ditawarkan lagi.

    Response = bentuk PurchaseInvoiceResponse (sama seperti daftar lama)
    supaya hook frontend tinggal ganti URL.
    """
    linked_exists = (
        db.query(PenerimaanBarang.id)
        .filter(
            PenerimaanBarang.purchase_invoice_id == PurchaseInvoice.id,
            PenerimaanBarang.status != StatusPenjualan.DIBATALKAN,
        )
        .exists()
    )
    rows = (
        db.query(PurchaseInvoice)
        .filter(
            PurchaseInvoice.supplier_id == supplier_id,
            PurchaseInvoice.status != StatusPenjualan.DIBATALKAN,
            ~linked_exists,
        )
        .order_by(PurchaseInvoice.created_at.desc())
        .limit(limit)
        .all()
    )
    return rows


@router.get("/purchase-invoice/{inv_id}", response_model=PurchaseInvoiceResponse)
def get_purchase_invoice_detail(
    inv_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Purchase Invoice."""
    item = svc.get_purchase_invoice_by_id(db, inv_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Invoice tidak ditemukan")
    return item


@router.put("/purchase-invoice/{inv_id}", response_model=PurchaseInvoiceResponse)
def update_purchase_invoice(
    inv_id: UUID,
    data_in: PurchaseInvoiceUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Purchase Invoice (header only).

    Field schema yang tidak didukung service (invoice_type) diabaikan agar
    tidak memicu HTTP 500 — update bersifat header-only.
    Update #7 (B-06): `purchase_order_id` kini didukung service dan ikut
    diteruskan oleh _service_kwargs bila dikirim (link/ubah PO sumber).
    """
    item = svc.get_purchase_invoice_by_id(db, inv_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Invoice tidak ditemukan")

    update_data = _service_kwargs(svc.update_purchase_invoice, data_in.model_dump(exclude_unset=True))
    try:
        return svc.update_purchase_invoice(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/purchase-invoice/{inv_id}/cancel")
def cancel_purchase_invoice(
    inv_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Purchase Invoice (hard delete).

    Dokumen + rincian + jurnal terkait dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'purchase_invoice', inv_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PURCHASE RETUR
# ==========================================

@router.get("/purchase-retur", response_model=PaginatedResponse[PurchaseReturResponse])
def get_purchase_retur_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no retur, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Purchase Retur dengan filter dan pagination."""
    data, total = svc.get_purchase_retur_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, supplier_id=supplier_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/purchase-retur", response_model=PurchaseReturResponse, status_code=status.HTTP_201_CREATED)
def create_purchase_retur(
    data_in: PurchaseReturCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Purchase Retur baru (auto-generate no retur + auto-post jurnal)."""
    try:
        details_data = [d.model_dump() for d in data_in.details]
        obj = svc.create_purchase_retur(
            db=db,
            gudang_id=data_in.gudang_id,
            purchase_invoice_id=data_in.purchase_invoice_id,
            tanggal=data_in.tanggal,
            purchase_order_id=data_in.purchase_order_id,
            supplier_id=data_in.supplier_id,
            details_data=details_data,
            alamat=data_in.alamat,
            ppn=data_in.ppn,
            keterangan=data_in.keterangan,
            auto_post_jurnal=False,  # Posting requires approved workflow.
            created_by=current_user.id,
        )
        # Update #043 — langsung final tanpa langkah approval untuk SEMUA user
        # (workflow maker-checker di modul penjualan/pembelian di-takedown).
        workflow_service.direct_complete(db, current_user, 'purchase_retur', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/purchase-retur/{retur_id}", response_model=PurchaseReturResponse)
def get_purchase_retur_detail(
    retur_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Purchase Retur."""
    item = svc.get_purchase_retur_by_id(db, retur_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Retur tidak ditemukan")
    return item


@router.put("/purchase-retur/{retur_id}", response_model=PurchaseReturResponse)
def update_purchase_retur(
    retur_id: UUID,
    data_in: PurchaseReturUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Purchase Retur (header only)."""
    item = svc.get_purchase_retur_by_id(db, retur_id)
    if not item:
        raise HTTPException(status_code=404, detail="Purchase Retur tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    try:
        return svc.update_purchase_retur(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/purchase-retur/{retur_id}/cancel")
def cancel_purchase_retur(
    retur_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Purchase Retur (hard delete).

    Dokumen + rincian + jurnal terkait dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'purchase_retur', retur_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# PENERIMAAN BARANG
# ==========================================

@router.get("/penerimaan", response_model=PaginatedResponse[PenerimaanBarangResponse])
def get_penerimaan_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari berdasarkan no form, keterangan"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status"),
    supplier_id: Optional[UUID] = Query(None, description="Filter supplier"),
    tanggal_from: Optional[date] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[date] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar Penerimaan Barang dengan filter dan pagination."""
    data, total = svc.get_penerimaan_list(
        db, skip=skip, limit=limit, search=search,
        status=status_filter, supplier_id=supplier_id,
        tanggal_from=tanggal_from, tanggal_to=tanggal_to,
    )
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/penerimaan", response_model=PenerimaanBarangResponse, status_code=status.HTTP_201_CREATED)
def create_penerimaan(
    data_in: PenerimaanBarangCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat Penerimaan Barang baru (auto-generate no form).

    Tahap 2: Field `purchase_invoice_id` opsional untuk anti double-record
    Persediaan (link ke invoice yang akan dipost nanti).
    """
    try:
        details_data = [d.model_dump() for d in data_in.details]
        obj = svc.create_penerimaan(
            db=db,
            gudang_id=data_in.gudang_id,
            tanggal=data_in.tanggal,
            purchase_order_id=data_in.purchase_order_id,
            supplier_id=data_in.supplier_id,
            details_data=details_data,
            alamat=data_in.alamat,
            keterangan=data_in.keterangan,
            created_by=current_user.id,
            purchase_invoice_id=data_in.purchase_invoice_id,
        )
        # Update #043 — langsung final tanpa langkah approval untuk SEMUA user
        # (workflow maker-checker di modul penjualan/pembelian di-takedown).
        workflow_service.direct_complete(db, current_user, 'penerimaan_barang', obj.id, force=True)
        db.refresh(obj)
        return obj
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/penerimaan/{pb_id}", response_model=PenerimaanBarangResponse)
def get_penerimaan_detail(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 Penerimaan Barang."""
    item = svc.get_penerimaan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penerimaan Barang tidak ditemukan")
    return item


@router.put("/penerimaan/{pb_id}", response_model=PenerimaanBarangResponse)
def update_penerimaan(
    pb_id: UUID,
    data_in: PenerimaanBarangUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update data Penerimaan Barang (header + optional detail replacement).

    Phase C fix: kirim `details` untuk replace seluruh detail penerimaan.
    """
    item = svc.get_penerimaan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penerimaan Barang tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    # Phase C: translate schema field `details` → service param `details_data`
    if 'details' in update_data:
        update_data['details_data'] = update_data.pop('details')
    try:
        return svc.update_penerimaan(db, db_obj=item, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/penerimaan/{pb_id}/selesaikan", response_model=PenerimaanBarangResponse)
def selesaikan_penerimaan(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """=== Update #068 — "Selesaikan" Penerimaan Barang (DIPROSES → SELESAI). ===

    Jalur pemulihan untuk penerimaan yang tertinggal di status DIPROSES.
    Latar: create_penerimaan COMMIT dokumen berstatus DIPROSES lebih dulu,
    baru workflow direct_complete menjalankan finish_penerimaan (tambah stok
    + jurnal GRNI). Bila finalisasi itu GAGAL (mis. setting GRNI belum
    di-configure, harga perolehan tak dapat ditentukan, periode ditutup,
    stok bermasalah), dokumen DIPROSES TERSISA di database — qty-nya sudah
    dihitung "diterima" (dropdown PO tersedia menyembunyikan PO-nya) tapi
    penerimaannya tidak pernah SELESAI, sehingga PO tidak akan pernah naik
    status ke Selesai (syarat _po_fully_received menerima penerimaan
    berstatus SELESAI). Sebelum #068 tidak ada tombol pemulihan di FE —
    satu-satunya jalan adalah Batal (hard delete) lalu buat ulang.

    Endpoint ini memfinalisasi ulang penerimaan DIPROSES (idempotent untuk
    yang sudah SELESAI: ditolak dengan pesan jelas). Mirror pola
    /lppb/{id}/selesaikan.
    """
    item = svc.get_penerimaan_by_id(db, pb_id)
    if not item:
        raise HTTPException(status_code=404, detail="Penerimaan Barang tidak ditemukan")
    try:
        return svc.finish_penerimaan(db, item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/penerimaan/{pb_id}/cancel")
def cancel_penerimaan(
    pb_id: UUID,
    payload: Optional[HardDeleteRequest] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen Penerimaan Barang (hard delete).

    Dokumen + rincian dihapus dari database (guard: stok sudah bergerak → tolak);
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    try:
        return cancel_document(
            db, 'penerimaan_barang', pb_id, current_user,
            reason=payload.reason if payload else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/penerimaan/{pb_id}/reverse", response_model=PenerimaanBarangResponse)
def reverse_penerimaan(
    pb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
    reason: Optional[str] = Query(None, description="Alasan reversal (optional)"),
):
    """Reverse Penerimaan Barang yang sudah di-finish (status SELESAI).

    Phase 5 — Master Roadmap §19: "Direct cancel after stock movement blocked"
    + §10: "Reversal exact value/layer".

    Berbeda dengan cancel (yang hanya untuk status DRAFT/DIPROSES), reverse
    bisa untuk penerimaan yang sudah SELESAI — stok sudah bertambah & GRNI
    journal sudah posted.

    Reverse akan:
    1. Reverse GRNI journal (Dr GRNI / Cr Persediaan — pembalik dari saat finish)
    2. Reverse stock movement (call reverse_stock_movement untuk setiap detail)
       - Hapus exact layer FIFO/FEFO yang dibuat saat receipt asli (kalau masih ada sisa)
       - Update StockBalance (qty + nilai dikurang)
       - Recalculate master barang.stok & harga_pokok
    3. Set status PenerimaanBarang ke DIBATALKAN
    4. Catat reversal_of_id di setiap StokMutasi reversal (audit trail)

    Tidak bisa reverse kalau:
    - Sudah ada purchase invoice yang memakai receipt ini (POSTED)
      → batalkan/reverse invoice terlebih dahulu
    - Sudah ada purchase retur yang memakai receipt detail (POSTED)
      → batalkan/reverse retur terlebih dahulu
    - Stok gudang sudah terpakai transaksi lain (mis. sudah terjual)
      → akan return error dengan detail stok yang kurang
    """
    from app.services.inventory_reversal_service import reverse_receipt

    try:
        result = reverse_receipt(
            db, pb_id, current_user.id,
            reason=reason or "Reversal penerimaan"
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# INVOICE BELUM BAYAR (untuk Pembayaran Kas)
# ==========================================

@router.get("/invoice-belum-bayar/{supplier_id}")
def get_invoice_belum_bayar(
    supplier_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """List invoice pembelian yang belum dibayar penuh untuk suatu supplier.

    Digunakan di form Pembayaran Kas untuk cascade dropdown:
    Pilih Hutang -> Pilih Supplier -> muncul list invoice.

    Return list sederhana dengan field tambahan `sisaTagihan`.

    === Update #068 — sisa tagihan NYATA + hanya invoice yang masih punya
    sisa (kelanjutan permintaan user: "alur yang menarik data — yang sudah
    terpakai/sudah lunas jangan ditampilkan lagi", diterapkan ke semua
    endpoint penarik data). Sebelumnya endpoint ini mengembalikan SEMUA
    invoice aktif dengan sisaTagihan = grand_total (komentar lama: tracking
    pembayaran per-invoice belum ada) — padahal alokasi pembayaran
    (payment_allocation, #Phase 4) sudah lama tersedia. Kini sisa dihitung
    pakai settlement_service.balances (grand_total − bayar − retur; paritas
    modul ln/tagihan) dan hanya invoice dengan sisa > 0 yang dikirim. ===
    """
    from app.models.transaksi.pembelian.purchase_invoice import PurchaseInvoice
    from app.models.master.supplier import Supplier
    from app.services import settlement_service as settlement
    from decimal import Decimal

    # Validasi supplier
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")

    # Ambil semua invoice aktif (bukan BATAL) untuk supplier ini
    invoices = (
        db.query(PurchaseInvoice)
        .filter(
            PurchaseInvoice.supplier_id == supplier_id,
            PurchaseInvoice.status != StatusPenjualan.DIBATALKAN,
        )
        .order_by(PurchaseInvoice.tanggal.desc())
        .all()
    )

    # === Update #068 — sisa tagihan per invoice dari alokasi pembayaran
    # aktif + retur (paritas settlement_service.balances / modul ln). ===
    balances = settlement.balances(db, invoices) if invoices else {}

    result = []
    for inv in invoices:
        sisa = Decimal(str(balances.get(inv.id, {}).get("sisa_tagihan", inv.grand_total or 0)))
        if sisa <= Decimal("0.00"):
            continue  # sudah lunas / lebih bayar — jangan ditawarkan lagi
        result.append({
            "id": inv.id,
            "no_form": inv.no_form,
            "no_faktur": inv.no_faktur,
            "tanggal": inv.tanggal,
            "grand_total": inv.grand_total,
            "total_ppn": inv.total_ppn,
            "sisa_tagihan": sisa,
            "status": inv.status.value if inv.status else "DRAFT",
        })

    return result


# ==========================================
# LPPB — LAPORAN PEMERIKSAAN & PENGUJIAN BARANG (Update ASAHI #005)
# Dokumen RECORD QC barang masuk — non-finansial (tanpa posting stok/jurnal).
# Workflow: Simpan (DRAFT) → "Selesaikan" (SELESAI, final) → Cetak.
# ==========================================

@router.get("/lppb", response_model=PaginatedResponse[LppbListResponse])
def list_lppb(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Cari no LPPB, no PO, supplier, proyek, No DO, No RI"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter status (DRAFT/SELESAI)"),
    purchase_order_id: Optional[UUID] = Query(None, description="Filter per Purchase Order"),
    supplier_id: Optional[UUID] = Query(None, description="Filter per supplier"),
    tanggal_from: Optional[datetime] = Query(None, description="Filter tanggal mulai"),
    tanggal_to: Optional[datetime] = Query(None, description="Filter tanggal sampai"),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil daftar LPPB dengan filter dan pagination."""
    try:
        data, total = lppb_svc.get_lppb_list(
            db, skip=skip, limit=limit, search=search,
            status=status_filter, purchase_order_id=purchase_order_id,
            supplier_id=supplier_id,
            tanggal_from=tanggal_from, tanggal_to=tanggal_to,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"data": data, "total": total, "skip": skip, "limit": limit}


@router.post("/lppb", response_model=LppbResponse, status_code=status.HTTP_201_CREATED)
def create_lppb(
    data_in: LppbCreate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Buat LPPB baru (auto-generate no "LPPB ASI/YYYY/NNN").

    Supplier ditarik otomatis dari Purchase Order yang dipilih;
    item tabel diprefill frontend via GET /purchase-order/{id}/sisa.
    """
    try:
        return lppb_svc.create_lppb(
            db=db,
            tanggal=data_in.tanggal,
            purchase_order_id=data_in.purchase_order_id,
            details=[d.model_dump() for d in data_in.details],
            created_by=current_user.id,
            tanggal_terima=data_in.tanggal_terima,
            nomor_do=data_in.nomor_do,
            nomor_ri=data_in.nomor_ri,
            proyek=data_in.proyek,
            revisi=data_in.revisi,
            no_format=data_in.no_format,
            keterangan=data_in.keterangan,
            mengetahui_jabatan=data_in.mengetahui_jabatan,
            mengetahui_nama=data_in.mengetahui_nama,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/lppb/{lppb_id}", response_model=LppbResponse)
def get_lppb(
    lppb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Ambil detail 1 LPPB (lengkap dengan baris pemeriksaan)."""
    item = lppb_svc.get_lppb_by_id(db, lppb_id)
    if not item:
        raise HTTPException(status_code=404, detail="LPPB tidak ditemukan")
    return item


@router.put("/lppb/{lppb_id}", response_model=LppbResponse)
def update_lppb(
    lppb_id: UUID,
    data_in: LppbUpdate,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Update LPPB (hanya status DRAFT)."""
    item = lppb_svc.get_lppb_by_id(db, lppb_id)
    if not item:
        raise HTTPException(status_code=404, detail="LPPB tidak ditemukan")

    update_data = data_in.model_dump(exclude_unset=True)
    details = update_data.pop("details", None)
    try:
        return lppb_svc.update_lppb(db, db_obj=item, details=details, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/lppb/{lppb_id}/selesaikan", response_model=LppbResponse)
def selesaikan_lppb(
    lppb_id: UUID,
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """"Selesaikan" LPPB (finalisasi): DRAFT → SELESAI.

    Workflow tanpa persetujuan (pola ROP #003): form diisi → Simpan (DRAFT) →
    tombol "Selesaikan". Dokumen final tidak bisa diedit lagi; hanya Batalkan.
    """
    item = lppb_svc.get_lppb_by_id(db, lppb_id)
    if not item:
        raise HTTPException(status_code=404, detail="LPPB tidak ditemukan")
    try:
        return lppb_svc.selesaikan_lppb(db, db_obj=item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/lppb/{lppb_id}/cancel")
def cancel_lppb(
    lppb_id: UUID,
    payload: Optional[Dict[str, Any]] = Body(None),
    db: Session = Depends(get_current_db),
    current_user: Pengguna = Depends(get_current_user),
):
    """Hapus permanen LPPB (hard delete) — berlaku utk DRAFT maupun final.

    Dokumen + baris pemeriksaan dihapus dari database;
    jejak lengkap tersimpan di log dokumen terhapus (modul Histori).
    """
    item = lppb_svc.get_lppb_by_id(db, lppb_id)
    if not item:
        raise HTTPException(status_code=404, detail="LPPB tidak ditemukan")
    reason = (payload or {}).get("reason")
    try:
        return lppb_svc.cancel_lppb(db, db_obj=item, user=current_user, reason=reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
