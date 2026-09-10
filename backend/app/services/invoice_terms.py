from datetime import timedelta
from app.models.master.syarat_bayar import SyaratBayar
from app.services.settlement_service import local_day


def set_due_date(db, invoice, explicit=None):
    term = db.get(SyaratBayar, invoice.syarat_bayar_id) if invoice.syarat_bayar_id else None
    if invoice.syarat_bayar_id and term is None:
        raise ValueError('Syarat bayar tidak ditemukan')
    days = term.hari or 0 if term else 0
    if days < 0:
        raise ValueError('Termin pembayaran tidak boleh negatif')
    due = local_day(explicit) if explicit is not None else local_day(invoice.tanggal) + timedelta(days=days)
    if due < local_day(invoice.tanggal):
        raise ValueError('Jatuh tempo tidak boleh sebelum tanggal invoice')
    invoice.tanggal_jatuh_tempo = due
