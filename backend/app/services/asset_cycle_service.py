"""Asset lifecycle documents share maker/checker and the accounting transaction lock."""
from calendar import monthrange
from datetime import date
from decimal import Decimal
from uuid import UUID
from fastapi import HTTPException
from app.models import AsetTetap
from app.models.transaksi.asset_event import AssetEvent
from app.models.transaksi.aset_tetap.aset_tetap import StatusAsetTetap
from app.models.transaksi.jurnal import RefModule
from app.services.accounting_control import atomic_accounting_write
from app.services.posting_service import auto_posting_jurnal, reverse_journal, JurnalEntryItem
from app.services.settlement_service import local_day
from app.services.warehouse_service import money


def snapshot(asset):
    return {name: str(getattr(asset, name)) for name in ('nilai_buku', 'akumulasi_penyusutan', 'umur_aset', 'nilai_sisa', 'penyusutan_per_bulan', 'lokasi') } | {'status': asset.status.value, 'capitalized': asset.capitalized}


def posted(db, asset):
    return db.query(AssetEvent).filter_by(aset_id=asset.id, status='POSTED').order_by(AssetEvent.tanggal, AssetEvent.created_at, AssetEvent.id).all()


def prepare(db, obj):
    asset = db.get(AsetTetap, obj.aset_id)
    if asset is None or asset.status == StatusAsetTetap.DIHAPUSKAN:
        raise ValueError('Aset tidak tersedia atau sudah dilepas')
    if getattr(asset.metode_penyusutan, 'value', asset.metode_penyusutan) != 'GARIS_LURUS':
        raise ValueError('Siklus aset tahap ini mendukung metode GARIS_LURUS saja')
    day = local_day(obj.tanggal)
    events = posted(db, asset)
    if day < local_day(asset.tanggal_mulai) or (events and day < local_day(events[-1].tanggal)):
        raise ValueError('Tanggal transaksi mendahului awal aset atau transaksi terakhir')
    p = obj.parameter
    entries = []
    if obj.jenis in ('KAPITALISASI', 'REGISTRASI'):
        if asset.capitalized or events:
            raise ValueError('Aset sudah dikapitalisasi')
        life, residual = int(p['umurBulan']), money(p['nilaiSisa'])
        if life <= 0 or residual < 0 or residual >= asset.nilai_perolehan:
            raise ValueError('Umur dan nilai sisa aset tidak valid')
        if obj.jenis == 'KAPITALISASI' and p['akunLawanId'] in (str(asset.akun_aset_id), str(asset.akun_akumulasi_id)):
            raise ValueError('Akun lawan kapitalisasi harus berbeda dari akun aset/akumulasi')
        obj.total = asset.nilai_perolehan
        opening = money(p.get('akumulasiAwal', 0)) if obj.jenis == 'REGISTRASI' else Decimal(0)
        if opening < 0 or opening + residual >= obj.total or money((obj.total-opening-residual)/life) <= 0:
            raise ValueError('Akumulasi awal/nilai sisa/umur menghasilkan nilai penyusutan tidak valid')
        if obj.jenis == 'REGISTRASI':
            from app.models import JurnalUmum
            source = db.get(JurnalUmum, UUID(p['sourceJournalId']))
            if not source or getattr(source.status, 'value', source.status) != 'POSTED' or source.ref_module not in (RefModule.MANUAL, RefModule.SALDO_AWAL) or source.reversal_of_id:
                raise ValueError('Registrasi memerlukan jurnal manual/saldo awal POSTED yang belum dibalik')
            if db.query(JurnalUmum).filter_by(reversal_of_id=source.id).first():
                raise ValueError('Jurnal sumber sudah dibalik')
            if local_day(source.tanggal) > day:
                raise ValueError('Tanggal registrasi mendahului jurnal sumber')
            if db.query(AssetEvent).filter(AssetEvent.source_journal_id == source.id, AssetEvent.status != 'BATAL', AssetEvent.id != obj.id).first():
                raise ValueError('Jurnal sumber sudah digunakan untuk registrasi aset lain')
            debit = sum((r.debit-r.kredit for r in source.details if r.akun_perkiraan_id == asset.akun_aset_id), Decimal(0))
            credit = sum((r.kredit-r.debit for r in source.details if r.akun_perkiraan_id == asset.akun_akumulasi_id), Decimal(0))
            if debit != obj.total or credit != opening:
                raise ValueError('Saldo aset/akumulasi pada jurnal sumber tidak sesuai nilai registrasi')
            obj.source_journal_id = source.id
        else:
            entries = [JurnalEntryItem(asset.akun_aset_id, debit=obj.total), JurnalEntryItem(UUID(p['akunLawanId']), kredit=obj.total)]
    else:
        if not asset.capitalized:
            raise ValueError('Kapitalisasi aset harus diposting terlebih dahulu; rekonsiliasi aset lama sebelum kapitalisasi')
        if obj.jenis == 'PENYUSUTAN':
            capital = next(x for x in events if x.jenis in ('KAPITALISASI', 'REGISTRASI'))
            depreciation = [x for x in events if x.jenis == 'PENYUSUTAN']
            start = local_day(capital.tanggal)
            month_index = start.year * 12 + start.month - 1 + len(depreciation)
            year, month0 = divmod(month_index, 12)
            expected = date(year, month0 + 1, monthrange(year, month0 + 1)[1])
            if day != expected:
                raise ValueError(f'Penyusutan berikutnya harus bertanggal {expected.isoformat()} (akhir bulan, berurutan)')
            remaining = asset.nilai_buku - asset.nilai_sisa
            if remaining <= 0 or len(depreciation) >= asset.umur_aset:
                raise ValueError('Aset sudah selesai disusutkan')
            obj.total = remaining if len(depreciation) == asset.umur_aset - 1 else min(asset.penyusutan_per_bulan, remaining)
            entries = [JurnalEntryItem(asset.akun_beban_id, debit=obj.total), JurnalEntryItem(asset.akun_akumulasi_id, kredit=obj.total)]
        elif obj.jenis == 'PELEPASAN':
            proceeds = money(p['nilaiPelepasan'])
            if proceeds < 0:
                raise ValueError('Nilai pelepasan tidak boleh negatif')
            forbidden = {str(asset.akun_aset_id), str(asset.akun_akumulasi_id)}
            if p.get('akunLawanId') in forbidden or p.get('akunLabaRugiId') in forbidden:
                raise ValueError('Akun hasil/laba rugi pelepasan harus berbeda dari akun aset/akumulasi')
            # Require depreciation through the preceding month before disposal.
            capital = next(x for x in events if x.jenis in ('KAPITALISASI', 'REGISTRASI'))
            start = local_day(capital.tanggal)
            months = min(asset.umur_aset, max(0, (day.year-start.year)*12+day.month-start.month))
            if sum(x.jenis == 'PENYUSUTAN' for x in events) < months:
                raise ValueError('Lengkapi penyusutan sampai bulan sebelum pelepasan')
            obj.total = proceeds
            if proceeds:
                entries.append(JurnalEntryItem(UUID(p['akunLawanId']), debit=proceeds))
            if asset.akumulasi_penyusutan:
                entries.append(JurnalEntryItem(asset.akun_akumulasi_id, debit=asset.akumulasi_penyusutan))
            entries.append(JurnalEntryItem(asset.akun_aset_id, kredit=asset.nilai_perolehan))
            difference = asset.nilai_buku - proceeds
            if difference:
                entries.append(JurnalEntryItem(UUID(p['akunLabaRugiId']), debit=max(difference, 0), kredit=max(-difference, 0)))
        elif obj.jenis == 'MUTASI':
            if not p.get('lokasi') or p['lokasi'] == asset.lokasi:
                raise ValueError('Lokasi tujuan harus diisi dan berbeda')
            obj.total = Decimal(0)
        else:
            raise ValueError('Jenis transaksi aset tidak didukung')
    return asset, entries


