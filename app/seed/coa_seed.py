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

                # 5. Detail Hutang Usaha
        hutang_details = [
            ("220101004", "ACE HARDWARE"), ("220101006", "PT ARIF TEKNIK MANDIRI"), ("220101007", "PT ACME INTERNATIONAL"),
            ("220101016", "ANPING COUNTY WODONG"), ("220101025", "PT ANUGERAH HUTAMA MANDIRI PERKASA"), ("220101026", "PT ASPIRASI HIDUP (rupa rupa)"),
            ("220101029", "ASGARAYA PERKASA UTAMA"), ("220101030", "PT. ASSAB STEELS INDONESIA"), ("220101032", "Acexon Technologie"),
            ("220102001", "PT BIROTIKA SEMESTA"), ("220102007", "BALLUFF ASIA Pte Ltd"), ("220102010", "BELISAFETY SUKSES SEJATI"),
            ("220103001", "CAHAYA BUKIT PERUNGGU"), ("220103002", "PT CHIYODA KOGYO INDONESIA"), ("220103006", "PT CIAMIX MACHINE INDONESIA"),
            ("220103007", "PT. CIPTA TEKNINDO PRIMA"), ("220103009", "PT CILEGON STEEL INDONESIA"), ("220103011", "PT CAKRA ADJI GUNUNG"),
            ("220103012", "PT. CIPTA SURYA MANDIRI JAYA"), ("220103013", "CARGOMATE GLOBAL LOGISTICS"), ("220104004", "PT DUTA KIMIA BERJAYA"),
            ("220104007", "DWI MUSTIKA JAYA"), ("220105008", "EU AUTOMATION PTE LTD"), ("220106001", "PT. FUJIMAKI STEEL INDONESIA"),
            ("220106002", "PT. FEDEX EXPRESS INTERNATIONAL"), ("220106004", "CV. FIRST MACHINERY TRADE.CO"), ("220106006", "PT. FEDERAL GRAND INDONESIA"),
            ("220106007", "FOSHAN HAIRAN MACHINERY"), ("220107001", "PT. GAYA STEEL"), ("220107003", "PT. GROZ BECKERT INDONESIA"),
            ("220107004", "PT GLOBAL MULTIPARTS"), ("220107011", "GOOD HAND ENTERPRISE CO LTD"), ("220107012", "GAPURA ANGKASA"),
            ("220107015", "GAPURA MAS PERSADA"), ("220108002", "CV. HADID JAYA INDUSTRI"), ("220108003", "PT HAITEK PRIMA SEMESTA"),
            ("220108006", "HOME CENTER INDONESIA RETAIL"), ("220108010", "CV. HDG TEAM"), ("220109006", "PT. INTAN PERSADA TEKNIK"),
            ("220109010", "ITKLIK INDONESIA"), ("220109013", "INDAH JAYA (LIM BUN SIA)"), ("220110001", "PT JASA ANGKASA SEMESTA TBK"),
            ("220110002", "CV JAVATECH MITRA GEMILANG"), ("220110003", "JINAN SENFENG LASER TECHNOLOGY CO.,LTD"), ("220110006", "JINAN SHARP DRILLING MACHINE TOOL CO. LTD."),
            ("220111002", "PT KARYA SUKSES STEEL"), ("220111003", "CV. KITA"), ("220111004", "PT KRISBROW INDONESIA"),
            ("220111005", "KARYA MANDIRI ALUMINIUM"), ("220111006", "KAWAKAMI"), ("220111007", "KMT MACHINE TOOLS"),
            ("220111009", "KANAR DIGITAL"), ("220111011", "KANTOR AKUNTAN PUBLIK BUDIANDRU DAN REKAN"), ("220111015", "PT. KYSC INDONESIA"),
            ("220111020", "KAIROS MULTI SEJAHTERA"), ("220111021", "CV. KUMALA ADITAMA"), ("220112002", "PT LOGISTIK KARYA BERMITRA"),
            ("220112004", "LAUTAN TIRTA TRANSPORTAMA"), ("220113001", "PT. MISUMI INDONESIA"), ("220113003", "CV MULIA NUSANTARA"),
            ("220113005", "PT. MIGOTO INDONESIA"), ("220113006", "PT MULIA MEGA MAKMUR"), ("220113014", "PT. MITRA SOLUSI JASATAMA"),
            ("220113017", "PT. MULTI STEEL DILUCH"), ("220113019", "MEKAR MAJU BERKAH"), ("220113023", "MUTIARA TATA TEKNIKA"),
            ("220113027", "MAKMUR JAYA ENGGINERING"), ("220114003", "PT NIKITA INDO PRESISI"), ("220116002", "PT PRATAMA MANDIRI PRIMA"),
            ("220116004", "PRATAMA MOTOR"), ("220116009", "PT PROSPECT MOTOR"), ("220116011", "PT PRIMA NANO COATING"),
            ("220119049", "SUZHOU PIONEER MATERIAL HANDLING EQ"), ("220116014", "PT. PERSADA NUSANTARA STEEL"), ("220116018", "PUTRA KARYA TEKNIK"),
            ("220117003", "QINGDAO MAIQU CO LTD"), ("220118001", "PT RAJA LISTRIK CIBATU"), ("220118002", "PT RUKUN SEJAHTERA TEKNIK"),
            ("220118006", "PT. RODA HAMMERINDO JAYA"), ("220118007", "RS Components Pte Ltd"), ("220118011", "ROCHMAD ARSAJI"),
            ("220119001", "SAPTA TEKNIK"), ("220119002", "PT SURYA UNGGUL PRATAMA"), ("220119004", "CV. SINAR JAYA STEEL"),
            ("220119005", "SINARIMA ELEKTRO PLATING"), ("220119006", "SURYA JAYA WINATA"), ("220119008", "SUGIYANTO"),
            ("220119009", "PT STILMETINDO PRIMA"), ("220119010", "PT SINAR PELITA SEJAHTERA"), ("220119011", "CV SETIA TEKNIK"),
            ("220119012", "PT. SANDANA ADI PRAKARSA"), ("220119013", "PT SINAR MUTIARA CAKRABUANA"), ("220119016", "PT SINAR SENTRA SOLUSINDO"),
            ("220119018", "PT SINERGI MEGAH MAKMUR"), ("220119019", "SAMHWA MACHINERY CO., LTD"), ("220119020", "CV SINAR ABADI JAYA"),
            ("220119023", "SHC CO.,LTD"), ("220119025", "PT SENTRA SUKSESTAMA SENTOSA"), ("220119027", "PT SUMBER MEGA JAYA"),
            ("220119029", "PT. SINAR BANGUN BAJA PRIMA"), ("220119034", "PT SINAR AGUNG INDOTAMA"), ("220119041", "SIDOMULYO TEHNIK"),
            ("220119047", "CV. SURYA AGUNG MANDIRI"), ("220120001", "PT. TRIPOLAR POSITIF INDONESIA"), ("220120003", "PT TIGA SAUDARA PRATAMA"),
            ("220120004", "PT TARUMA KURNIA JAYA"), ("220120005", "PT TRIMA LAKSANA JAYA PRATAMA"), ("220120009", "TEKNO COAT NUSINDO"),
            ("220120011", "PT TANAMAS TEGUH MANDIRI"), ("220120012", "TRANSPORTAMA MANDIRI LOGISTIK"), ("220121001", "PT UTAMA KARYA NIAGA"),
            ("220121002", "PT. UNINDO HI TECH PRATAMA"), ("220121004", "PT. UPS Cardig International"), ("220123004", "PT. WIJAYA ALAM TEHNIK"),
            ("220123005", "PT WAHANA DIRGANTARA"), ("220123006", "WEIFANG BAIKEDA ENVIRONMENTAL"), ("220124002", "Xiamen Kinton Industrial Co., Ltd."),
            ("220124003", "PT. XDC Indonesia"), ("220124004", "PT. XAJERIKO ABADI SEJAHTERA"), ("220125002", "PT YOKATTA INDONESIA"),
            ("220125004", "PT YOSHINOBU"), ("220125006", "YUOU(LUOYANG) DOORS AND WINDOWS TECHNOLOGY CO., LTD"), ("220126001", "ZHEJIANG JIECANG LINEAR MOTION"),
            ("220127001", "PENYESUAIAN HPP TAHUN LALU"), ("220127002", "UANG MUKA PENJUALAN"), ("220127003", "HUTANG PEMEGANG SAHAM PA EKO"),
            ("220127004", "HUTANG PEMEGANG SAHAM PA ADI"),
        ]
        insert_details(hutang_details, "220000000")

        db.commit()
        print("Inserted Kas, Piutang Usaha, Piutang Karyawan, dan Hutang Usaha details")

    except Exception as e:
        db.rollback()
        print(f"Error seeding COA: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_coa()
