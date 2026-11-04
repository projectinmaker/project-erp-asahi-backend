"""penawaran_service.py

Service layer untuk modul Penawaran (quotation) — Update #3, revisi #031.

Blueprint = SalesOrder (header + detail + TransaksiBiaya), TANPA jurnal dan
TANPA workflow:
- create_penawaran: validasi pelanggan, sub_total per baris =
  harga * qty * (100 - diskon%) / 100, totals via document_totals.refresh_totals
  (order_docs — pola identik create_sales_order).
- Update #031 — penomoran "No Quotation":
    QUOT ASI/001/MJE/X/2026
    └prefix┘└kode┘└seq┘└singkatan pelanggan (LOCK dari master)┘└bulan romawi┘└tahun┘
  Nomor urut PER PELANGGAN, RESET PER TAHUN; user hanya memilih bulan & tahun
  (preview di form via peek_nomor_penawaran; nomor final ditetapkan saat simpan).
- Update #031 — item BARIS BEBAS: baris detail boleh berupa item yang diketik
  manual (belum ada di master barang — item penawaran "belum ada di gudang").
  Item bebas TIDAK dibuatkan di master data; tersimpan di tabel katalog
  penawaran (penawaran_katalog_item) supaya item berulang tinggal dipilih lagi.
  Saat konversi ke Sales Order, item bebas BARU dibuatkan di master barang
  (stock_item=False — bukan barang stok).
- update_penawaran: hanya status DRAFT; full update header + replace details + biaya.
  Pelanggan tidak bisa diganti (bagian dari identitas nomor Quotation) —
  buat penawaran baru untuk pelanggan berbeda.
- convert_to_so: konversi jadi Sales Order (create_sales_order + direct_complete
  force — Update #031: SO langsung final untuk semua user, tanpa workflow),
  lalu penawaran menjadi SELESAI.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.detail.penawaran_detail import PenawaranDetail
from app.models.master.barang import Barang, MetodeValuasi
from app.models.master.kategori_barang import KategoriBarang
from app.models.master.pelanggan import Pelanggan
from app.models.master.penawaran_katalog import PenawaranKatalogItem
from app.models.master.satuan import Satuan
from app.models.transaksi.penjualan.penawaran import Penawaran
from app.models.transaksi.transaksi_biaya import TransaksiBiaya
from app.services.accounting_control import atomic_accounting_write, accounting_lock
from app.services.decimal_utils import safe_decimal, safe_int
from app.utils.nomor_dokumen import (
    ROMAN_MONTHS,
    get_kode_perusahaan,
    resolve_prefix,
    roman_month,
)


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


def _validate_bulan_tahun(bulan: int | None, tahun: int | None, tanggal: datetime) -> tuple[int, int]:
    """Update #031 — resolusi bulan/tahun nomor Quotation.

    Default (bila tidak dikirim): mengikuti tanggal dokumen. Validasi bulan
    1-12 dan tahun masuk akal (2000-2100) — user hanya boleh mengisi itu saja;
    nomor urut & singkatan otomatis dari sistem/master.
    """
    b = int(bulan) if bulan is not None else tanggal.month
    t = int(tahun) if tahun is not None else tanggal.year
    if b < 1 or b > 12:
        raise ValueError("Bulan Quotation harus angka 1-12")
    if t < 2000 or t > 2100:
        raise ValueError("Tahun Quotation harus antara 2000-2100")
    return b, t


def _parse_quotation_seq(no: str, prefix: str, kode: str, tahun: int) -> int | None:
    """Parse nomor urut dari nomor Quotation berformat ketat.

    Format: "{PREFIX} {KODE}/{seq:03d}/{singkatan}/{bulan romawi}/{tahun}".
    Parse KETAT (5 segmen + segmen bulan harus romawi valid + segmen akhir =
    tahun) supaya dokumen lama "PEN ASI/2026/001" (3 segmen) atau nomor
    menyimpang TIDAK ikut terhitung dalam sequence.
    """
    if not no:
        return None
    parts = no.split("/")
    if len(parts) != 5:
        return None
    if parts[0] != f"{prefix} {kode}":
        return None
    if parts[3] not in ROMAN_MONTHS:
        return None
    if parts[4] != str(tahun):
        return None
    seq = parts[1]
    return int(seq) if seq.isdigit() else None


def _generate_no_quotation(
    db: Session, pelanggan: Pelanggan, bulan: int, tahun: int
) -> str:
    """Generate No Quotation berikutnya — urut PER PELANGGAN, reset per tahun.

    - Prefix dari Preferensi (doc_type PENAWARAN, default "QUOT" — Update #031).
    - Kode perusahaan dari Preferensi (default "ASI").
    - Segmen singkatan = snapshot pelanggan.singkatan (terkunci dari master;
      kosong → "-").
    - Sequence dihitung dari nomor Quotation pelanggan yang sama pada tahun
      tersebut — pattern LIKE longgar lalu parse ketat (lihat _parse_quotation_seq)
      supaya nomor lama format "PEN ASI/YYYY/NNN" tidak mengganggu.
    """
    accounting_lock(db)
    prefix = resolve_prefix(db, "PENAWARAN", "QUOT")
    kode = get_kode_perusahaan(db)
    singkatan = (getattr(pelanggan, "singkatan", None) or "").strip() or "-"

    pattern = f"{prefix} {kode}/%/{tahun}"
    rows = (
        db.query(Penawaran.no_penawaran)
        .filter(Penawaran.pelanggan_id == pelanggan.id, Penawaran.no_penawaran.like(pattern))
        .all()
    )
    last_seq = 0
    for (no,) in rows:
        seq = _parse_quotation_seq(no, prefix, kode, tahun)
        if seq is not None:
            last_seq = max(last_seq, seq)

    return f"{prefix} {kode}/{last_seq + 1:03d}/{singkatan}/{roman_month(bulan)}/{tahun}"


def peek_nomor_penawaran(db: Session, pelanggan_id: UUID, bulan: int, tahun: int) -> str:
    """Preview nomor Quotation BERIKUTNYA (indikatif, tanpa create) — Update #031.

    Dipakai form Penawaran untuk menampilkan nomor yang akan dipakai sebelum
    submit (pola "No. Dokumen" form Penerimaan #16). Nomor final di-generate
    ulang saat create.
    """
    pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
    if not pelanggan:
        raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")
    bulan, tahun = _validate_bulan_tahun(bulan, tahun, datetime.now(UTC))
    return _generate_no_quotation(db, pelanggan, bulan, tahun)


# ═══════════════════════════════════════════════════════════════════════════
# Update #031 — katalog item penawaran (item bebas, TIDAK masuk master data)
# ═══════════════════════════════════════════════════════════════════════════


def _upsert_katalog(db: Session, nama: str, satuan: str | None, harga: Decimal) -> PenawaranKatalogItem:
    """Upsert item katalog penawaran by nama (case-insensitive setelah strip).

    Baris baru → dipakai_count=1; sudah ada → satuan/harga diperbarui ke
    nilai terakhir + dipakai_count naik. Dipanggil saat penawaran disimpan
    (create maupun update) untuk setiap baris item bebas.
    """
    nama_norm = (nama or "").strip()
    row = (
        db.query(PenawaranKatalogItem)
        .filter(func.lower(func.trim(PenawaranKatalogItem.nama)) == nama_norm.lower())
        .first()
    )
    if row is not None:
        if satuan:
            row.satuan_nama = satuan
        row.harga = harga or Decimal("0")
        row.dipakai_count = (row.dipakai_count or 0) + 1
        return row
    row = PenawaranKatalogItem(
        nama=nama_norm,
        satuan_nama=(satuan or None),
        harga=harga or Decimal("0"),
        dipakai_count=1,
    )
    db.add(row)
    db.flush()
    return row


def get_katalog_items(db: Session, search: str | None = None, limit: int = 50) -> list[PenawaranKatalogItem]:
    """Daftar item katalog penawaran (paling sering dipakai duluan).

    Dipakai form Penawaran sebagai saran item bebas — item berulang tinggal
    dipilih lagi (nama/satuan/harga terakhir terisi otomatis).
    """
    query = db.query(PenawaranKatalogItem)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(PenawaranKatalogItem.nama.ilike(pattern))
    return (
        query.order_by(PenawaranKatalogItem.dipakai_count.desc(), PenawaranKatalogItem.nama.asc())
        .limit(max(1, min(limit, 200)))
        .all()
    )


def _normalize_free_item(d: dict) -> None:
    """Normalisasi baris item bebas (mutasi dict in-place) — Update #031.

    Baris bebas = barang_id kosong + item_nama terisi. Wajib ada nama;
    satuan/harga opsional (harga default 0). Normalisasi strip.
    """
    item_nama = (d.get("item_nama") or "").strip()
    if not item_nama:
        raise ValueError("Baris item bebas wajib memiliki nama item")
    d["item_nama"] = item_nama
    d["item_satuan"] = (d.get("item_satuan") or "").strip() or None
    d["katalog_id"] = d.get("katalog_id") or None


# ═══════════════════════════════════════════════════════════════════════════
# CRUD Penawaran
# ═══════════════════════════════════════════════════════════════════════════


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
        joinedload(Penawaran.details).joinedload(PenawaranDetail.satuan),
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
            joinedload(Penawaran.details).joinedload(PenawaranDetail.satuan),
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
    customer_po_number: str | None = None,
    customer_code: str | None = None,
    created_by: UUID | None = None,
    # === Update #031 — No Quotation (user hanya input bulan & tahun) ===
    bulan: int | None = None,
    tahun: int | None = None,
) -> Penawaran:
    """Buat Penawaran baru beserta detail + biaya tambahan.

    - No Quotation otomatis "QUOT ASI/001/MJE/X/2026" (urut per pelanggan,
      reset tahunan; bulan/tahun dari input user, default tanggal dokumen).
    - Singkatan customer TERKUNCI dari master pelanggan (payload customer_code
      diabaikan — field di form read-only).
    - sub_total per baris = harga * qty * (100 - diskon%) / 100
    - Totals header via refresh_totals (pola identik create_sales_order).
    - Baris boleh item master (barang_id) ATAU item bebas (item_nama —
      tersimpan ke katalog penawaran, tidak menyentuh master data).
    - TANPA jurnal, TANPA workflow (status DRAFT).
    """
    try:
        biaya_data = biaya_data or []

        # Validasi pelanggan (mirror create_sales_order)
        pelanggan = db.query(Pelanggan).filter(Pelanggan.id == pelanggan_id).first()
        if not pelanggan:
            raise ValueError(f"Pelanggan dengan ID {pelanggan_id} tidak ditemukan")

        # Update #031 — singkatan LOCK dari master (payload diabaikan);
        # No. PO customer field dihapus dari form (param tetap diterima untuk
        # backward compat payload lama).
        customer_code = (getattr(pelanggan, "singkatan", None) or "").strip() or None
        customer_po_number = (customer_po_number or "").strip() or None

        # Update #031 — validasi & resolusi bulan/tahun nomor Quotation
        bulan_final, tahun_final = _validate_bulan_tahun(bulan, tahun, tanggal)

        # sub_total per baris + normalisasi baris (master / bebas)
        _normalize_details(details_data)
        for d in details_data:
            if not d.get("barang_id"):
                _normalize_free_item(d)

        # Generate No Quotation (urut per pelanggan, reset tahunan)
        no_penawaran = _generate_no_quotation(db, pelanggan, bulan_final, tahun_final)

        # Buat header
        penawaran = Penawaran(
            no_penawaran=no_penawaran,
            tanggal=tanggal,
            berlaku_hingga=berlaku_hingga,
            pelanggan_id=pelanggan_id,
            syarat_bayar_id=syarat_bayar_id,
            alamat_pengiriman=alamat_pengiriman,
            keterangan=keterangan,
            customer_po_number=customer_po_number,
            customer_code=customer_code,
            mata_uang=mata_uang,
            diskon_global=diskon_global,
            ppn=ppn,
            status="DRAFT",
            created_by=created_by,
        )
        db.add(penawaran)
        db.flush()

        # Buat detail (master / bebas) + upsert katalog utk item bebas
        for d in details_data:
            katalog_id = None
            if not d.get("barang_id"):
                katalog = _upsert_katalog(db, d["item_nama"], d.get("item_satuan"), safe_decimal(d.get("harga")))
                katalog_id = katalog.id
            db.add(PenawaranDetail(
                penawaran_id=penawaran.id,
                barang_id=d.get("barang_id") or None,
                katalog_id=katalog_id,
                item_nama=d.get("item_nama"),
                item_satuan=d.get("item_satuan"),
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


def _normalize_customer_code(customer_code: str | None) -> str | None:
    """Normalisasi + validasi singkatan customer (mirror create_sales_order):
    strip; tanpa karakter '/'; maksimal 20 karakter."""
    code = (customer_code or "").strip() or None
    if code:
        if "/" in code:
            raise ValueError("Singkatan customer tidak boleh mengandung karakter '/'")
        if len(code) > 20:
            raise ValueError("Singkatan customer maksimal 20 karakter")
    return code


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
    customer_po_number: str | None = None,
    customer_code: str | None = None,
    mata_uang: str | None = None,
    diskon_global: Decimal | None = None,
    ppn: Decimal | None = None,
    details: list | None = None,
    biaya_tambahan: list | None = None,
) -> Penawaran:
    """Update penawaran — hanya status DRAFT (revisi = edit DRAFT, opsi user #031).

    Full update: field header + (opsional) replace seluruh baris detail dan
    biaya tambahan (pola mirror update_penerimaan). Totals dihitung ulang
    via refresh_totals.

    Update #031:
    - No Quotation IMMUTABLE setelah create (bulan/tahun tidak diupdate).
    - Pelanggan TIDAK bisa diganti — singkatan adalah segmen nomor Quotation;
      ganti pelanggan = buat penawaran baru.
    - Singkatan customer_code dari payload diabaikan (terkunci master).
    """
    if db_obj.status != "DRAFT":
        raise ValueError(f"Penawaran dengan status {db_obj.status} tidak bisa diupdate")

    # Update #031 — pelanggan terkunci (segmen nomor Quotation)
    if pelanggan_id is not None and pelanggan_id != db_obj.pelanggan_id:
        raise ValueError(
            "Pelanggan tidak bisa diganti pada penawaran yang sudah dibuat — "
            "singkatan pelanggan adalah bagian dari No Quotation. Batalkan "
            "penawaran ini dan buat penawaran baru untuk pelanggan tersebut."
        )

    # Update ASAHI #003 — No. PO customer + singkatan (bisa di-clear dengan "")
    customer_code = _normalize_customer_code(customer_code)
    if customer_po_number is not None:
        db_obj.customer_po_number = (customer_po_number or "").strip() or None

    if tanggal is not None:
        db_obj.tanggal = tanggal
    if berlaku_hingga is not None:
        db_obj.berlaku_hingga = berlaku_hingga
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

    # Replace detail (bila dikirim) — master / bebas + upsert katalog
    if details is not None:
        _normalize_details(details)
        for d in details:
            if not d.get("barang_id"):
                _normalize_free_item(d)
        db_obj.details.clear()
        db.flush()
        for d in details:
            katalog_id = None
            if not d.get("barang_id"):
                katalog = _upsert_katalog(db, d["item_nama"], d.get("item_satuan"), safe_decimal(d.get("harga")))
                katalog_id = katalog.id
            db_obj.details.append(PenawaranDetail(
                barang_id=d.get("barang_id") or None,
                katalog_id=katalog_id,
                item_nama=d.get("item_nama"),
                item_satuan=d.get("item_satuan"),
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


# ═══════════════════════════════════════════════════════════════════════════
# Update #031 — konversi: item bebas → master barang (stock_item=False)
# ═══════════════════════════════════════════════════════════════════════════


def _next_kode_barang_otomatis(db: Session) -> str:
    """Kode barang otomatis "ITM-0001" (sequence berlanjut dari yang ada)."""
    rows = db.query(Barang.kode).filter(Barang.kode.like("ITM-%")).all()
    last = max(
        (int(r[0].rsplit("-", 1)[-1]) for r in rows if r[0] and r[0].rsplit("-", 1)[-1].isdigit()),
        default=0,
    )
    return f"ITM-{last + 1:04d}"


def _ensure_satuan_by_nama(db: Session, nama: str) -> Satuan:
    """Cari satuan by nama (case-insensitive); buat bila belum ada."""
    norm = (nama or "").strip()
    satuan = (
        db.query(Satuan)
        .filter(func.lower(func.trim(Satuan.nama)) == norm.lower(), Satuan.status == "AKTIF")
        .first()
    )
    if satuan is not None:
        return satuan
    satuan = Satuan(nama=norm[:20] or "PCS", status="AKTIF")
    db.add(satuan)
    db.flush()
    return satuan


def _ensure_kategori_umum(db: Session) -> KategoriBarang:
    """Kategori default "Umum" untuk barang hasil konversi item bebas."""
    kategori = (
        db.query(KategoriBarang)
        .filter(func.lower(func.trim(KategoriBarang.nama)) == "umum", KategoriBarang.status == "AKTIF")
        .first()
    )
    if kategori is not None:
        return kategori
    kategori = KategoriBarang(kode="UMUM", nama="Umum", status="AKTIF")
    db.add(kategori)
    db.flush()
    return kategori


def _ensure_barang_dari_item_bebas(db: Session, d: PenawaranDetail) -> Barang:
    """Buat master barang dari baris item bebas saat konversi ke SO — #031.

    Item bebas tidak pernah menyentuh master selama masih penawaran; saat
    customer SETUJU (order nyata), item dibuatkan di master supaya SO →
    pengiriman → invoice bisa berjalan normal:
    - stock_item=False (bukan barang stok — jasa/non-stok, tidak mengganggu
      gudang), metode valuasi AVERAGE, kategori "Umum".
    - satuan dari teks bebas baris (dicari/dibuat by nama; fallback PCS).
    - harga jual = harga penawaran.
    - kode otomatis ITM-0001 (berurutan).
    """
    nama = (d.item_nama or "").strip()
    if not nama:
        raise ValueError("Baris item bebas tanpa nama tidak bisa dikonversi")
    satuan = _ensure_satuan_by_nama(db, d.item_satuan or "PCS")
    kategori = _ensure_kategori_umum(db)
    barang = Barang(
        kode=_next_kode_barang_otomatis(db),
        nama=nama[:200],
        kategori_id=kategori.id,
        satuan_id=satuan.id,
        metode_valuasi=MetodeValuasi.AVERAGE,
        status="AKTIF",
        stock_item=False,  # item bebas penawaran — bukan barang stok
        harga_jual=d.harga or Decimal("0"),
        harga_pokok=Decimal("0"),
    )
    db.add(barang)
    db.flush()
    return barang


@atomic_accounting_write
def convert_to_so(db: Session, penawaran_id: UUID, user):
    """Konversi Penawaran menjadi Sales Order (Update #3, revisi #031).

    - Guard: hanya penawaran berstatus DRAFT / DIPROSES.
    - SO dibuat lewat create_sales_order lalu difinalisasi dengan
      workflow_service.direct_complete FORCE=True (Update #031: SO langsung
      final tanpa langkah approval untuk SEMUA user — bukan hanya admin).
    - Copy: tanggal = now, pelanggan, syarat_bayar, alamat_pengiriman,
      keterangan = "Dari Penawaran {no}" (+ keterangan lama), diskon_global,
      ppn, details (harga/qty/diskon/satuan), biaya_tambahan.
    - Update #031: baris item BEBAS otomatis dibuatkan master barang
      (stock_item=False) sebelum masuk SO — SO tetap berisi barang nyata.
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

    details_data = []
    for d in penawaran.details:
        # Update #031 — item bebas → master barang (stock_item=False)
        barang_id = d.barang_id
        if not barang_id:
            barang = _ensure_barang_dari_item_bebas(db, d)
            barang_id = barang.id
        details_data.append(
            {
                "barang_id": barang_id,
                "satuan_id": d.satuan_id,
                "qty": d.qty,
                "harga": d.harga,
                "diskon": d.diskon if d.diskon is not None else Decimal("0"),
                "sub_total": d.sub_total,
            }
        )
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
        # Update ASAHI #003 — No. PO customer + singkatan dibawa ke SO
        # (Update #031: singkatan tetap snapshot master saat penawaran dibuat;
        # form penawaran tidak lagi punya field No. PO — nilai umumnya kosong).
        customer_po_number=penawaran.customer_po_number,
        customer_code=penawaran.customer_code,
        created_by=getattr(user, "id", None) or penawaran.created_by,
        currency=penawaran.mata_uang,
    )

    # Finalisasi (Update #031): FORCE untuk semua user — SO langsung jadi
    # tanpa langkah approval (submit → approve; order terminal APPROVED,
    # tanpa posting jurnal).
    workflow_service.direct_complete(db, user, "sales_order", so.id, force=True)

    # Tandai penawaran selesai
    penawaran.status = "SELESAI"
    db.add(penawaran)
    db.commit()
    db.refresh(so)
    logger.info(
        f"Penawaran {penawaran.no_penawaran} converted to SalesOrder {so.no_pesanan}"
    )
    return so
