"""PurchaseInvoiceReceiptMatch — Three-way match bridge table.

Sesuai Master Roadmap §20 "Recommended bridge":

    purchase_invoice_receipt_match
    --------------------------------
    purchase_invoice_detail_id
    penerimaan_barang_detail_id
    matched_qty
    matched_unit_cost
    matched_value

Dipakai untuk track match antara invoice line dan receipt line. Satu invoice
line bisa match banyak receipt lines (partial receipt), dan satu receipt line
bisa match banyak invoice lines (partial invoice).

Tujuan (Roadmap §20):
- "Three-way match foundation"
- "Receipt-Invoice match bridge"
- "Partial receipt/invoice supported"

Logic match (akan di-implement di service, Phase 5 hanya buat schema):
- Saat post PurchaseInvoice, sistem akan auto-match invoice lines ke receipt
  lines berdasarkan:
  - Same barang_id
  - Same supplier_id (via header)
  - Receipt qty available (qty_received - qty_already_matched > 0)
- Kalau invoice price != receipt price, catat match dengan matched_unit_cost
  dari invoice price (karena PPN input VAT dihitung dari invoice).
- Match table ini juga dipakai untuk GRNI clearing: matched_value di-debit
  dari GRNI account, di-kredit ke AP account.

Catatan:
- Tidak ada unique constraint di (purchase_invoice_detail_id, penerimaan_barang_detail_id)
  karena bisa ada multiple match entries antara 2 line tsb (kasus partial match).
  Service code harus ensure tidak over-match (sum matched_qty per invoice detail
  <= invoice qty, sum matched_qty per receipt detail <= receipt qty).
"""
from decimal import Decimal
from sqlalchemy import Column, Integer, Numeric, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PurchaseInvoiceReceiptMatch(BaseModel, BaseMixin):
    """Bridge table for three-way matching (PO → Receipt → Invoice).

    Each row represents a match between one Purchase Invoice detail line
    and one Goods Receipt (Penerimaan Barang) detail line.
    """
    __tablename__ = "purchase_invoice_receipt_match"

    # === FK ke PurchaseInvoiceDetail ===
    purchase_invoice_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("purchase_invoice_detail.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # === FK ke PenerimaanBarangDetail (Goods Receipt Detail) ===
    penerimaan_barang_detail_id = Column(
        UUID(as_uuid=True),
        ForeignKey("penerimaan_barang_detail.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # === Match details (snapshot at match time) ===
    matched_qty = Column(Integer, nullable=False)
    matched_unit_cost = Column(Numeric(18, 2), nullable=False)
    matched_value = Column(Numeric(18, 2), nullable=False)

    # Relationships
    purchase_invoice_detail = relationship(
        "PurchaseInvoiceDetail",
        back_populates="match_entries",
        foreign_keys=[purchase_invoice_detail_id],
    )
    penerimaan_barang_detail = relationship(
        "PenerimaanBarangDetail",
        foreign_keys=[penerimaan_barang_detail_id],
    )

    def __repr__(self):
        return (
            f"<PurchaseInvoiceReceiptMatch "
            f"invoice_detail={self.purchase_invoice_detail_id} "
            f"receipt_detail={self.penerimaan_barang_detail_id} "
            f"qty={self.matched_qty} value={self.matched_value}>"
        )
