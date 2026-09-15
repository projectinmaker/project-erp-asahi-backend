from sqlalchemy import Column, String, Integer, ForeignKey, Enum as SQLEnum, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


# Reuse MetodePenyusutan dari aset_tetap (sudah ada di DB sebagai PG enum type)
from app.models.transaksi.aset_tetap.aset_tetap import MetodePenyusutan


class KategoriAset(BaseModel, BaseMixin):
    """Master data Kategori Aset (Asset Category).

    Sesuai Master Roadmap §9 Asset Category:
    P0 minimum:
    - asset_cost_account
    - accumulated_depreciation_account
    - depreciation_expense_account
    - default useful life/method (optional, dipakai sebagai default saat create aset)
    """
    __tablename__ = "kategori_aset"
    __table_args__ = (
        Index("ix_kategori_aset_kode", "kode", unique=True, postgresql_where=text("status = 'AKTIF'")),
    )

    # === P0 minimum ===
    kode = Column(String(20), nullable=False)
    nama = Column(String(100), nullable=False)
    status = Column(String(20), default="AKTIF", nullable=False)

    # === NEW Phase 2 — P0 accounting mapping ===
    akun_aset_id = Column(  # asset_cost_account
        UUID(as_uuid=True), ForeignKey('akun_perkiraan.id', name='fk_kategori_aset_akun_aset'),
        nullable=True
    )
    akun_akumulasi_id = Column(  # accumulated_depreciation_account
        UUID(as_uuid=True), ForeignKey('akun_perkiraan.id', name='fk_kategori_aset_akun_akumulasi'),
        nullable=True
    )
    akun_beban_id = Column(  # depreciation_expense_account
        UUID(as_uuid=True), ForeignKey('akun_perkiraan.id', name='fk_kategori_aset_akun_beban'),
        nullable=True
    )

    # === NEW Phase 2 — Default untuk create aset baru ===
    default_useful_life = Column(Integer, nullable=True)  # dalam bulan
    default_method = Column(
        SQLEnum(MetodePenyusutan), nullable=True
    )  # GARIS_LURUS / SALDO_MENURUN

    # Relationships
    akun_aset = relationship('AkunPerkiraan', foreign_keys=[akun_aset_id])
    akun_akumulasi = relationship('AkunPerkiraan', foreign_keys=[akun_akumulasi_id])
    akun_beban = relationship('AkunPerkiraan', foreign_keys=[akun_beban_id])
