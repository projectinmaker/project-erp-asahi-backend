"""Allocations are draft children; only their posted, unreversed payment settles an invoice."""
from sqlalchemy import Column, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class PaymentAllocation(BaseModel, BaseMixin):
    __tablename__ = 'payment_allocation'
    __table_args__ = (
        CheckConstraint('nilai > 0', name='ck_allocation_positive'),
        CheckConstraint('(penerimaan_id IS NOT NULL AND sales_invoice_id IS NOT NULL AND pembayaran_id IS NULL AND purchase_invoice_id IS NULL) OR (pembayaran_id IS NOT NULL AND purchase_invoice_id IS NOT NULL AND penerimaan_id IS NULL AND sales_invoice_id IS NULL)', name='ck_allocation_pair'),
        UniqueConstraint('penerimaan_id', 'sales_invoice_id', name='uq_allocation_receipt_invoice'),
        UniqueConstraint('pembayaran_id', 'purchase_invoice_id', name='uq_allocation_payment_invoice'),
    )
    penerimaan_id = Column(UUID(as_uuid=True), ForeignKey('penerimaan_kas.id'), nullable=True, index=True)
    pembayaran_id = Column(UUID(as_uuid=True), ForeignKey('pembayaran_kas.id'), nullable=True, index=True)
    sales_invoice_id = Column(UUID(as_uuid=True), ForeignKey('sales_invoice.id'), nullable=True, index=True)
    purchase_invoice_id = Column(UUID(as_uuid=True), ForeignKey('purchase_invoice.id'), nullable=True, index=True)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey('akun_perkiraan.id'), nullable=False)
    nilai = Column(Numeric(18, 2), nullable=False)
    penerimaan = relationship('PenerimaanKas', back_populates='alokasi')
    pembayaran = relationship('PembayaranKas', back_populates='alokasi')
    sales_invoice = relationship('SalesInvoice')
    purchase_invoice = relationship('PurchaseInvoice')

    @property
    def invoice_id(self):
        return self.sales_invoice_id or self.purchase_invoice_id
