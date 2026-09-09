"""Transaction boundaries and guards shared by accounting writes."""

from functools import wraps
from inspect import signature


def atomic_accounting_write(function):
    """Rollback failed operations, lock existing documents, check their period."""
    parameters = signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = parameters.bind(*args, **kwargs)
        db = bound.arguments["db"]
        try:
            item = bound.arguments.get("db_obj")
            if item is not None:
                item = (db.query(type(item)).filter(type(item).id == item.id)
                        .populate_existing().with_for_update().one())
                bound.arguments["db_obj"] = item
            from app.services.penutupan_periode_service import validate_periode_not_closed
            for tanggal in (getattr(item, "tanggal", None), bound.arguments.get("tanggal")):
                if tanggal is not None:
                    validate_periode_not_closed(db, tanggal)
            return function(*bound.args, **bound.kwargs)
        except Exception:
            db.rollback()
            raise

    return wrapped


def require_unposted(item):
    if getattr(item, "jurnal_umum_id", None):
        raise ValueError("Dokumen sudah diposting. Batalkan dengan jurnal pembalik sebelum membuat koreksi.")
    value = getattr(getattr(item, "status", None), "value", getattr(item, "status", None))
    if value in ("BATAL", "DIBATALKAN", "SELESAI", "DISETUJUI"):
        raise ValueError("Dokumen final/batal tidak bisa diubah.")


def require_no_stock_movement(item):
    value = getattr(item.status, "value", item.status)
    if value in ("SELESAI", "DISETUJUI"):
        raise ValueError("Dokumen sudah mengubah stok. Gunakan retur/penyesuaian stok, bukan pembatalan langsung.")
