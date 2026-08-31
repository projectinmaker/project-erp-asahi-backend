"""
Asahi ERP - Import COA dari Excel

Script ini melakukan:
1. Hapus semua data COA lama (akun_perkiraan + kas_bank_akun)
2. Baca COA dari file Excel (format dotted: 100.000.001)
3. Import ke database dengan hierarchy yang benar
4. Re-seed KasBankAkun (KAS & BANK) berdasarkan COA baru

Usage:
    cd Backend
    python -m app.seed.import_coa_from_excel
    # atau
    python app/seed/import_coa_from_excel.py
"""

import sys
import os
from collections import Counter
from decimal import Decimal

import openpyxl
from sqlalchemy.orm import Session
from sqlalchemy import text

# Tambahkan root project ke path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.database import SessionLocal, engine
from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, TingkatAkun, SaldoNormal
from app.models.master.kas_bank_akun import KasBankAkun, JenisKasBank


# ============================================================
# KONFIGURASI
# ============================================================

# Path ke file Excel COA — bisa di-override via CLI:
#   python app/seed/import_coa_from_excel.py /path/to/COA_ASAHI_push_erp.xlsx
DEFAULT_EXCEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "COA_ASAHI_push_erp.xlsx"
)

# ============================================================
# MAPPING: Kode prefix → HeaderCOA enum
# ============================================================
# Berdasarkan struktur Excel:
#   001.x = ASET (AKTIVA)
#   003.x = EKUITAS (MODAL)
#   004.x = IKHTISAR LABA RUGI
#   1xx   = AKTIVA (Aktiva Lancar + Tetap)
#   2xx   = KEWAJIBAN
#   3xx   = MODAL
#   4xx   = PENDAPATAN
#   5xx   = HPP
#   6xx   = BEBAN

TOP_LEVEL_MAP = {
    "001": HeaderCOA.AKTIVA,   # ASET
    "100": HeaderCOA.AKTIVA,   # Kas & Setara Kas
    "110": HeaderCOA.AKTIVA,   # Bank
    "111": HeaderCOA.AKTIVA,   # Piutang Usaha
    "130": HeaderCOA.AKTIVA,   # Piutang Lainnya
    "131": HeaderCOA.AKTIVA,   # Persediaan
    "132": HeaderCOA.AKTIVA,   # Uang Muka Pembelian
    "150": HeaderCOA.AKTIVA,   # Pajak Dibayar Dimuka
    "160": HeaderCOA.AKTIVA,   # Biaya Dibayar Dimuka
    "180": HeaderCOA.AKTIVA,   # Aset Tetap
    "181": HeaderCOA.AKTIVA,   # Akumulasi Penyusutan
    "210": HeaderCOA.AKTIVA,   # Uang Muka Penjualan
    "220": HeaderCOA.KEWAJIBAN,  # Hutang Usaha
    "230": HeaderCOA.KEWAJIBAN,  # Hutang Pajak
    "240": HeaderCOA.KEWAJIBAN,  # Hutang Biaya
    "250": HeaderCOA.KEWAJIBAN,  # Hutang Pemegang Saham
    "260": HeaderCOA.KEWAJIBAN,  # Hutang Afiliasi
    "270": HeaderCOA.KEWAJIBAN,  # Hutang Asset
    "280": HeaderCOA.KEWAJIBAN,  # Hutang Bank
    "290": HeaderCOA.KEWAJIBAN,  # Hutang Jangka Panjang
    "003": HeaderCOA.MODAL,    # EKUITAS
    "300": HeaderCOA.MODAL,    # Modal Saham
    "310": HeaderCOA.MODAL,    # Saldo Laba
    "004": HeaderCOA.PENDAPATAN,  # IKHTISAR LABA RUGI (virtual)
    "401": HeaderCOA.PENDAPATAN,  # Pendapatan Usaha
    "402": HeaderCOA.PENDAPATAN,  # Pendapatan & Biaya Luar Usaha
    "510": HeaderCOA.HPP,      # Harga Pokok Penjualan
    "520": HeaderCOA.HPP,      # Biaya Penjualan
    "610": HeaderCOA.BEBAN,    # Beban Administrasi & Umum
    "620": HeaderCOA.BEBAN,    # Beban Gaji
    "630": HeaderCOA.BEBAN,    # Beban Perawatan
    "640": HeaderCOA.BEBAN,    # Beban Penyusutan
    "650": HeaderCOA.BEBAN,    # Beban Pajak
    "660": HeaderCOA.BEBAN,    # Beban Bunga & Admin Bank
}

