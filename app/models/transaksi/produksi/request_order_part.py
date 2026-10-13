"""Request Order Part — form pre-PO yang diajukan Engineering.

Catatan update (Request Order Part):
Sebelum masuk ke PO, Engineering mengisi form REQUEST ORDER PART.
Isi form: Project Name, Model, Customer (ditarik otomatis dari Sales Order
yang dipilih), Tanggal, lalu tabel part dengan kolom
NO | ID PART | DESCRIPTION | MATERIAL | QTY | QS | SUPPLIER | KETERANGAN
yang terbagi dua kelompok kolom: ORDER dan STOK.
Terdapat kolom Note (kiri) dan blok tanda tangan DIBUAT / DIPERIKSA /
DIKETAHUI / DISETUJUI (input nama) di kanan bawah.

Distribusi salinan cetak: PUTIH = PURCHASING, KUNING = PRODUKSI,
MERAH = ENGINEERING.
"""

import enum
from sqlalchemy import Column, String, Text, Numeric, Integer, ForeignKey, Enum as SQLEnum, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import BaseModel
from app.models.base import BaseMixin


class StatusRequestOrderPart(str, enum.Enum):
    DRAFT = "DRAFT"
    DISETUJUI = "DISETUJUI"


class RequestOrderPart(BaseModel, BaseMixin):
    __tablename__ = "request_order_part"

    no_rop = Column(String(40), unique=True, nullable=False, index=True)  # "ROP ASI/YYYY/NNN"
    tanggal = Column(DateTime(timezone=True), nullable=False)

    # Header form
    project_name = Column(String(200), nullable=False)
    model = Column(String(200), nullable=True)
    # Customer ditarik dari SO yang dipilih (denormalisasi pelanggan_id)
    sales_order_id = Column(UUID(as_uuid=True), ForeignKey("sales_order.id"), nullable=False, index=True)
    pelanggan_id = Column(UUID(as_uuid=True), ForeignKey("pelanggan.id"), nullable=False, index=True)

    # Kolom note di sisi kiri bawah form
    catatan = Column(Text, nullable=True)

    # Blok tanda tangan (input NAMA — gaya form kertas)
    dibuat_oleh = Column(String(150), nullable=True)
    diperiksa_oleh = Column(String(150), nullable=True)
    diketahui_oleh = Column(String(150), nullable=True)
    disetujui_oleh = Column(String(150), nullable=True)

    status = Column(SQLEnum(StatusRequestOrderPart), default=StatusRequestOrderPart.DRAFT, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("pengguna.id"), nullable=False)

    # Relationships
    sales_order = relationship("SalesOrder", foreign_keys=[sales_order_id])
    pelanggan = relationship("Pelanggan", foreign_keys=[pelanggan_id])
    creator = relationship("Pengguna", foreign_keys=[created_by])
    items = relationship(
        "RequestOrderPartItem",
        back_populates="request_order_part",
        cascade="all, delete-orphan",
        order_by="RequestOrderPartItem.no",
    )


class RequestOrderPartItem(BaseModel, BaseMixin):
    __tablename__ = "request_order_part_item"

    request_order_part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("request_order_part.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    no = Column(Integer, nullable=False, default=1)

    # ── Kelompok kolom ORDER ──────────────────────────────────────────
    id_part = Column(String(100), nullable=True)
    description = Column(String(500), nullable=True)
    material = Column(String(200), nullable=True)
    qty = Column(Numeric(18, 2), default=0, nullable=False)

    # ── Kelompok kolom STOK ───────────────────────────────────────────
    qs = Column(Numeric(18, 2), default=0, nullable=False)  # QS = Quantity Stok
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("supplier.id"), nullable=True)
    keterangan = Column(String(500), nullable=True)

    # Relationships
    supplier = relationship("Supplier")
    request_order_part = relationship("RequestOrderPart", back_populates="items")
