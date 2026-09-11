from typing import Type, TypeVar, List, Optional, Any, Tuple
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from app.schemas.base import BaseSchema

T = TypeVar('T', bound=object)

def get_master_list(
    db: Session,
    Model: Type[T],
    skip: int,
    limit: int,
    search_fields: List[str],
    search: Optional[str] = None
) -> Tuple[List[T], int]:
    """
    Fungsi generik untuk mengambil daftar master data.
    Return: Tuple berisi (list_data, total_count)
    """
    query = db.query(Model)

    if search:
        conditions = [getattr(Model, field).ilike(f"%{search}%") for field in search_fields]
        query = query.filter(or_(*conditions))

    # Hitung total keseluruhan data (SEBELUM di-slice)
    total = query.count()

    # Ambil data sesuai batas skip dan limit
    data = query.order_by(Model.created_at.desc()).offset(skip).limit(limit).all()

    return data, total

def get_master_by_id(db: Session, Model: Type[T], item_id: UUID) -> Optional[T]:
    """Fungsi generik untuk mengambil 1 data master berdasarkan UUID"""
    return db.query(Model).filter(Model.id == item_id).first()

def _duplicate_detail(Model: Type[T], data: dict, error: IntegrityError) -> str:
    """Gunakan metadata constraint, tanpa membocorkan detail SQL/database."""
    table = Model.__table__
    constraint_name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    for constraint in list(table.constraints) + list(table.indexes):
        if not isinstance(constraint, UniqueConstraint) and not getattr(constraint, "unique", False):
            continue
        columns = list(constraint.columns)
        name = constraint.name or f"{table.name}_{'_'.join(c.name for c in columns)}_key"
        if name == constraint_name and len(columns) == 1:
            field = columns[0].name
            label = "NIK" if field == "nik" else field.replace("_", " ").capitalize()
            if field in data:
                return f"{label} {table.name.replace('_', ' ')} '{data[field]}' sudah digunakan"
    return f"Data {table.name.replace('_', ' ')} sudah digunakan"


def _commit_master(db: Session, Model: Type[T], data: dict) -> None:
    """Commit create/update dan pulihkan session jika constraint gagal."""
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        # PostgreSQL unique_violation; constraint lain tetap ditangani sebagai error asli.
        if getattr(error.orig, "pgcode", None) == "23505":
            raise HTTPException(status_code=400, detail=_duplicate_detail(Model, data, error)) from error
        raise


def create_master(
    db: Session, Model: Type[T], schema_in: BaseSchema, allow_reactivate: bool = True
) -> T:
    """Create baru atau aktifkan kembali master NONAKTIF dengan kode/NIK sama."""
    data = schema_in.model_dump()
    table = Model.__table__
    key = next((field for field in ("kode", "nik") if field in table.c and field in data), None)
    if table.name == 'barang' and 'stok' in table.c:
        from app.services.accounting_control import accounting_lock
        accounting_lock(db)
        validate_barang_account(db, data.get('akun_persediaan_id'))
    if allow_reactivate and key and "status" in table.c:
        # Serialize create dengan kode yang sama, termasuk saat belum ada row.
        # Lock otomatis dilepas ketika transaksi commit/rollback.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:master_key, 0))"),
                {"master_key": f"master:{table.fullname}:{key}:{data[key]}"},
            )
        matches = (
            db.query(Model)
            .filter(getattr(Model, key) == data[key])
            .order_by(Model.created_at.desc(), Model.id.desc())
            .populate_existing()
            .with_for_update()
            .all()
        )
        # Jangan memilih NONAKTIF jika ada record AKTIF lain dengan kode sama.
        if any(item.status == "AKTIF" for item in matches):
            label = "NIK" if key == "nik" else "Kode"
            entity = table.name.replace("_", " ")
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"{label} {entity} '{data[key]}' sudah digunakan oleh {entity} aktif",
            )
        # Data dari opsi A mungkin memiliki beberapa record NONAKTIF.
        # Pilih record paling baru secara deterministik; record lain tetap utuh.
        existing = next((item for item in matches if item.status == "NONAKTIF"), None)
        if existing is not None:
            if table.name == 'barang':
                from app.models.transaksi.stock_balance import StockBalance
                from app.models.transaksi.stok_mutasi import StokMutasi
                if db.query(StockBalance).filter_by(barang_id=existing.id).first() or db.query(StokMutasi).filter_by(barang_id=existing.id).first():
                    for inventory_field in ('stok', 'harga_pokok', 'metode_valuasi'):
                        data[inventory_field] = getattr(existing, inventory_field)
            for field, value in data.items():
                # COA lama dipertahankan bila request tidak memberi pengganti.
                if field in ("akun_piutang_id", "akun_hutang_id", "akun_persediaan_id") and value is None:
                    continue
                setattr(existing, field, value)
            existing.status = "AKTIF"
            db.add(existing)
            _commit_master(db, Model, data)
            db.refresh(existing)
            return existing
    db_obj = Model(**data)
    db.add(db_obj)
    _commit_master(db, Model, data)
    db.refresh(db_obj)
    return db_obj