# Prefix yang merupakan "virtual grouping" (001, 003, 004) — tidak masuk DB
VIRTUAL_PREFIXES = {"001", "003", "004"}

# SaldoNormal berdasarkan HeaderCOA
SALDO_NORMAL_MAP = {
    HeaderCOA.AKTIVA: SaldoNormal.DEBIT,
    HeaderCOA.KEWAJIBAN: SaldoNormal.KREDIT,
    HeaderCOA.MODAL: SaldoNormal.KREDIT,
    HeaderCOA.PENDAPATAN: SaldoNormal.KREDIT,
    HeaderCOA.HPP: SaldoNormal.DEBIT,
    HeaderCOA.BEBAN: SaldoNormal.DEBIT,
}


# ============================================================
# STEP 0: Baca Excel
# ============================================================
def read_excel(filepath: str):
    """
    Baca file Excel COA dan kembalikan list of dict:
    [{"row": int, "kode": str, "nama": str, "is_header": bool}, ...]
    """
    if not os.path.exists(filepath):
        print(f"ERROR: File Excel tidak ditemukan: {filepath}")
        print(f"Pastikan file 'COA_ASAHI_push_erp.xlsx' ada di folder Backend/")
        sys.exit(1)

    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active

    rows = []
    for i, row in enumerate(ws.iter_rows(min_row=7, values_only=True), start=7):
        kode, nama, flag = row
        if kode is None:
            continue
        kode = str(kode).strip()
        if not kode:
            continue
        nama = str(nama).strip() if nama else ""
        is_header = bool(flag and str(flag).strip() and "induk" in str(flag).lower())
        rows.append({
            "row": i,
            "kode": kode,
            "nama": nama,
            "is_header": is_header,
        })

    return rows


# ============================================================
# STEP 1: Hapus Data Lama
# ============================================================
def delete_old_data(db: Session):
    """
    Hapus semua data KasBankAkun dan AkunPerkiraan yang lama.
    Urutan: KasBankAkun dulu (punya FK ke AkunPerkiraan), baru AkunPerkiraan.
    """
    print("\n" + "=" * 60)
    print("STEP 1: HAPUS DATA LAMA")
    print("=" * 60)

    # 1a. Hapus KasBankAkun (punya FK ke akun_perkiraan)
    count_kb = db.query(KasBankAkun).count()
    if count_kb > 0:
        db.query(KasBankAkun).delete()
        print(f"  [OK] Hapus {count_kb} records dari kas_bank_akun")
    else:
        print(f"  [SKIP] kas_bank_akun sudah kosong")

    # 1b. Hapus semua AkunPerkiraan
    count_coa = db.query(AkunPerkiraan).count()
    if count_coa > 0:
        db.query(AkunPerkiraan).delete()
        print(f"  [OK] Hapus {count_coa} records dari akun_perkiraan")
    else:
        print(f"  [SKIP] akun_perkiraan sudah kosong")

    db.flush()
    print(f"  Data lama berhasil dihapus.")


