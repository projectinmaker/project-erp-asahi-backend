"""
nomor_dokumen.py

Utility untuk generate nomor dokumen otomatis.
Format: PREFIX-YYYY-MM-NNN (contoh: INV-2026-08-001)
Menggunakan dependency injection (db: Session) bukan langsung SessionLocal.

=== Update ASAHI #019 — de-hardcode konfigurasi penomoran ===
Segmen yang sebelumnya hardcoded kini dibaca dari app_setting (menu
Preferensi → Penomoran Dokumen; registry lengkap di
app/services/preferensi_service.py):
- Kode perusahaan "ASI"        → get_kode_perusahaan(db)
  (key KODE_PERUSAHAAN_NOMOR, default "ASI")
- Prefix per jenis dokumen     → resolve_prefix(db, doc_type, default)
  (key PREFIX_{doc_type}, fallback = argumen `prefix` pemanggil).
Pemanggil lama tetap kompatibel (doc_type optional); selama setting belum
diubah, nomor yang dihasilkan IDENTIK dengan perilaku lama.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.services import app_setting_service

# === Update ASAHI #019 — key app_setting untuk penomoran dokumen ===
KEY_KODE_PERUSAHAAN_NOMOR = "KODE_PERUSAHAAN_NOMOR"
DEFAULT_KODE_PERUSAHAAN = "ASI"


# Mapping doc_type ke prefix default — referensi tunggal (Update ASAHI #019:
# dilengkapi agar konsisten dengan nilai yang dipakai call site + registry
# preferensi; nilai = perilaku default saat ini, pengguna dapat meng-override
# per doc_type lewat Preferensi → Penomoran Dokumen).
default_prefix_map = {
    # Entri lama (dipertahankan untuk kompatibilitas/dokumentasi historis)
    "INVOICE": "INV",
    "PAYMENT": "PAY",
    "RETUR_PEMBELAN": "RET-PB",  # (typo historis, dipertahankan apa adanya)
    "RETUR_BARANG": "RET-BRG",
    "TRANSFER_KAS": "TK",
    "MANUAL_JURNAL": "JV",
    # Entri selaras call site (Update ASAHI #019)
    "SALES_ORDER": "SO",
    "PENAWARAN": "PEN",
    "PENGIRIMAN_BARANG": "DO",  # Update ASAHI #3: dulu "KB" (Kartu/Bukti) — kini Delivery Order
    "SALES_INVOICE": "INV",
    "RETUR_PENJUALAN": "RET-J",
    "TUKAR_FAKTUR": "TF",
    "PURCHASE_ORDER": "PO",
    "PENERIMAAN_BARANG": "PB",
    "PURCHASE_INVOICE": "PINV",
    "RETUR_PEMBELIAN": "RET-B",
    "LPPB": "LPPB",
    "REQUEST_ORDER_PART": "ROP",
    "PENYESUAIAN_STOK": "ADJ",  # dulu "PS" (stale — call site selalu memakai "ADJ")
    "PEMINDAHAN_BARANG": "TRF-STK",
    "PERMINTAAN_BARANG": "REQ",
    "PEMBAYARAN": "PAY",
    "PEMBAYARAN_LAIN": "VC",
    "PENERIMAAN": "REC",
    "PENERIMAAN_LAIN": "PB",
    "TRANSFER_BANK": "TRF",
    "JURNAL_MANUAL": "JV",
}


def get_kode_perusahaan(db: Session) -> str:
    """Kode perusahaan — segmen kedua nomor dokumen (Update ASAHI #019).

    Baca key KODE_PERUSAHAAN_NOMOR dari app_setting (Preferensi → Penomoran
    Dokumen); fallback "ASI" (nilai hardcoded lama) bila belum dikonfigurasi.
    Normalisasi trim + UPPERCASE agar segmen nomor selalu rapi.
    """
    raw = app_setting_service.get_setting(db, KEY_KODE_PERUSAHAAN_NOMOR)
    kode = (raw or DEFAULT_KODE_PERUSAHAAN).strip().upper()
    return kode or DEFAULT_KODE_PERUSAHAAN


def resolve_prefix(db: Session, doc_type: str, default: str) -> str:
    """Prefix nomor dokumen per jenis dokumen (Update ASAHI #019).

    Baca key PREFIX_{doc_type} dari app_setting (Preferensi → Penomoran
    Dokumen); fallback `default` bila belum dikonfigurasi. Normalisasi
    trim + UPPERCASE (registry preferensi juga menormalkan saat menyimpan).
    """
    raw = app_setting_service.get_setting(db, f"PREFIX_{doc_type}")
    prefix = (raw if raw is not None else default).strip().upper()
    return prefix or default


def get_nomor_dokumen(
    db: Session,
    model_class,
    prefix: str,
    no_column: str = "nomor",
    tanggal: date | None = None,
    doc_type: str | None = None,
) -> str:
    """
    Generate nomor dokumen otomatis dengan format: PREFIX-YYYY-MM-NNN.

    Parameter:
        db: SQLAlchemy Session (dependency injection)
        model_class: Model SQLAlchemy untuk mencari nomor terakhir
        prefix: Prefix dokumen (INV, PAY, SO, PO, dll) — dipakai sebagai
            fallback bila doc_type diberikan namun setting belum ada
        no_column: Nama kolom nomor di model (default: "nomor")
        tanggal: Tanggal dokumen (default: hari ini)
        doc_type: Jenis dokumen (optional — Update ASAHI #019). Bila diisi,
            prefix efektif = resolve_prefix(db, doc_type, prefix) sehingga
            pengguna dapat mengubah prefix lewat Preferensi → Penomoran.

    Return:
        String nomor dokumen, contoh: "INV-2026-08-001"
    """
    if tanggal is None:
        tanggal = date.today()

    # === Update ASAHI #019 — prefix dapat di-override via preferensi ===
    if doc_type:
        prefix = resolve_prefix(db, doc_type, prefix)

    month_str = tanggal.strftime("%Y-%m")
    pattern = f"{prefix}-{month_str}%"

    from app.services.accounting_control import accounting_lock
    accounting_lock(db)
    numbers = db.query(getattr(model_class, no_column)).filter(getattr(model_class, no_column).like(pattern)).all()
    last_seq = max((int(row[0].rsplit('-', 1)[-1]) for row in numbers
                    if row[0] and row[0].rsplit('-', 1)[-1].isdigit()), default=0)

    next_seq = last_seq + 1
    return f"{prefix}-{month_str}-{next_seq:03d}"


def get_nomor_dokumen_tahunan(
    db: Session,
    model_class,
    prefix: str,
    no_column: str = "nomor",
    tanggal: date | None = None,
    customer_code: str | None = None,
    doc_type: str | None = None,
) -> str:
    """
    Generate nomor dokumen otomatis dengan format ASAHI:
    "<PREFIX> ASI/YYYY/NNN" (contoh: "SO ASI/2026/001", "PO ASI/2026/001").

    Update ASAHI (revisi Sales Order): bila `customer_code` diberikan, format
    menjadi "<PREFIX> ASI/<CODE>/YYYY/NNN" — segmen singkatan nama perusahaan
    customer (input manual, bebas):
        customer_code="TBI" → "SO ASI/TBI/2026/001"
        kosong/None        → "SO ASI/-/2026/001"

    Update ASAHI #019 (de-hardcode): segmen "ASI" kini dibaca
    get_kode_perusahaan(db) (key KODE_PERUSAHAAN_NOMOR) dan prefix dapat
    di-override per doc_type lewat resolve_prefix — default identik dengan
    perilaku lama selama setting belum diubah.

    Perilaku:
    - Sequence RESET ke 001 setiap ganti tahun (reset tahunan).
    - Sequence TERPISAH per prefix (SO dan PO masing-masing berurutan sendiri)
      dan BERLANJUT dari nomor lama tanpa segmen code pada tahun yang sama
      (pattern LIKE juga men-match "SO ASI/2026/001" → tidak ada nomor ganda).
    - Dokumen lama berformat PREFIX-YYYY-MM-NNN tidak ter-match pattern ini,
      sehingga migrasi dimulai bersih dari 001 pada tahun berjalan.

    Parameter:
        db: SQLAlchemy Session (dependency injection)
        model_class: Model SQLAlchemy untuk mencari nomor terakhir
        prefix: Prefix dokumen ("SO" / "PO") — fallback bila doc_type
            diberikan namun setting PREFIX_{doc_type} belum ada
        no_column: Nama kolom nomor di model (default: "nomor")
        tanggal: Tanggal dokumen (default: hari ini)
        customer_code: Singkatan customer (khusus SO; optional). Karakter "/"
            tidak boleh dipakai (divalidasi service) agar segmen nomor bersih.
        doc_type: Jenis dokumen (optional — Update ASAHI #019); lihat
            get_nomor_dokumen.

    Return:
        String nomor dokumen, contoh: "SO ASI/2026/001" / "SO ASI/TBI/2026/001"
    """
    if tanggal is None:
        tanggal = date.today()

    year_str = tanggal.strftime("%Y")

    # === Update ASAHI #019 — de-hardcode penomoran ===
    # Prefix per jenis dokumen + kode perusahaan dibaca dari app_setting
    # (Preferensi → Penomoran Dokumen); fallback = nilai lama ("ASI" dan
    # argumen prefix pemanggil).
    if doc_type:
        prefix = resolve_prefix(db, doc_type, prefix)
    kode = get_kode_perusahaan(db)

    from app.services.accounting_control import accounting_lock
    accounting_lock(db)

    if customer_code is not None:
        # Format baru (Sales Order): "SO ASI/<CODE>/YYYY/NNN".
        # Pattern sengaja "<PREFIX> <KODE>%/<tahun>/%" (segmen kode perusahaan
        # lalu wildcard) supaya nomor lama TANPA segmen code ("SO ASI/2026/001")
        # tetap terhitung dalam sequence yang sama.
        code = customer_code.strip() or "-"
        pattern = f"{prefix} {kode}%/{year_str}/%"
        numbers = db.query(getattr(model_class, no_column)).filter(getattr(model_class, no_column).like(pattern)).all()
        last_seq = max((int(row[0].rsplit('/', 1)[-1]) for row in numbers
                        if row[0] and row[0].rsplit('/', 1)[-1].isdigit()), default=0)
        return f"{prefix} {kode}/{code}/{year_str}/{last_seq + 1:03d}"

    pattern = f"{prefix} {kode}/{year_str}/%"
    numbers = db.query(getattr(model_class, no_column)).filter(getattr(model_class, no_column).like(pattern)).all()
    last_seq = max((int(row[0].rsplit('/', 1)[-1]) for row in numbers
                    if row[0] and row[0].rsplit('/', 1)[-1].isdigit()), default=0)

    next_seq = last_seq + 1
    return f"{prefix} {kode}/{year_str}/{next_seq:03d}"
