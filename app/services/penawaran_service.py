"""penawaran_service.py

Service layer untuk modul Penawaran (quotation) — Update #3.

Blueprint = SalesOrder (header + detail + TransaksiBiaya), TANPA jurnal dan
TANPA workflow:
- create_penawaran: validasi pelanggan, sub_total per baris =
  harga * qty * (100 - diskon%) / 100, totals via document_totals.refresh_totals
  (order_docs — pola identik create_sales_order), nomor otomatis PEN-YYYY-MM-NNN.
- update_penawaran: hanya status DRAFT; full update header + replace details + biaya.
- convert_to_so: konversi jadi Sales Order (create_sales_order + direct_complete,
  mirror endpoint POST /penjualan/sales-order) lalu penawaran menjadi SELESAI.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session, joinedload

from app.models.detail.penawaran_detail import PenawaranDetail
from app.models.master.pelanggan import Pelanggan
from app.models.transaksi.penjualan.penawaran import Penawaran
from app.models.transaksi.transaksi_biaya import TransaksiBiaya
from app.services.accounting_control import atomic_accounting_write
from app.services.decimal_utils import safe_decimal, safe_int
from app.utils.nomor_dokumen import get_nomor_dokumen


def _hitung_sub_total_line(harga: Decimal, qty: int, diskon: Decimal) -> Decimal:
    """sub_total per baris = harga * qty * (100 - diskon%) / 100 (mirror SO)."""
    return harga * qty * (Decimal("100") - diskon) / Decimal("100")


def _normalize_details(details_data: list) -> None:
    """Isi sub_total tiap baris dari harga/qty/diskon (mutasi dict in-place)."""
    for d in details_data:
        d["sub_total"] = _hitung_sub_total_line(
            safe_decimal(d.get("harga")),
            safe_int(d.get("qty")),
            safe_decimal(d.get("diskon")),
        )


def get_penawaran_list(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
    status: str | None = None,
    pelanggan_id: UUID | None = None,
    tanggal_from: date | None = None,
    tanggal_to: date | None = None,
) -> tuple[list[Penawaran], int]:
    """Ambil daftar penawaran dengan filter & pagination (mirror list SO)."""
    query = db.query(Penawaran).options(
        joinedload(Penawaran.pelanggan),
        joinedload(Penawaran.syarat_bayar),
        joinedload(Penawaran.creator),
        joinedload(Penawaran.details).joinedload(PenawaranDetail.barang),
        joinedload(Penawaran.biaya_tambahan),
    )

    if search:
        pattern = f"%{search}%"
        query = query.outerjoin(Pelanggan, Pelanggan.id == Penawaran.pelanggan_id).filter(
            Penawaran.no_penawaran.ilike(pattern)
            | Pelanggan.nama.ilike(pattern)
        )
    if status:
        query = query.filter(Penawaran.status == status)
    if pelanggan_id:
        query = query.filter(Penawaran.pelanggan_id == pelanggan_id)
    if tanggal_from:
        query = query.filter(Penawaran.tanggal >= tanggal_from)
    if tanggal_to:
        query = query.filter(Penawaran.tanggal <= tanggal_to)

    total = query.count()
    data = query.order_by(Penawaran.created_at.desc()).offset(skip).limit(limit).all()
    return data, total


def get_penawaran_by_id(db: Session, penawaran_id: UUID) -> Penawaran | None:
    """Ambil 1 penawaran berdasarkan ID dengan detail + biaya (mirror get SO)."""
    return (
        db.query(Penawaran)
        .options(
            joinedload(Penawaran.pelanggan),
            joinedload(Penawaran.syarat_bayar),
            joinedload(Penawaran.creator),
            joinedload(Penawaran.details).joinedload(PenawaranDetail.barang),
            joinedload(Penawaran.biaya_tambahan),
        )
        .filter(Penawaran.id == penawaran_id)
        .first()
    )


@atomic_accounting_write
def create_penawaran(
    db: Session,
    tanggal: datetime,
    pelanggan_id: UUID,
    details_data: list,
    biaya_data: list | None = None,
    syarat_bayar_id: UUID | None = None,
    alamat_pengiriman: str | None = None,
    keterangan: str | None = None,
    mata_uang: str = "IDR",
    diskon_global: Decimal | None = Decimal("0"),
    ppn: Decimal = Decimal("11"),
    berlaku_hingga: date | None = None,
    created_by: UUID | None = None,
) -> Penawaran:
    """Buat Penawaran baru beserta detail + biaya tambahan.

    - Generate no_penawaran otomatis (prefix PEN)
    - sub_total per baris = harga * qty * (100 - diskon%) / 100
    - Totals header (sub_total/total_diskon/total_ppn/total_biaya_tambahan/
      grand_total) dihitung dengan pola create_sales_order → refresh_totals.
    - TANPA jurnal, TANPA workflow (status DRAFT).
    """
    try:
        biaya_data = biaya_data or []

        # Validasi pelanggan (mirror create_sales_order)
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # sub_total per baris
        _normalize_details(details_data)

        # Generate nomor penawaran
        no_penawaran = get_nomor_dokumen(
            db, Penawaran, prefix="PEN",
            no_column="no_penawaran", tanggal=tanggal.date(),
        )

        # Buat header
        penawaran = Penawaran(
            no_penawaran=no_penawaran,
            tanggal=tanggal,
            berlaku_hingga=berlaku_hingga,
            pelanggan_id=pelanggan_id,
            syarat_bayar_id=syarat_bayar_id,
            alamat_pengiriman=alamat_pengiriman,
            keterangan=keterangan,
            mata_uang=mata_uang,
            diskon_global=diskon_global,
            ppn=ppn,
            status="DRAFT",
            created_by=created_by,
        )
        db.add(penawaran)
        db.flush()

        # Buat detail
        for d in details_data:
            db.add(PenawaranDetail(
                penawaran_id=penawaran.id,
                barang_id=d["barang_id"],
                satuan_id=d.get("satuan_id"),
                qty=safe_int(d.get("qty")),
                harga=safe_decimal(d.get("harga")),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=safe_decimal(d.get("sub_total")),
                keterangan=d.get("keterangan"),
            ))

        # Buat biaya tambahan
        for b in biaya_data:
            db.add(TransaksiBiaya(
                nama=b["nama"],
                jumlah=safe_decimal(b.get("jumlah")),
                penawaran_id=penawaran.id,
            ))

        # Totals — mirror alur create_sales_order (order_docs di refresh_totals)
        from app.services.document_totals import refresh_totals
        db.flush()
        refresh_totals(penawaran)

        db.commit()
        db.refresh(penawaran)
        logger.info(f"Penawaran created: {no_penawaran} | grand_total={penawaran.grand_total}")
        return penawaran

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating Penawaran: {e}")
        raise


@atomic_accounting_write
def update_penawaran(
    db: Session,
    db_obj: Penawaran,
    tanggal: datetime | None = None,
    berlaku_hingga: date | None = None,
    pelanggan_id: UUID | None = None,
    syarat_bayar_id: UUID | None = None,
    alamat_pengiriman: str | None = None,
    keterangan: str | None = None,
    mata_uang: str | None = None,
    diskon_global: Decimal | None = None,
    ppn: Decimal | None = None,
    details: list | None = None,
    biaya_tambahan: list | None = None,
) -> Penawaran:
    """Update penawaran — hanya status DRAFT.

    Full update: field header + (opsional) replace seluruh baris detail dan
    biaya tambahan (pola mirror update_penerimaan). Totals dihitung ulang
    via refresh_totals.
    """
    if db_obj.status != "DRAFT":
        raise ValueError(f"Penawaran dengan status {db_obj.status} tidak bisa diupdate")

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if berlaku_hingga is not None:
        db_obj.berlaku_hingga = berlaku_hingga
    if pelanggan_id is not None:
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")
        db_obj.pelanggan_id = pelanggan_id
    if syarat_bayar_id is not None:
        db_obj.syarat_bayar_id = syarat_bayar_id
    if alamat_pengiriman is not None:
        db_obj.alamat_pengiriman = alamat_pengiriman
    if keterangan is not None:
        db_obj.keterangan = keterangan
    if mata_uang is not None:
        db_obj.mata_uang = mata_uang
    if diskon_global is not None:
        db_obj.diskon_global = diskon_global
    if ppn is not None:
        db_obj.ppn = ppn

    # Replace detail (bila dikirim)
    if details is not None:
        _normalize_details(details)
        db_obj.details.clear()
        db.flush()
        for d in details:
            db_obj.details.append(PenawaranDetail(
                barang_id=d["barang_id"],
                satuan_id=d.get("satuan_id"),
                qty=safe_int(d.get("qty")),
                harga=safe_decimal(d.get("harga")),
                diskon=safe_decimal(d.get("diskon")),
                sub_total=safe_decimal(d.get("sub_total")),
                keterangan=d.get("keterangan"),
            ))
        db.flush()

    # Replace biaya tambahan (bila dikirim)
    if biaya_tambahan is not None:
        db_obj.biaya_tambahan.clear()
        db.flush()
        for b in biaya_tambahan:
            db_obj.biaya_tambahan.append(TransaksiBiaya(
                nama=b["nama"],
                jumlah=safe_decimal(b.get("jumlah")),
            ))
        db.flush()

    # Hitung ulang totals (mirror update SO/invoice)
    from app.services.document_totals import refresh_totals
    refresh_totals(db_obj)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


@atomic_accounting_write
def convert_to_so(db: Session, penawaran_id: UUID, user):
    """Konversi Penawaran menjadi Sales Order (Update #3).

    - Guard: hanya penawaran berstatus DRAFT / DIPROSES.
    - SO dibuat lewat create_sales_order (pola internal yang sama dengan
      endpoint POST /penjualan/sales-order) lalu difinalisasi dengan
      workflow_service.direct_complete — admin: submit → approve
      (order terminal APPROVED, tanpa posting jurnal).
    - Copy: tanggal = now, pelanggan, syarat_bayar, alamat_pengiriman,
      keterangan = "Dari Penawaran {no}" (+ keterangan lama), diskon_global,
      ppn, details (harga/qty/diskon/satuan), biaya_tambahan.
    - Penawaran ditandai SELESAI.
    """
    from app.services import penjualan_service, workflow_service

    penawaran = get_penawaran_by_id(db, penawaran_id)
    if penawaran is None:
        raise ValueError(f"Penawaran dengan ID {penawaran_id} tidak ditemukan")
    if penawaran.status not in ("DRAFT", "DIPROSES"):
        raise ValueError(
            f"Penawaran dengan status {penawaran.status} tidak bisa dikonversi "
            f"menjadi Sales Order"
        )
    if not penawaran.details:
        raise ValueError("Penawaran tidak memiliki baris detail")

    keterangan = f"Dari Penawaran {penawaran.no_penawaran}"
    if penawaran.keterangan:
        keterangan = f"{keterangan} — {penawaran.keterangan}"

    details_data = [
        {
            "barang_id": d.barang_id,
            "satuan_id": d.satuan_id,
            "qty": d.qty,
            "harga": d.harga,
            "diskon": d.diskon if d.diskon is not None else Decimal("0"),
            "sub_total": d.sub_total,
        }
        for d in penawaran.details
    ]
    biaya_data = [
        {"nama": b.nama, "jumlah": b.jumlah}
        for b in penawaran.biaya_tambahan
    ]

    so = penjualan_service.create_sales_order(
        db=db,
        tanggal=datetime.now(UTC),
        pelanggan_id=penawaran.pelanggan_id,
        details_data=details_data,
        biaya_data=biaya_data,
        syarat_bayar_id=penawaran.syarat_bayar_id,
        alamat_pengiriman=penawaran.alamat_pengiriman,
        keterangan=keterangan,
        diskon_global=penawaran.diskon_global,
        ppn=penawaran.ppn,
        created_by=getattr(user, "id", None) or penawaran.created_by,
        currency=penawaran.mata_uang,
    )

    # Finalisasi (mirror endpoint POST /penjualan/sales-order):
    # admin bypass → submit → approve; order berhenti di APPROVED (terminal).
    workflow_service.direct_complete(db, user, "sales_order", so.id)

    # Tandai penawaran selesai
    penawaran.status = "SELESAI"
    db.add(penawaran)
    db.commit()
    db.refresh(so)
    logger.info(
        f"Penawaran {penawaran.no_penawaran} converted to SalesOrder {so.no_pesanan}"
    )
    return so