# ============================================================
# STEP 2: Tentukan Hierarchy & Mapping
# ============================================================
def build_hierarchy(rows: list) -> list:
    """
    Dari flat list rows, bangun hierarchy:
    - Tentukan parent untuk setiap row
    - Tentukan header_coa, tingkat, saldo_normal
    - Lewati virtual grouping rows (001, 003, 004)
    - Lewati rows dengan nama kosong

    Returns: list of dict siap insert ke DB
    """
    print("\n" + "=" * 60)
    print("STEP 2: ANALISIS HIERARCHY DARI EXCEL")
    print("=" * 60)

    # Track kode → index untuk parent lookup
    kode_index = {}
    # Track "current section" untuk non-header detail rows
    # yang parent-nya perlu ditempatkan di bawah header terdekat
    current_parent_kode = None

    # Mapping untuk induk: key=detail kode, value=parent kode
    parent_map = {}

    # First pass: build parent relationships
    for item in rows:
        kode = item["kode"]
        parts = kode.split(".")
        prefix1 = parts[0]
        is_header = item["is_header"]

        # Skip virtual grouping
        if prefix1 in VIRTUAL_PREFIXES:
            kode_index[kode] = item
            continue

        # Skip rows with empty nama (akan di-skip saat insert juga)
        if not item["nama"]:
            kode_index[kode] = item
            continue

        if is_header:
            # Header row: cari parent-nya
            # Level 1 header: 3rd part = 000, 2nd part != 000 → parent is the section above
            # Level 2 header: 3rd part = 000, 2nd part = 000 → top-level header

            # Strategy: cari parent berdasarkan closest ancestor yang ada di kode_index
            parent_kode = None

            if parts[2] == "000" and parts[1] == "000":
                # Top-level header (e.g., 100.000.000 Kas dan Setara Kas)
                # Parent = the virtual section (001.100.000 or 001.200.000)
                # Atau if no virtual section, no parent
                # Check if there's a virtual grouping above
                for prev_item in reversed(rows[:rows.index(item)]):
                    prev_parts = prev_item["kode"].split(".")
                    if prev_parts[0] in VIRTUAL_PREFIXES:
                        # This is a virtual grouping, use it as reference but don't set as parent
                        break
                parent_kode = None  # Top-level headers have no parent in DB

            elif parts[2] == "000" and parts[1] != "000":
                # Sub-header (e.g., 510.001.000 Biaya Overhead Pabrik)
                # Parent = the main header with same prefix1 and parts[1]=000
                # e.g., 510.001.000 parent = 510.000.000
                candidate = f"{parts[0]}.000.000"
                if candidate in kode_index and kode_index[candidate]["nama"]:
                    parent_kode = candidate
                else:
                    parent_kode = None

            parent_map[kode] = parent_kode
            current_parent_kode = kode
            kode_index[kode] = item

        else:
            # Detail row
            # Parent = header dengan prefix1 sama dan .000.000
            # atau sub-header yang paling dekat
            parts_detail = kode.split(".")

            if parts_detail[2] != "000" and parts_detail[1] == "000":
                # Detail di bawah top-level header: e.g., 100.000.001 → parent 100.000.000
                candidate = f"{parts_detail[0]}.000.000"
                if candidate in kode_index:
                    parent_kode = candidate
                else:
                    parent_kode = current_parent_kode
            elif parts_detail[2] != "000" and parts_detail[1] != "000":
                # Detail di bawah sub-header: e.g., 510.001.001 → parent 510.001.000
                candidate = f"{parts_detail[0]}.{parts_detail[1]}.000"
                if candidate in kode_index:
                    parent_kode = candidate
                else:
                    candidate2 = f"{parts_detail[0]}.000.000"
                    parent_kode = candidate2 if candidate2 in kode_index else current_parent_kode
            elif parts_detail[1] != "000" and parts_detail[2] == "000":
                # Ini sebenarnya header tanpa flag (some in Excel)
                # Treat as GROUP, parent = top-level
                candidate = f"{parts_detail[0]}.000.000"
                parent_kode = candidate if candidate in kode_index else current_parent_kode
            else:
                parent_kode = current_parent_kode

            parent_map[kode] = parent_kode
            kode_index[kode] = item

    # Second pass: build final records
    records = []
    skipped_virtual = 0
    skipped_empty = 0

    for item in rows:
        kode = item["kode"]
        nama = item["nama"]
        parts = kode.split(".")
        prefix1 = parts[0]

        # Skip virtual grouping rows
        if prefix1 in VIRTUAL_PREFIXES:
            skipped_virtual += 1
            continue

        # Skip rows with empty nama
        if not nama:
            skipped_empty += 1
            continue

        # Tentukan header_coa
        # Priority: exact prefix1 match, then check first non-virtual ancestor
        header_coa = TOP_LEVEL_MAP.get(prefix1)
        if header_coa is None:
            # Fallback: check parent chain
            parent_kode = parent_map.get(kode)
            if parent_kode:
                parent_prefix = parent_kode.split(".")[0]
                header_coa = TOP_LEVEL_MAP.get(parent_prefix)
            if header_coa is None:
                print(f"  [WARNING] Tidak bisa tentukan header untuk {kode} - {nama}, skip")
                continue

        # Tentukan tingkat
        is_header = item["is_header"]
        if is_header:
            # Header: cek apakah top-level (parts[1]==000) atau sub (parts[1]!=000)
            if parts[1] == "000":
                tingkat = TingkatAkun.HEADER
            else:
                tingkat = TingkatAkun.GROUP
        else:
            tingkat = TingkatAkun.DETAIL

        # Tentukan saldo_normal
        saldo_normal = SALDO_NORMAL_MAP[header_coa]

        records.append({
            "kode": kode,
            "nama": nama,
            "header": header_coa,
            "tingkat": tingkat,
            "parent_kode": parent_map.get(kode),
            "saldo_normal": saldo_normal,
        })

    print(f"  Total rows dari Excel  : {len(rows)}")
    print(f"  Dilewat (virtual)     : {skipped_virtual}")
    print(f"  Dilewat (nama kosong) : {skipped_empty}")
    print(f"  Records siap import   : {len(records)}")

    # Summary per header
    header_counts = Counter(r["header"].value for r in records)
    for h, c in sorted(header_counts.items()):
        print(f"    {h:12s}: {c} akun")

    return records


