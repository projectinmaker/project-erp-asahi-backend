"""asset_register_reconciliation_service.py

Service untuk rekonsiliasi Asset Register dengan GL (Master Roadmap §24).

Sesuai Roadmap §24:
- "Asset Register cost = FA Cost GL"
- "Accumulated Depreciation Register = GL Accum Dep"

Reconciliation membandingkan:
1. Sum(nilai_perolehan) dari AsetTetap yang AKTIF/DALAM_PERBAIKAN (tidak DIHAPUSKAN)
   vs Sum(debit - kredit) di GL untuk akun aset (FIXED_ASSET system_account_type)
2. Sum(akumulasi_penyusutan) dari AsetTetap yang AKTIF/DALAM_PERBAIKAN
   vs Sum(kredit - debit) di GL untuk akun akumulasi penyusutan (ACCUM_DEPR system_account_type)
3. NBV (nilai_buku) = nilai_perolehan - akumulasi_penyusutan

Dipakai oleh:
- Endpoint GET /api/v1/aset-tetap/rekonsiliasi
- Hypercare daily reconciliation (Roadmap §68)
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.transaksi.aset_tetap.aset_tetap import (
    AsetTetap, StatusAsetTetap,
)
from app.models.master.kategori_aset import KategoriAset
from app.models.akun_perkiraan import AkunPerkiraan, TingkatAkun
from app.models.transaksi.jurnal import JurnalUmum, StatusJurnal
from app.models.detail.jurnal_detail import JurnalDetail


# ==========================================
# Helper: Get GL balance for an account
# ==========================================
def _get_gl_balance(
    db: Session,
    akun_id: UUID,
    as_of: Optional[datetime] = None,
) -> Decimal:
    """Hitung saldo GL untuk akun tertentu (debit - kredit untuk akun DEBIT normal,
    kredit - debit untuk akun KREDIT normal).

    Filter: hanya POSTED journals, sampai tanggal as_of (inclusive).
    """
    query = (
        db.query(
            func.coalesce(func.sum(JurnalDetail.debit), 0),
            func.coalesce(func.sum(JurnalDetail.kredit), 0),
        )
        .join(JurnalUmum, JurnalUmum.id == JurnalDetail.jurnal_umum_id)
        .filter(
            JurnalDetail.akun_perkiraan_id == akun_id,
            JurnalUmum.status == StatusJurnal.POSTED,
        )
    )
    if as_of is not None:
        query = query.filter(JurnalUmum.tanggal <= as_of)

    row = query.first()
    total_debit = Decimal(str(row[0] or 0))
    total_kredit = Decimal(str(row[1] or 0))

    # Untuk akun aset (DEBIT normal): saldo = debit - kredit
    # Untuk akun akumulasi penyusutan (KREDIT normal): saldo = kredit - debit
    # Kita return (debit - kredit), caller yang interpret
    return total_debit - total_kredit


# ==========================================
# Reconciliation per account
# ==========================================
def get_rekonsiliasi_aset(
    db: Session,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Rekonsiliasi Asset Register vs GL.

    Return:
        Dict dengan struktur:
        {
            "basis": "CURRENT_ASSET_REGISTER_VS_ALL_POSTED_JOURNALS",
            "as_of": "2026-09-15T...",
            "summary": {
                "total_nilai_perolehan_register": Decimal,
                "total_akumulasi_penyusutan_register": Decimal,
                "total_nilai_buku_register": Decimal,
                "total_asset_count": int,
            },
            "per_akun": [
                {
                    "akun_aset_id": UUID,
                    "akun_aset_kode": str,
                    "akun_aset_nama": str,
                    "nilai_perolehan_register": Decimal,
                    "saldo_gl_cost": Decimal,
                    "selisih_cost": Decimal,
                    "akun_akumulasi_id": UUID,
                    "akun_akumulasi_kode": str,
                    "akun_akumulasi_nama": str,
                    "akumulasi_penyusutan_register": Decimal,
                    "saldo_gl_accum": Decimal,
                    "selisih_accum": Decimal,
                    "asset_count": int,
                },
                ...
            ],
            "catatan": "Selisih dapat berasal dari saldo awal, capitalization belum dipost, disposal belum dipost, dan mapping akun berubah."
        }
    """
    # Ambil semua aset yang AKTIF atau DALAM_PERBAIKAN (tidak DIHAPUSKAN)
    # Group by (akun_aset_id, akun_akumulasi_id) untuk reconciliation per akun
    assets = (
        db.query(AsetTetap)
        .filter(AsetTetap.status != StatusAsetTetap.DIHAPUSKAN)
        .all()
    )

    # Group by (akun_aset_id, akun_akumulasi_id)
    groups: Dict[tuple, Dict] = {}
    for asset in assets:
        key = (asset.akun_aset_id, asset.akun_akumulasi_id)
        if key not in groups:
            groups[key] = {
                'akun_aset_id': asset.akun_aset_id,
                'akun_akumulasi_id': asset.akun_akumulasi_id,
                'nilai_perolehan': Decimal('0'),
                'akumulasi_penyusutan': Decimal('0'),
                'asset_count': 0,
            }
        groups[key]['nilai_perolehan'] += Decimal(str(asset.nilai_perolehan or 0))
        groups[key]['akumulasi_penyusutan'] += Decimal(str(asset.akumulasi_penyusutan or 0))
        groups[key]['asset_count'] += 1

    # Get GL balance for each account
    per_akun = []
    total_nilai_perolehan = Decimal('0')
    total_akumulasi_penyusutan = Decimal('0')
    total_asset_count = 0

    for key, group in groups.items():
        akun_aset_id = group['akun_aset_id']
        akun_akumulasi_id = group['akun_akumulasi_id']

        # Get COA info
        akun_aset = db.get(AkunPerkiraan, akun_aset_id) if akun_aset_id else None
        akun_akumulasi = db.get(AkunPerkiraan, akun_akumulasi_id) if akun_akumulasi_id else None

        # GL balance: untuk akun aset (DEBIT normal), saldo = debit - kredit
        gl_cost_raw = _get_gl_balance(db, akun_aset_id, as_of) if akun_aset_id else Decimal('0')
        # Untuk akun akumulasi penyusutan (KREDIT normal), saldo = kredit - debit = -(debit - kredit)
        gl_accum_raw = _get_gl_balance(db, akun_akumulasi_id, as_of) if akun_akumulasi_id else Decimal('0')
        gl_accum = -gl_accum_raw  # convert to positive (kredit normal)

        register_cost = group['nilai_perolehan']
        register_accum = group['akumulasi_penyusutan']

        selisih_cost = register_cost - gl_cost_raw  # both should be positive (DEBIT normal)
        selisih_accum = register_accum - gl_accum  # both should be positive

        per_akun.append({
            'akun_aset_id': str(akun_aset_id) if akun_aset_id else None,
            'akun_aset_kode': akun_aset.kode if akun_aset else '-',
            'akun_aset_nama': akun_aset.nama if akun_aset else '-',
            'nilai_perolehan_register': str(register_cost),
            'saldo_gl_cost': str(gl_cost_raw),
            'selisih_cost': str(selisih_cost),
            'akun_akumulasi_id': str(akun_akumulasi_id) if akun_akumulasi_id else None,
            'akun_akumulasi_kode': akun_akumulasi.kode if akun_akumulasi else '-',
            'akun_akumulasi_nama': akun_akumulasi.nama if akun_akumulasi else '-',
            'akumulasi_penyusutan_register': str(register_accum),
            'saldo_gl_accum': str(gl_accum),
            'selisih_accum': str(selisih_accum),
            'asset_count': group['asset_count'],
        })

        total_nilai_perolehan += register_cost
        total_akumulasi_penyusutan += register_accum
        total_asset_count += group['asset_count']

    return {
        'basis': 'CURRENT_ASSET_REGISTER_VS_ALL_POSTED_JOURNALS',
        'as_of': as_of.isoformat() if as_of else None,
        'summary': {
            'total_nilai_perolehan_register': str(total_nilai_perolehan),
            'total_akumulasi_penyusutan_register': str(total_akumulasi_penyusutan),
            'total_nilai_buku_register': str(total_nilai_perolehan - total_akumulasi_penyusutan),
            'total_asset_count': total_asset_count,
        },
        'per_akun': per_akun,
        'catatan': (
            'Selisih dapat berasal dari: '
            '(1) saldo awal aset yang belum di-link ke jurnal saldo awal, '
            '(2) capitalization event yang belum dipost, '
            '(3) disposal event yang belum dipost, '
            '(4) mapping akun aset/akumulasi yang berubah setelah capitalization, '
            '(5) jurnal manual yang langsung debit/kredit akun aset tanpa via AssetEvent. '
            'Tidak ada koreksi otomatis — selisih harus dianalisis manual.'
        ),
    }


def get_ringkasan_rekonsiliasi_aset(
    db: Session,
    as_of: Optional[datetime] = None,
) -> Dict:
    """Ringkasan rekonsiliasi aset (summary only, tanpa detail per akun).

    Dipakai di dashboard / health check.
    """
    full = get_rekonsiliasi_aset(db, as_of)
    return {
        'basis': full['basis'],
        'as_of': full['as_of'],
        'summary': full['summary'],
        'reconciliation_status': (
            'MATCH' if all(
                Decimal(p['selisih_cost']) == 0 and Decimal(p['selisih_accum']) == 0
                for p in full['per_akun']
            ) and full['per_akun']
            else 'MISMATCH'
        ),
        'akun_count': len(full['per_akun']),
    }