def update_master(db: Session, db_obj: Any, schema_in: BaseSchema) -> Any:
    """Fungsi generik untuk update data master yang sudah ada"""
    update_data = schema_in.model_dump(exclude_unset=True)
    if db_obj.__table__.name == 'barang' and 'akun_persediaan_id' in update_data:
        from app.services.accounting_control import accounting_lock
        accounting_lock(db)
        if update_data['akun_persediaan_id'] != db_obj.akun_persediaan_id:
            validate_barang_account(db, update_data['akun_persediaan_id'])
    if db_obj.__table__.name == "barang" and any(field in update_data for field in ('stok', 'harga_pokok', 'metode_valuasi')):
        protect_inventory(db, db_obj, update_data)
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db.add(db_obj)
    # Simpan nilai sebelum rollback agar konflik reaktivasi (status saja)
    # tetap bisa menampilkan kode yang bentrok tanpa reload objek.
    error_data = {column.name: getattr(db_obj, column.name) for column in db_obj.__table__.columns}
    _commit_master(db, type(db_obj), error_data)
    db.refresh(db_obj)
    return db_obj

def soft_delete_master(db: Session, db_obj: Any) -> Any:
    """Mengubah status master data menjadi NONAKTIF"""
    db_obj.status = "NONAKTIF"
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def protect_inventory(db, item, data):
    from app.services.accounting_control import accounting_lock
    from app.models.transaksi.stock_balance import StockBalance
    from app.models.transaksi.stok_mutasi import StokMutasi
    accounting_lock(db)
    if db.query(StockBalance).filter_by(barang_id=item.id).first() or db.query(StokMutasi).filter_by(barang_id=item.id).first():
        for field in ('stok', 'harga_pokok', 'metode_valuasi'):
            if field in data and data[field] != getattr(item, field):
                raise HTTPException(400, 'Stok, biaya, dan metode valuasi tidak boleh diubah langsung setelah ada saldo/mutasi')


def inventory_account_candidates(db):
    """Structural candidates; accounting chooses the actual inventory account."""
    from app.models.akun_perkiraan import AkunPerkiraan
    from app.models.master.kas_bank_akun import KasBankAkun
    from app.models.master.pelanggan import Pelanggan
    from app.models.master.supplier import Supplier
    account = AkunPerkiraan
    query = db.query(account).filter(account.header == 'AKTIVA', account.tingkat == 'DETAIL',
        account.saldo_normal == 'DEBIT', account.status == 'AKTIF', account.is_subledger == False)
    for model, field in ((KasBankAkun, 'akun_perkiraan_id'), (Pelanggan, 'akun_piutang_id'), (Supplier, 'akun_hutang_id')):
        query = query.filter(~db.query(model.id).filter(getattr(model, field) == account.id).exists())
    return query


def validate_barang_account(db, account_id):
    if account_id is None:
        return
    from app.models.akun_perkiraan import AkunPerkiraan
    if inventory_account_candidates(db).filter(AkunPerkiraan.id == account_id).first() is None:
        raise HTTPException(400, 'Akun Persediaan harus akun AKTIVA DETAIL, saldo normal DEBIT, AKTIF, bukan subledger atau akun kas/piutang/hutang yang terhubung')
