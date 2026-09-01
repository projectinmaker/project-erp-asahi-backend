import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, SaldoNormal, TingkatAkun
from app.models.master.setting_akun import SettingAkun


def seed_phase3_coa_and_settings():
    db: Session = SessionLocal()
    try:
        existing_count = db.query(AkunPerkiraan).count()
        if existing_count == 0:
            print("ERROR: COA kosong. Jalankan 'python3 -m app.seed.coa_seed' dulu.")
            return

        # ============================================================
        # 1. COA TAMBAHAN UNTUK MANUFAKTUR
        # ============================================================

        # Struktur: (kode, nama, header, saldo_normal, induk_kode)
        # induk_kode = None artinya HEADER level
        new_coa = [
            # -- AKTIVA --
            # PAJAK DIBAYAR DIMUKA
            ("150100001", "PPN Masukan", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "150000000"),

            # PERSEDIAAN (detail di bawah 131000000)
            ("131100001", "Persediaan Bahan Baku", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "131000000"),
            ("131100002", "Persediaan Barang Dalam Proses (WIP)", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "131000000"),
            ("131100003", "Persediaan Barang Jadi", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "131000000"),
            ("131100004", "Persediaan Bahan Pembantu", HeaderCOA.AKTIVA, SaldoNormal.DEBIT, "131000000"),

            # -- KEWAJIBAN --
            # HUTANG PAJAK (detail di bawah 230000000)
            ("230100001", "PPN Keluaran", HeaderCOA.KEWAJIBAN, SaldoNormal.KREDIT, "230000000"),

            # -- PENDAPATAN --
            ("400100001", "Pendapatan Penjualan", HeaderCOA.PENDAPATAN, SaldoNormal.KREDIT, "400000000"),
            ("400100002", "Retur Penjualan", HeaderCOA.PENDAPATAN, SaldoNormal.KREDIT, "400000000"),
            ("400100003", "Potongan Penjualan", HeaderCOA.PENDAPATAN, SaldoNormal.KREDIT, "400000000"),

            # -- HPP --
            ("500100001", "Harga Pokok Penjualan", HeaderCOA.HPP, SaldoNormal.DEBIT, "500000000"),
            ("500100002", "Persediaan Awal", HeaderCOA.HPP, SaldoNormal.DEBIT, "500000000"),
            ("500100003", "Pembelian", HeaderCOA.HPP, SaldoNormal.DEBIT, "500000000"),
            ("500100004", "Retur Pembelian", HeaderCOA.HPP, SaldoNormal.DEBIT, "500000000"),
            ("500100005", "Beban Angkut Pembelian", HeaderCOA.HPP, SaldoNormal.DEBIT, "500000000"),

            # -- BEBAN --
            ("600100001", "Beban Gaji dan Upah", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100002", "Beban Tenaga Kerja Langsung", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100003", "Beban Overhead Pabrik", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100004", "Beban Listrik dan Air", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100005", "Beban Sewa", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100006", "Beban Penyusutan", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100007", "Beban Perlengkapan", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100008", "Beban Biaya Admin", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100009", "Beban Biaya Transfer Bank", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
            ("600100010", "Beban Lain-lain", HeaderCOA.BEBAN, SaldoNormal.DEBIT, "600000000"),
        ]

        inserted_coa = 0
        skipped_coa = 0

        for kode, nama, header, saldo_normal, induk_kode_str in new_coa:
            # Cek apakah kode sudah ada
            if db.query(AkunPerkiraan).filter(AkunPerkiraan.kode == kode).first():
                skipped_coa += 1
                continue

            # Cari induk
            induk = db.query(AkunPerkiraan).filter(AkunPerkiraan.kode == induk_kode_str).first()
            if not induk:
                print(f"  WARNING: Induk {induk_kode_str} tidak ditemukan, skip {kode} - {nama}")
                skipped_coa += 1
                continue

            coa = AkunPerkiraan(
                kode=kode,
                nama=nama,
                header=header,
                tingkat=TingkatAkun.DETAIL,
                induk_id=induk.id,
                induk_kode=induk.kode,
                saldo_normal=saldo_normal,
                saldo=0,
                status="AKTIF",
            )
            db.add(coa)
            inserted_coa += 1

        db.commit()
        print(f"COA: {inserted_coa} inserted, {skipped_coa} skipped (already exists)")

        # ============================================================
        # 2. SETTING AKUN DEFAULTS
        # ============================================================

        # Key -> (label, kode_coa)
        # Kode COA di atas yang sudah di-insert
        setting_defaults = [
            ("PENDAPATAN_PENJUALAN", "Pendapatan Penjualan", "400100001"),
            ("PPN_KELUARAN", "PPN Keluaran", "230100001"),
            ("PPN_MASUKAN", "PPN Masukan", "150100001"),
            ("RETUR_PENJUALAN", "Retur Penjualan", "400100002"),
            ("RETUR_PEMBELIAN", "Retur Pembelian", "500100004"),
            ("HPP_PENJUALAN", "Harga Pokok Penjualan", "500100001"),
            ("PEMBELIAN", "Pembelian", "500100003"),
            ("PERSEDIAAN_BAHAN_BAKU", "Persediaan Bahan Baku", "131100001"),
            ("PERSEDIAAN_WIP", "Persediaan Barang Dalam Proses (WIP)", "131100002"),
            ("PERSEDIAAN_BARANG_JADI", "Persediaan Barang Jadi", "131100003"),
            ("BEBAN_ANGKUT_PEMBELIAN", "Beban Angkut Pembelian", "500100005"),
            ("BEBAN_TRANSFER_BANK", "Beban Biaya Transfer Bank", "600100009"),
            ("BEBAN_ADMIN", "Beban Biaya Admin", "600100008"),
            ("PIUTANG_USAHA", "Piutang Usaha", "111000000"),
            ("HUTANG_USAHA", "Hutang Usaha", "220000000"),
        ]

        inserted_setting = 0
        skipped_setting = 0

        for key, label, kode_coa in setting_defaults:
            # Cek sudah ada
            if db.query(SettingAkun).filter(SettingAkun.key == key).first():
                skipped_setting += 1
                continue

            # Cari COA
            coa = db.query(AkunPerkiraan).filter(AkunPerkiraan.kode == kode_coa).first()
            if not coa:
                print(f"  WARNING: COA {kode_coa} tidak ditemukan, skip setting {key}")
                skipped_setting += 1
                continue

            setting = SettingAkun(
                key=key,
                label=label,
                akun_perkiraan_id=coa.id,
            )
            db.add(setting)
            inserted_setting += 1

        db.commit()
        print(f"SettingAkun: {inserted_setting} inserted, {skipped_setting} skipped (already exists)")
        print("Phase 3 seed complete.")

    except Exception as e:
        db.rollback()
        print(f"Error seeding Phase 3: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_phase3_coa_and_settings()