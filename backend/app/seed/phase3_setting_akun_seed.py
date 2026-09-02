import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from sqlalchemy import or_, inspect, text
from app.database import SessionLocal
from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, SaldoNormal, TingkatAkun
from app.models.master.setting_akun import SettingAkun


# ============================================================
# HELPER: Deteksi format kode COA di database
# ============================================================
def _detect_format(db: Session) -> str:
    """
    Deteksi apakah DB menggunakan format dotted (150.000.001)
    atau flat (150000001) berdasarkan sample data.
    """
    sample = db.query(AkunPerkiraan.kode).first()
    if sample and "." in sample[0]:
        return "dotted"
    return "flat"


def _kode(prefix3: str, mid3: str, seq3: str, fmt: str) -> str:
    """
    Generate kode COA dalam format yang sesuai.
    prefix3, mid3, seq3 masing-masing 3 digit.
    flat:    "150100001"
    dotted:  "150.100.001"
    """
    if fmt == "dotted":
        return f"{prefix3}.{mid3}.{seq3}"
    return f"{prefix3}{mid3}{seq3}"


def _find_coa(db: Session, kode_flat: str, kode_dotted: str):
    """Cari COA by kode, mencoba kedua format."""
    return (
        db.query(AkunPerkiraan)
        .filter(or_(AkunPerkiraan.kode == kode_flat, AkunPerkiraan.kode == kode_dotted))
        .first()
    )


def _find_coa_by_nama(db: Session, nama_contains: str, tingkat=None):
    """Cari COA by nama (fallback ketika kode tidak ketemu)."""
    q = db.query(AkunPerkiraan).filter(AkunPerkiraan.nama.ilike(f"%{nama_contains}%"))
    if tingkat:
        q = q.filter(AkunPerkiraan.tingkat == tingkat)
    return q.first()


