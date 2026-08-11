import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.akun_perkiraan import AkunPerkiraan, HeaderCOA, SaldoNormal, TingkatAkun


def seed_coa():
    db: Session = SessionLocal()
    try:
        # Cek jika data sudah ada
        if db.query(AkunPerkiraan).count() > 0:
            print("COA already exists, skipping seed.")
            return

        # 1. HEADER LEVEL 1
        headers = [
            {
                "kode": "100000000",
                "nama": "KAS DAN SETARA KAS",
                "header": HeaderCOA.AKTIVA,
                "saldo_normal": SaldoNormal.DEBIT,
            },
            {
                "kode": "111000000",
                "nama": "PIUTANG USAHA",
                "header": HeaderCOA.AKTIVA,
                "saldo_normal": SaldoNormal.DEBIT,
            },
            {
                "kode": "130000000",
                "nama": "PIUTANG LAINNYA",
                "header": HeaderCOA.AKTIVA,
                "saldo_normal": SaldoNormal.DEBIT,
            },
            {
                "kode": "130000001",
                "nama": "PIUTANG KARYAWAN",
                "header": HeaderCOA.AKTIVA,
                "saldo_normal": SaldoNormal.DEBIT,
            },
            {
                "kode": "131000000",
                "nama": "PERSEDIAAN",
                "header": HeaderCOA.AKTIVA,
                "saldo_normal": SaldoNormal.DEBIT,
            },
            {
                "kode": "150000000",
                "nama": "PAJAK DIBAYAR DIMUKA",
                "header": HeaderCOA.AKTIVA,
                "saldo_normal": SaldoNormal.DEBIT,
            },
            {
                "kode": "180000000",
                "nama": "ASET TETAP",
                "header": HeaderCOA.AKTIVA,
                "saldo_normal": SaldoNormal.DEBIT,
            },
            {
                "kode": "181000000",
                "nama": "AKUMULASI PENYUSUTAN",
                "header": HeaderCOA.AKTIVA,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "220000000",
                "nama": "HUTANG USAHA",
                "header": HeaderCOA.KEWAJIBAN,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "230000000",
                "nama": "HUTANG PAJAK",
                "header": HeaderCOA.KEWAJIBAN,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "240000000",
                "nama": "HUTANG BIAYA",
                "header": HeaderCOA.KEWAJIBAN,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "260000000",
                "nama": "HUTANG AFILIASI",
                "header": HeaderCOA.KEWAJIBAN,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "270000000",
                "nama": "HUTANG ASSET",
                "header": HeaderCOA.KEWAJIBAN,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "300000000",
                "nama": "MODAL SAHAM",
                "header": HeaderCOA.MODAL,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "310000000",
                "nama": "SALDO LABA",
                "header": HeaderCOA.MODAL,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "400000000",
                "nama": "PENDAPATAN",
                "header": HeaderCOA.PENDAPATAN,
                "saldo_normal": SaldoNormal.KREDIT,
            },
            {
                "kode": "500000000",
                "nama": "HARGA POKOK PENJUALAN",
                "header": HeaderCOA.HPP,
                "saldo_normal": SaldoNormal.DEBIT,
            },
            {
                "kode": "600000000",
                "nama": "BEBAN USAHA",
                "header": HeaderCOA.BEBAN,
                "saldo_normal": SaldoNormal.DEBIT,
            },
        ]

        for h in headers:
            db.add(
                AkunPerkiraan(
                    kode=h["kode"],
                    nama=h["nama"],
                    header=h["header"],
                    tingkat=TingkatAkun.HEADER,
                    saldo_normal=h["saldo_normal"],
                )
            )
        db.commit()
        print(f"Inserted {len(headers)} Headers")

        # 2. DETAIL LEVEL 3 (Kas & Bank) -> Diperbaiki formatnya jadi 2 elemen
        kas_details = [
            ("100000001", "Kas Kecil"),
            ("100000002", "Kas Besar"),
            ("110000001", "Bank BNI 2762"),
            ("110000002", "Bank Mandiri 7269"),
            ("110000003", "Bank BSI 7032"),
            ("110000004", "Bank BRI 2307"),
            ("110000005", "Bank BCA 7777"),
            ("110000006", "Bank BSI 4562"),
        ]

        # 3. Detail Piutang Usaha
        piutang_details = [
            ("111101001", "PT. AUTO ASKA INDONESIA"),
            ("111103001", "PT CEMINDO GEMILANG TBK"),
            ("111106001", "PT. FILPRIME BERKAH SINERGI"),
            ("111106002", "PT. FUJI SEAT INDONESIA"),
            ("111107001", "G-TEKT INDONESIA MANUFACTURING"),
            ("111107002", "PT. GETEKA FOUNINDO"),
            ("111108001", "PT H-ONE KOGI PRIMA AUTO TECHNOLOGIES INDONESIA"),
            ("111109001", "INKA MULTI SOLUSI TRADING"),
            ("111111002", "PT. KITADA ENGINEERING INDONESIA"),
            ("111111003", "KSB INDONESIA"),
            ("111113003", "PT. MIRKA DIRAYA ABIRUPA"),
            ("111113005", "PT. MARUHIDE INDONESIA"),
            ("111119002", "SANOH INDONESIA"),
            ("111120001", "TOYOTA BOSHOKU INDONESIA"),
            ("111121001", "PT. USUI INTERNATIONAL INDONESIA"),
            ("111125001", "YUTAKA MANUFACTURING INDONESIA"),
        ]

        # 4. Detail Piutang Karyawan
        karyawan_details = [
            ("130123001", "WAHIDIN"),
            ("130104001", "DWI HARTANTO"),
            ("130114001", "NURUL FIQRI"),
            ("130125001", "YUSUF"),
            ("130123002", "WIDODO"),
            ("130104002", "DWI KONELI"),
            ("130112001", "LUKMAN"),
            ("130125002", "YOGIAT"),
            ("130110001", "JUKIH"),
            ("130118001", "RIO GUNAWAN"),
            ("130121001", "UANG BARU"),
            ("130113001", "MUHAMMAD AJI WIJAYANTO"),
            ("130113002", "M RUDI"),
            ("130101001", "ARIS M"),
            ("130116001", "PRADIVAL"),
            ("130120001", "TRI SUDARWANTO"),
            ("130101002", "ALFATH"),
            ("130101003", "ARIS S"),
            ("130113003", "MUHAMAD KHAIRUL UMAM"),
            ("130106001", "FAIZ MUZAKI"),
            ("130120002", "TRIYONO"),
        ]

        def insert_details(details_list, induk_kode_str):
            for kode, nama in details_list:
                induk = db.query(AkunPerkiraan).filter_by(kode=induk_kode_str).first()
                if induk:
                    db.add(
                        AkunPerkiraan(
                            kode=kode,
                            nama=nama,
                            header=induk.header,
                            tingkat=TingkatAkun.DETAIL,
                            induk_id=induk.id,
                            induk_kode=induk.kode,
                            saldo_normal=induk.saldo_normal,
                        )
                    )

        insert_details(kas_details, "100000000")
        insert_details(piutang_details, "111000000")
        insert_details(karyawan_details, "130000001")

        db.commit()
        print("Inserted Kas, Piutang Usaha, dan Piutang Karyawan details")

    except Exception as e:
        db.rollback()
        print(f"Error seeding COA: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_coa()