# ============================================================
# STEP 3: Insert ke Database
# ============================================================
def insert_coa(db: Session, records: list):
    """
    Insert semua records ke akun_perkiraan table.
    Urutan: insert dulu semua, lalu update induk_id.
    """
    print("\n" + "=" * 60)
    print("STEP 3: INSERT DATA COA KE DATABASE")
    print("=" * 60)

    # Map kode → AkunPerkiraan object untuk parent lookup
    kode_to_akun = {}

    # Insert semua records (tanpa induk_id dulu)
    for rec in records:
        akun = AkunPerkiraan(
            kode=rec["kode"],
            nama=rec["nama"],
            header=rec["header"],
            tingkat=rec["tingkat"],
            saldo_normal=rec["saldo_normal"],
            saldo=Decimal("0"),
            status="AKTIF",
            induk_id=None,
            induk_kode=rec["parent_kode"],
        )
        db.add(akun)
        kode_to_akun[rec["kode"]] = akun

    db.flush()  # Flush agar semua dapat ID

    # Update induk_id berdasarkan parent_kode
    updated_parent = 0
    for rec in records:
        parent_kode = rec["parent_kode"]
        if parent_kode and parent_kode in kode_to_akun:
            akun = kode_to_akun[rec["kode"]]
            parent_akun = kode_to_akun[parent_kode]
            akun.induk_id = parent_akun.id
            updated_parent += 1

    db.flush()

    print(f"  [OK] Inserted {len(records)} akun perkiraan")
    print(f"  [OK] Updated {updated_parent} parent relationships")

    # Verify
    total = db.query(AkunPerkiraan).count()
    headers = db.query(AkunPerkiraan).filter(AkunPerkiraan.tingkat == TingkatAkun.HEADER).count()
    groups = db.query(AkunPerkiraan).filter(AkunPerkiraan.tingkat == TingkatAkun.GROUP).count()
    details = db.query(AkunPerkiraan).filter(AkunPerkiraan.tingkat == TingkatAkun.DETAIL).count()
    print(f"  Verifikasi: {total} total ({headers} HEADER + {groups} GROUP + {details} DETAIL)")