def _ensure_setting_akun_table(db: Session):
    """Cek apakah tabel setting_akun ada. Kalau belum, coba create langsung."""
    inspector = inspect(db.bind)
    if 'setting_akun' in inspector.get_table_names():
        return True

    print("WARNING: Tabel 'setting_akun' belum ada di database.")
    print("  Mencoba create tabel langsung...")
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS setting_akun (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                key VARCHAR(100) NOT NULL UNIQUE,
                label VARCHAR(200) NOT NULL,
                akun_perkiraan_id UUID NOT NULL REFERENCES akun_perkiraan(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_setting_akun_key ON setting_akun(key);
        """))
        db.commit()
        print("  OK: Tabel 'setting_akun' berhasil dibuat.")
        return True
    except Exception as e:
        db.rollback()
        print(f"  ERROR: Gagal create tabel: {e}")
        print(f"  Solusi: Jalankan 'alembic upgrade head' dulu, atau 'alembic stamp e5f6a7b8c9' lalu 'alembic upgrade head'")
        return False


def seed_phase3_coa_and_settings():
    db: Session = SessionLocal()
    try:
        existing_count = db.query(AkunPerkiraan).count()
        if existing_count == 0:
            print("ERROR: COA kosong. Jalankan 'python3 -m app.seed.coa_seed' dulu.")
            return

        # ============================================================
        # 0b. ENSURE SETTING_AKUN TABLE EXISTS
        # ============================================================
        if not _ensure_setting_akun_table(db):
            return

        # ============================================================
        # 0. DETECT FORMAT
        # ============================================================
        fmt = _detect_format(db)
        print(f"Detected COA kode format: {fmt}")

        # ============================================================
        # 1. ENSURE PARENT HEADERS EXIST
        # ============================================================
        # Header yang MUNGKIN belum ada di DB (tergantung bagaimana COA di-import)
        # Format: (prefix3, nama, header_enum, saldo_normal)
        required_headers = [
            ("400", "PENDAPATAN", HeaderCOA.PENDAPATAN, SaldoNormal.KREDIT),
            ("500", "HARGA POKOK PENJUALAN", HeaderCOA.HPP, SaldoNormal.DEBIT),
            ("600", "BEBAN USAHA", HeaderCOA.BEBAN, SaldoNormal.DEBIT),
        ]

        print("\n--- Step 1: Ensure parent headers ---")
        created_headers = 0
        for prefix3, nama, header, saldo_normal in required_headers:
            kode_flat = _kode(prefix3, "000", "000", "flat")
            kode_dotted = _kode(prefix3, "000", "000", "dotted")

            parent = _find_coa(db, kode_flat, kode_dotted)
            if parent:
                print(f"  OK: {parent.kode} - {parent.nama} already exists")
                continue

            # Coba cari by nama (mungkin kode beda, e.g. Excel pakai 401, 510, 610)
            parent = _find_coa_by_nama(db, nama, TingkatAkun.HEADER)
            if parent:
                print(f"  OK: {parent.kode} - {parent.nama} found by name")
                continue

            # Juga coba cari header dengan kategori yang sama
            # (misal Excel punya 401.000.000 PENDAPATAN USAHA)
            nama_keywords = {
                HeaderCOA.PENDAPATAN: ["PENDAPATAN"],
                HeaderCOA.HPP: ["HARGA POKOK", "HPP"],
                HeaderCOA.BEBAN: ["BEBAN"],
            }
            for keyword in nama_keywords.get(header, []):
                parent = _find_coa_by_nama(db, keyword, TingkatAkun.HEADER)
                if parent and parent.header == header:
                    print(f"  OK: {parent.kode} - {parent.nama} found by keyword '{keyword}'")
                    break

            if parent:
                continue

            # Header belum ada — buat baru dengan format yang sesuai
            kode = _kode(prefix3, "000", "000", fmt)
            new_header = AkunPerkiraan(
                kode=kode,
                nama=nama,
                header=header,
                tingkat=TingkatAkun.HEADER,
                saldo_normal=saldo_normal,
                saldo=0,
                status="AKTIF",
            )
            db.add(new_header)
            created_headers += 1
            print(f"  CREATED: {kode} - {nama}")

        db.flush()
        if created_headers > 0:
            print(f"  => {created_headers} header(s) created")
        else:
            print(f"  => All parent headers already exist")

        # ============================================================
        # 2. COA TAMBAHAN UNTUK MANUFAKTUR
        # ============================================================
        # Format: (prefix3, mid3, seq3, nama, header, saldo_normal, parent_prefix3)
        # mid3 = sub-group kode (biasanya "100" untuk detail langsung di bawah header)
        # Untuk parent lookup: coba prefix3+000+000 dulu, lalu fallback by nama

        new_coa_defs = [
            # -- AKTIVA --
            # PPN Masukan (under PAJAK DIBAYAR DIMUKA 150)
            ("150", "100", "001", "PPN Masukan", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "150"),

            # Persediaan (under PERSEDIAAN 131)
            ("131", "100", "001", "Persediaan Bahan Baku", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "131"),
            ("131", "100", "002", "Persediaan Barang Dalam Proses (WIP)", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "131"),
            ("131", "100", "003", "Persediaan Barang Jadi", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "131"),
            ("131", "100", "004", "Persediaan Bahan Pembantu", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "131"),

            # -- KEWAJIBAN --
            # PPN Keluaran (under HUTANG PAJAK 230)
            ("230", "100", "001", "PPN Keluaran", HeaderCOA.KEWAJIBAN, SaldoNormal.KREDIT, "230"),

            # -- PENDAPATAN --
            ("400", "100", "001", "Pendapatan Penjualan", HeaderCOA.PENDAPATAN, SaldoNormal.KREDIT, "400"),
            ("400", "100", "002", "Retur Penjualan", HeaderCOA.PENDAPATAN, SaldoNormal.KREDIT, "400"),
            ("400", "100", "003", "Potongan Penjualan", HeaderCOA.PENDAPATAN, SaldoNormal.KREDIT, "400"),

            # -- HPP --
            ("500", "100", "001", "Harga Pokok Penjualan", HeaderCOA.HPP, SaldoNormal.DEBIT, "500"),
            ("500", "100", "002", "Persediaan Awal", HeaderCOA.HPP, SaldoNormal.DEBIT, "500"),
            ("500", "100", "003", "Pembelian", HeaderCOA.HPP, SaldoNormal.DEBIT, "500"),
            ("500", "100", "004", "Retur Pembelian", HeaderCOA.HPP, SaldoNormal.DEBIT, "500"),
            ("500", "100", "005", "Beban Angkut Pembelian", HeaderCOA.HPP, SaldoNormal.DEBIT, "500"),

            # -- BEBAN --
            ("600", "100", "001", "Beban Gaji dan Upah", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "002", "Beban Tenaga Kerja Langsung", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "003", "Beban Overhead Pabrik", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "004", "Beban Listrik dan Air", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "005", "Beban Sewa", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "006", "Beban Penyusutan", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "007", "Beban Perlengkapan", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "008", "Beban Biaya Admin", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "009", "Beban Biaya Transfer Bank", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "010", "Beban Lain-lain", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
            ("600", "100", "011", "Selisih Persediaan", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600"),
        ]

        # Nama fallback untuk parent lookup (ketika kode parent tidak ketemu)
        parent_name_fallback = {
            "150": "PAJAK DIBAYAR DIMUKA",
            "131": "PERSEDIAAN",
            "230": "HUTANG PAJAK",
            "400": "PENDAPATAN",
            "500": "HARGA POKOK",
            "600": "BEBAN",
        }

        print("\n--- Step 2: Create detail COA ---")
        inserted_coa = 0
        skipped_coa = 0

        for prefix3, mid3, seq3, nama, header, saldo_normal, parent_prefix3 in new_coa_defs:
            # Generate kode dalam kedua format untuk cek
            kode_flat = _kode(prefix3, mid3, seq3, "flat")
            kode_dotted = _kode(prefix3, mid3, seq3, "dotted")
            kode = kode_flat if fmt == "flat" else kode_dotted

            # Cek apakah kode sudah ada (cek kedua format)
            existing = _find_coa(db, kode_flat, kode_dotted)
            if existing:
                skipped_coa += 1
                continue

            # Cari parent: coba kode dulu, lalu fallback by nama
            parent_kode_flat = _kode(parent_prefix3, "000", "000", "flat")
            parent_kode_dotted = _kode(parent_prefix3, "000", "000", "dotted")
            parent = _find_coa(db, parent_kode_flat, parent_kode_dotted)

            if not parent:
                # Fallback: cari by nama
                fallback_name = parent_name_fallback.get(parent_prefix3, "")
                if fallback_name:
                    parent = _find_coa_by_nama(db, fallback_name, TingkatAkun.HEADER)
                    if not parent:
                        parent = _find_coa_by_nama(db, fallback_name, TingkatAkun.GROUP)

            if not parent:
                print(f"  WARNING: Induk {parent_prefix3} tidak ditemukan, skip {kode} - {nama}")
                skipped_coa += 1
                continue

            coa = AkunPerkiraan(
                kode=kode,
                nama=nama,
                header=header,
                tingkat=TingkatAkun.DETAIL,
                induk_id=parent.id,
                induk_kode=parent.kode,
                saldo_normal=saldo_normal,
                saldo=0,
                status="AKTIF",
            )
            db.add(coa)
            inserted_coa += 1
            print(f"  + {kode} - {nama} (parent: {parent.kode})")

        db.commit()
        print(f"\nCOA: {inserted_coa} inserted, {skipped_coa} skipped")

        # ============================================================
        # 3. SETTING AKUN DEFAULTS
        # ============================================================
        print("\n--- Step 3: Create Setting Akun ---")

        # Key -> (label, prefix3, mid3, seq3)
        # Kode COA di atas yang baru di-insert / sudah ada
        setting_defaults = [
            ("PENDAPATAN_PENJUALAN", "Pendapatan Penjualan", "400", "100", "001"),
            ("PPN_KELUARAN", "PPN Keluaran", "230", "100", "001"),
            ("PPN_MASUKAN", "PPN Masukan", "150", "100", "001"),
            ("RETUR_PENJUALAN", "Retur Penjualan", "400", "100", "002"),
            ("RETUR_PEMBELIAN", "Retur Pembelian", "500", "100", "004"),
            ("HPP_PENJUALAN", "Harga Pokok Penjualan", "500", "100", "001"),
            ("PEMBELIAN", "Pembelian", "500", "100", "003"),
            ("PERSEDIAAN_BAHAN_BAKU", "Persediaan Bahan Baku", "131", "100", "001"),
            ("PERSEDIAAN_WIP", "Persediaan Barang Dalam Proses (WIP)", "131", "100", "002"),
            ("PERSEDIAAN_BARANG_JADI", "Persediaan Barang Jadi", "131", "100", "003"),
            ("BEBAN_ANGKUT_PEMBELIAN", "Beban Angkut Pembelian", "500", "100", "005"),
            ("BEBAN_TRANSFER_BANK", "Beban Biaya Transfer Bank", "600", "100", "009"),
            ("BEBAN_ADMIN", "Beban Biaya Admin", "600", "100", "008"),
            ("PIUTANG_USAHA", "Piutang Usaha", "111", "000", "000"),
            ("HUTANG_USAHA", "Hutang Usaha", "220", "000", "000"),
            ("SELISIH_PERSEDIAAN", "Selisih Persediaan", "600", "100", "011"),
            ("PERSEDIAAN_BAHAN_PEMBANTU", "Persediaan Bahan Pembantu", "131", "100", "004"),
        ]

        inserted_setting = 0
        skipped_setting = 0

        for key, label, prefix3, mid3, seq3 in setting_defaults:
            # Cek sudah ada
            if db.query(SettingAkun).filter(SettingAkun.key == key).first():
                skipped_setting += 1
                continue

            # Cari COA (kedua format)
            kode_flat = _kode(prefix3, mid3, seq3, "flat")
            kode_dotted = _kode(prefix3, mid3, seq3, "dotted")
            coa = _find_coa(db, kode_flat, kode_dotted)

            if not coa:
                # Fallback: cari by nama
                coa = _find_coa_by_nama(db, label)

            if not coa:
                print(f"  WARNING: COA {label} tidak ditemukan, skip setting {key}")
                skipped_setting += 1
                continue

            setting = SettingAkun(
                key=key,
                label=label,
                akun_perkiraan_id=coa.id,
            )
            db.add(setting)
            inserted_setting += 1
            print(f"  + {key} -> {coa.kode} ({coa.nama})")

        db.commit()
        print(f"\nSettingAkun: {inserted_setting} inserted, {skipped_setting} skipped")
        print("Phase 3 seed complete.")

    except Exception as e:
        db.rollback()
        print(f"Error seeding Phase 3: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_phase3_coa_and_settings()
