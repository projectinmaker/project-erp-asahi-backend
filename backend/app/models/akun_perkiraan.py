import enum
from sqlalchemy import Column, String, Numeric, Integer, ForeignKey, Enum as SQLEnum, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class HeaderCOA(str, enum.Enum):
    """Header klasifikasi akun (LEGACY — tetap dipertahankan untuk backward
    compatibility. Field `account_class` yang baru menjadi canonical target
    tapi `header` enum lama tetap dipakai di banyak query/service existing."""

    AKTIVA = "AKTIVA"
    KEWAJIBAN = "KEWAJIBAN"
    MODAL = "MODAL"
    PENDAPATAN = "PENDAPATAN"
    HPP = "HPP"
    BEBAN = "BEBAN"


class SaldoNormal(str, enum.Enum):
    DEBIT = "DEBIT"
    KREDIT = "KREDIT"


class TingkatAkun(str, enum.Enum):
    HEADER = "HEADER"
    GROUP = "GROUP"
    DETAIL = "DETAIL"


# Mapping dari HeaderCOA (legacy) -> account_class (target architecture).
# Dipakai untuk auto-derive account_class jika field belum diisi explicit saat
# create/update. Lihat coa_service._derive_account_class_from_header().
HEADER_TO_ACCOUNT_CLASS = {
    HeaderCOA.AKTIVA: "ASSET",
    HeaderCOA.KEWAJIBAN: "LIABILITY",
    HeaderCOA.MODAL: "EQUITY",
    HeaderCOA.PENDAPATAN: "REVENUE",
    HeaderCOA.HPP: "COGS",
    HeaderCOA.BEBAN: "EXPENSE",
}

# Mapping node_type (workbook) -> TingkatAkun (existing enum).
# Workbook menggunakan "HEADER"/"GROUP"/"DETAIL" yang 1:1 dengan enum existing.
NODE_TYPE_TO_TINGKAT = {
    "HEADER": TingkatAkun.HEADER,
    "GROUP": TingkatAkun.GROUP,
    "DETAIL": TingkatAkun.DETAIL,
}

# Mapping TingkatAkun -> node_type (untuk response ke FE).
TINGKAT_TO_NODE_TYPE = {v: k for k, v in NODE_TYPE_TO_TINGKAT.items()}


class AkunPerkiraan(BaseModel, BaseMixin):
    __tablename__ = "akun_perkiraan"

    # === LEGACY FIELDS (backward compatibility) ===
    kode = Column(String(20), unique=True, nullable=False, index=True)
    nama = Column(String(200), nullable=False)
    header = Column(SQLEnum(HeaderCOA), nullable=False)
    tingkat = Column(SQLEnum(TingkatAkun), nullable=False)

    # Self-referencing untuk hierarchy
    induk_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=True)
    induk_kode = Column(String(20), nullable=True)  # Denormalized untuk query cepat

    saldo_normal = Column(SQLEnum(SaldoNormal), nullable=False)
    saldo = Column(Numeric(18, 2), default=0, nullable=False)
    tanggal = Column(DateTime(timezone=True), nullable=True)  # Tanggal mulai aktif / penempatan saldo awal
    status = Column(String(20), default="AKTIF", nullable=False)

    # True untuk COA detail yang auto-created per pelanggan/supplier
    # (subledger "Piutang - {nama}" / "Hutang - {nama}"). Akun ini tetap
    # dipakai untuk jurnal, tapi disembunyikan dari modul Akun Perkiraan/COA
    # utama supaya list COA tidak penuh sama entry per-customer/supplier.
    is_subledger = Column(Boolean, default=False, nullable=False)

    # ========================================================
    # === NEW FIELDS — ASAHI COA REVISI v2 (target architecture) ===
    # ========================================================
    # Klasifikasi & reporting (dari sheet COA_SYSTEM_MASTER)
    account_class = Column(String(20), nullable=True)        # ASSET/LIABILITY/EQUITY/REVENUE/COGS/EXPENSE
    account_subclass = Column(String(50), nullable=True)     # CASH_BANK/ACCOUNTS_RECEIVABLE/INVENTORY_*/...
    financial_statement = Column(String(20), nullable=True)  # NERACA/LABA RUGI
    report_group = Column(String(50), nullable=True)         # CURRENT_ASSET/COGS/OPERATING_REVENUE/...
    system_account_type = Column(String(50), nullable=True)  # AR_CONTROL/AP_CONTROL/BANK_CLEARING/CURRENT_EARNINGS/...

    # Posting control
    allow_system_posting = Column(Boolean, default=True, nullable=False)
    allow_manual_posting = Column(Boolean, default=True, nullable=False)
    is_control_account = Column(Boolean, default=False, nullable=False)
    subledger_type = Column(String(30), nullable=True)       # AR/AP/INVENTORY/BANK_TRANSFER
    reconciliation_required = Column(Boolean, default=False, nullable=False)

    # Canonical active flag (sync dengan status AKTIF/NONAKTIF)
    # active=True  -> akun bisa dipakai transaksi baru
    # active=False -> akun di-hide dari UI transaksi baru, tapi tetap terbaca
    #                 di laporan historis & reversal jurnal lama.
    active = Column(Boolean, default=True, nullable=False)

    # ========================================================
    # === Relationships ===
    # ========================================================
    child_accounts = relationship(
        "AkunPerkiraan", back_populates="parent_account", foreign_keys=[induk_id]
    )
    parent_account = relationship(
        "AkunPerkiraan",
        back_populates="child_accounts",
        remote_side="AkunPerkiraan.id",
        foreign_keys=[induk_id],
    )

    # ========================================================
    # === Helper properties (dipakai service/endpoint) ===
    # ========================================================

    @property
    def is_postable_for_manual(self) -> bool:
        """True kalau akun boleh dipakai user untuk posting jurnal manual.

        Rule:
        - Harus level DETAIL
        - active == True
        - allow_manual_posting == True
        - Bukan control account (control account hanya boleh diposting sistem)
          KECUALI allow_manual_posting explicitly True (kasus khusus, jarang).
        """
        return (
            self.tingkat == TingkatAkun.DETAIL
            and self.active is True
            and self.allow_manual_posting is True
        )

    @property
    def is_postable_for_system(self) -> bool:
        """True kalau akun boleh dipakai auto-posting oleh modul sistem
        (Sales/Purchase/Kas-Bank/Inventory dll)."""
        return (
            self.tingkat == TingkatAkun.DETAIL
            and self.active is True
            and self.allow_system_posting is True
        )

    @property
    def is_legacy_locked(self) -> bool:
        """True kalau akun dikunci untuk transaksi baru (legacy COGS 511xxx).

        Dipakai untuk menandai akun yang HANYA untuk histori:
        - allow_system_posting == False
        - allow_manual_posting == False
        Tapi active tetap True supaya tetap muncul di laporan historis.
        """
        return (
            self.allow_system_posting is False
            and self.allow_manual_posting is False
            and self.active is True
        )

    @property
    def is_system_account(self) -> bool:
        """True kalau akun ini adalah system account (CURRENT_EARNINGS,
        RETAINED_EARNINGS, dll) — tidak boleh diposting manual maupun sistem
        (nilai berasal dari perhitungan laba rugi / closing)."""
        return (
            self.system_account_type is not None
            and self.allow_system_posting is False
            and self.allow_manual_posting is False
        )