# ============================================================
# STEP 4: Re-Seed KasBankAkun
# ============================================================
KAS_BANK_MAPPING = [
    # (kode_coa_dotted, kode_kas_bank, nama_display, jenis)
    # NOTE: Kas Kecil & Kas Besar dihapus per Phase 1 (meeting 25 Agt 2026)
    ("110.000.001", "BK-001", "Bank BNI 2762", JenisKasBank.BANK),
    ("110.000.002", "BK-002", "Bank Mandiri 7269", JenisKasBank.BANK),
    ("110.000.003", "BK-003", "Bank BSI 7032", JenisKasBank.BANK),
    ("110.000.004", "BK-004", "Bank BRI 2307", JenisKasBank.BANK),
    ("110.000.005", "BK-005", "Bank BCA 7777", JenisKasBank.BANK),
    ("110.000.006", "BK-006", "Bank BSI 4562", JenisKasBank.BANK),
]


def seed_kas_bank_akun(db: Session):
    """
    Seed KasBankAkun berdasarkan COA baru (kode dotted format).
    """
    print("\n" + "=" * 60)
    print("STEP 4: RE-SEED KAS BANK AKUN")
    print("=" * 60)

    inserted = 0
    for kode_coa, kode_kb, nama, jenis in KAS_BANK_MAPPING:
        coa = db.query(AkunPerkiraan).filter_by(kode=kode_coa).first()
        if not coa:
            print(f"  [WARNING] COA {kode_coa} tidak ditemukan, skip {nama}")
            continue

        kb = KasBankAkun(
            kode=kode_kb,
            nama=nama,
            jenis=jenis,
            akun_perkiraan_id=coa.id,
            saldo=Decimal("0"),
            status="AKTIF",
        )
        db.add(kb)
        inserted += 1
        print(f"  [+] {kode_kb:8s} | {kode_coa:15s} | {nama:25s} | {jenis.value}")

    db.flush()
    print(f"  [OK] Inserted {inserted} KasBankAkun records")


# ============================================================
# MAIN
# ============================================================
def main():
    print("\n" + "#" * 60)
    print("#  ASAHI ERP - IMPORT COA FROM EXCEL")
    print("#" * 60)
    # Determine Excel path
    if len(sys.argv) > 1:
        excel_path = sys.argv[1]
    else:
        excel_path = DEFAULT_EXCEL_PATH
    print(f"  Excel file : {excel_path}")
    print(f"  File exists: {os.path.exists(excel_path)}")
    if not os.path.exists(excel_path):
        print(f"  ERROR: File tidak ditemukan!")
        print(f"  Usage: python app/seed/import_coa_from_excel.py [path/to/COA_ASAHI_push_erp.xlsx]")
        sys.exit(1)

    db: Session = SessionLocal()
    try:
        # Step 0: Baca Excel
        print("\n" + "=" * 60)
        print("STEP 0: BACA FILE EXCEL")
        print("=" * 60)
        rows = read_excel(excel_path)
        print(f"  [OK] Baca {len(rows)} baris dari Excel")

        # Step 1: Hapus data lama
        delete_old_data(db)

        # Step 2: Analisis hierarchy
        records = build_hierarchy(rows)

        if len(records) == 0:
            print("\n  ERROR: Tidak ada data yang akan diimport!")
            return

        # Step 3: Insert ke database
        insert_coa(db, records)

        # Step 4: Re-seed KasBankAkun
        seed_kas_bank_akun(db)

        # Commit semua
        db.commit()

        print("\n" + "#" * 60)
        print("#  IMPORT BERHASIL!")
        print("#" * 60)
        total_coa = db.query(AkunPerkiraan).count()
        total_kb = db.query(KasBankAkun).count()
        print(f"  Total Akun Perkiraan : {total_coa}")
        print(f"  Total Kas/Bank Akun  : {total_kb}")

    except Exception as e:
        db.rollback()
        print(f"\n  [ERROR] Import gagal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