@atomic_accounting_write
def create_event(db, aset_id, jenis, tanggal, parameter, created_by):
    obj = AssetEvent(aset_id=aset_id, jenis=jenis, tanggal=tanggal, parameter=parameter, created_by=created_by, status='DRAFT')
    prepare(db, obj)
    db.add(obj)
    db.flush()
    return obj


def post_event(db, obj, actor_id):
    asset, entries = prepare(db, obj)
    obj.sebelum = snapshot(asset)
    if entries:
        journal = auto_posting_jurnal(db, RefModule.PENYUSUTAN, 'FA-'+str(obj.id)[:20], entries,
            ref_id=obj.id, tanggal=obj.tanggal, created_by=actor_id, tipe_transaksi='ASET_'+obj.jenis)
        obj.jurnal_umum_id = journal.id
    if obj.jenis in ('KAPITALISASI', 'REGISTRASI'):
        asset.capitalized = True
        asset.umur_aset = int(obj.parameter['umurBulan'])
        asset.nilai_sisa = money(obj.parameter['nilaiSisa'])
        asset.akumulasi_penyusutan = money(obj.parameter.get('akumulasiAwal', 0)) if obj.jenis == 'REGISTRASI' else Decimal(0)
        asset.nilai_buku = asset.nilai_perolehan - asset.akumulasi_penyusutan
        asset.penyusutan_per_bulan = money((asset.nilai_buku - asset.nilai_sisa) / asset.umur_aset)
    elif obj.jenis == 'PENYUSUTAN':
        asset.akumulasi_penyusutan += obj.total
        asset.nilai_buku -= obj.total
    elif obj.jenis == 'PELEPASAN':
        asset.status = StatusAsetTetap.DIHAPUSKAN
        asset.nilai_buku = Decimal(0)
    else:
        asset.lokasi = obj.parameter['lokasi']
    obj.status = 'POSTED'
    obj.sesudah = snapshot(asset)
    db.flush()


@atomic_accounting_write
def cancel_event(db, db_obj, user, reason):
    from app.services.workflow_service import role, APPROVERS, find_workflow, append_event
    if role(user) not in APPROVERS:
        raise HTTPException(403, 'Pembatalan memerlukan manajer/admin')
    if not reason or not reason.strip():
        raise ValueError('Alasan pembatalan wajib diisi')
    if db_obj.status == 'BATAL':
        raise ValueError('Dokumen sudah dibatalkan')
    if db_obj.status == 'POSTED':
        asset = db.get(AsetTetap, db_obj.aset_id)
        if posted(db, asset)[-1].id != db_obj.id:
            raise ValueError('Batalkan transaksi aset berikutnya terlebih dahulu')
        if db_obj.jurnal_umum_id:
            reverse_journal(db, db_obj.jurnal_umum_id, user.id, reason)
        for key, value in db_obj.sebelum.items():
            if key in ('nilai_buku', 'akumulasi_penyusutan', 'nilai_sisa', 'penyusutan_per_bulan'):
                value = Decimal(value)
            elif key == 'umur_aset':
                value = int(value)
            elif key == 'status':
                value = StatusAsetTetap(value)
            elif key == 'lokasi' and value == 'None':
                value = None
            setattr(asset, key, value)
    db_obj.status = 'BATAL'
    # The atomic decorator appends the cancellation workflow event.
    db.flush()
    return db_obj
