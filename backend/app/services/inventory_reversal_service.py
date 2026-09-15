"""inventory_reversal_service.py

Service untuk reverse stock movement (mutasi stok) dengan restore exact layer
FIFO/FEFO. Sesuai Master Roadmap §10:
- "Reversal exact value/layer" — reversal harus restore exact layer yang
  dikonsumsi saat mutasi asli, bukan re-costing dengan harga sekarang.
- "Reversal mempertahankan audit trail" — setiap reversal punya link ke
  mutasi asli (via reversal_of_id di StokMutasi).

Logic:
1. Identifikasi mutasi asli (StokMutasi yang akan di-reverse)
2. Validasi:
   - Mutasi asli belum di-reverse (reversal_of_id == None pada mutasi lain)
   - Stok cukup untuk restore (kalau asli KELUAR → restore MASUK, harus
     pasti qty gudang cukup — kecuali ada transaksi lain setelahnya yang
     juga mengurangi stok)
3. Reverse:
   - Kalau asli MASUK: balik ke KELUAR, kurangi stok, restore layer
     (khusus FIFO/FEFO: hapus layer yang dibuat saat mutasi asli)
   - Kalau asli KELUAR: balik ke MASUK, tambah stok, restore layer
     (khusus FIFO/FEFO: re-create layer dengan cost_parts asli yang
     dikonsumsi — preserve original harga_satuan & tanggal_masuk)
4. Set reversal_of_id di mutasi reversal supaya link ke mutasi asli
5. Reverse stock balance (qty + nilai)

Dipanggil oleh:
- reverse_pemindahan (untuk pemindahan barang yang sudah approved)
- Future: reverse delivery, reverse receipt, reverse adjustment (Phase 4/5)

Catatan: Untuk transaksi yang juga generate jurnal (mis. delivery, receipt),
caller harus juga reverse jurnal terpisah via posting_service.reverse_journal().
Service ini hanya handle stock movement saja.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.transaksi.stok_mutasi import StokMutasi, TipeMutasiStok
from app.models.transaksi.stok_kartu_layer import StokKartuLayer
from app.models.transaksi.stock_balance import StockBalance
from app.models.master.barang import Barang
from app.services.accounting_control import atomic_accounting_write


# ==========================================
# Helper
# ==========================================
def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _is_masuk(tipe: TipeMutasiStok) -> bool:
    """True kalau tipe mutasi adalah masuk (stock-in)."""
    return tipe in (
        TipeMutasiStok.MASUK,
        TipeMutasiStok.PENYESUAIAN_TAMBAH,
        TipeMutasiStok.PEMINDAHAN_MASUK,
    )


def _opposite_tipe(tipe: TipeMutasiStok) -> TipeMutasiStok:
    """Tipe mutasi kebalikan dari yang diberikan."""
    opposite_map = {
        TipeMutasiStok.MASUK: TipeMutasiStok.KELUAR,
        TipeMutasiStok.KELUAR: TipeMutasiStok.MASUK,
        TipeMutasiStok.PENYESUAIAN_TAMBAH: TipeMutasiStok.PENYESUAIAN_KURANG,
        TipeMutasiStok.PENYESUAIAN_KURANG: TipeMutasiStok.PENYESUAIAN_TAMBAH,
        TipeMutasiStok.PEMINDAHAN_KELUAR: TipeMutasiStok.PEMINDAHAN_MASUK,
        TipeMutasiStok.PEMINDAHAN_MASUK: TipeMutasiStok.PEMINDAHAN_KELUAR,
    }
    return opposite_map.get(tipe, TipeMutasiStok.MASUK)


# ==========================================
# Public API
# ==========================================
@atomic_accounting_write
def reverse_stock_movement(
    db: Session,
    original_mutasi_id: UUID,
    user_id: UUID,
    reason: str = "Pembatalan",
) -> StokMutasi:
    """Reverse satu mutasi stok dengan restore exact layer FIFO/FEFO.

    Logic per metode valuasi:
    - AVERAGE: tambah/kurangi stok & nilai sesuai mutasi asli. Tidak ada layer.
    - FIFO/FEFO:
      * Kalau asli MASUK (created layer) → hapus layer tsb kalau qty_sisa masih
        full. Kalau sudah terkonsumsi sebagian, restore dengan qty yang sesuai.
      * Kalau asli KELUAR (consumed layer) → re-create layer dengan cost_parts
        asli (preserve harga_satuan & tanggal_masuk asli).

    Parameter:
        db: SQLAlchemy Session
        original_mutasi_id: UUID dari StokMutasi yang akan di-reverse
        user_id: UUID user yang melakukan reversal (untuk audit trail)
        reason: Alasan reversal (mis. "Pembatalan pemindahan TRF-STK-...")

    Return:
        StokMutasi reversal (sudah committed, dengan reversal_of_id terisi)

    Raises:
        ValueError kalau:
        - Mutasi asli tidak ditemukan
        - Mutasi asli sudah di-reverse (one-reversal policy)
        - Stok tidak mencukupi untuk reverse (mis. stok sudah terpakai transaksi lain)
    """
    # === 1. Load original mutasi dengan FOR UPDATE supaya lock ===
    original = (
        db.query(StokMutasi)
        .filter(StokMutasi.id == original_mutasi_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if original is None:
        raise ValueError(f"Mutasi stok dengan ID {original_mutasi_id} tidak ditemukan")

    # === 2. Cek apakah sudah di-reverse ===
    existing_reversal = (
        db.query(StokMutasi)
        .filter(StokMutasi.reversal_of_id == original.id)
        .first()
    )
    if existing_reversal:
        raise ValueError(
            f"Mutasi {original.ref_no} sudah pernah di-reverse oleh mutasi "
            f"{existing_reversal.ref_no}. One-reversal policy."
        )

    barang = db.get(Barang, original.barang_id)
    if barang is None:
        raise ValueError(f"Barang dengan ID {original.barang_id} tidak ditemukan")

    # === 3. Load stock balance (gudang asal) ===
    key = str(original.gudang_id) if original.gudang_id else 'UNASSIGNED'
    pos = (
        db.query(StockBalance)
        .filter_by(barang_id=original.barang_id, location_key=key)
        .with_for_update()
        .first()
    )
    if pos is None:
        # Auto-create empty balance kalau belum ada (kasus edge)
        pos = StockBalance(
            barang_id=original.barang_id,
            gudang_id=original.gudang_id,
            location_key=key,
            qty=0,
            nilai=Decimal(0),
        )
        db.add(pos)
        db.flush()

    # === 4. Reverse logic per arah mutasi ===
    original_is_masuk = _is_masuk(original.tipe)
    reverse_tipe = _opposite_tipe(original.tipe)
    qty = original.qty
    total_nilai = original.total_nilai or Decimal(0)
    harga_satuan = original.harga_satuan

    # Validate stock availability untuk reversal MASUK→KELUAR
    # (kalau asli MASUK, reversal akan KELUAR — cek stok cukup)
    if original_is_masuk and qty > pos.qty:
        raise ValueError(
            f"Tidak bisa reverse mutasi MASUK {original.ref_no}: stok gudang "
            f"tidak mencukupi ({pos.qty} < {qty} yang perlu dikurangi). "
            f"Mungkin stok sudah terpakai transaksi lain setelah mutasi asli."
        )

    # Load cost_parts asli (untuk restore layer FIFO/FEFO)
    cost_parts = original.cost_parts or []
    metode = getattr(barang.metode_valuasi, 'value', barang.metode_valuasi) or 'AVERAGE'

    if metode != 'AVERAGE':
        if original_is_masuk:
            # === Asli MASUK (created layer) → Reverse: KELUAR (hapus layer) ===
            # Cari layer yang dibuat saat mutasi asli (by ref_id + ref_module)
            layers_to_remove = (
                db.query(StokKartuLayer)
                .filter(
                    StokKartuLayer.barang_id == original.barang_id,
                    StokKartuLayer.gudang_id == original.gudang_id,
                    StokKartuLayer.ref_id == original.ref_id,
                    StokKartuLayer.ref_module == original.ref_module,
                    StokKartuLayer.qty_sisa > 0,
                )
                .all()
            )
            total_layer_qty = sum(l.qty_sisa for l in layers_to_remove)
            if total_layer_qty < qty:
                # Sebagian layer sudah dikonsumsi — restore sisa yang ada,
                # sisanya biarkan (tidak bisa hapus layer yang sudah terpakai)
                logger.warning(
                    f"Reverse mutasi MASUK {original.ref_no}: hanya bisa hapus "
                    f"{total_layer_qty} dari {qty} qty layer (sisanya sudah terpakai)"
                )
            # Hapus (kurangi qty_sisa) sesuai urutan layer
            remaining = qty
            for layer in layers_to_remove:
                take = min(remaining, layer.qty_sisa)
                layer.qty_sisa -= take
                remaining -= take
                if remaining <= 0:
                    break
        else:
            # === Asli KELUAR (consumed layer) → Reverse: MASUK (re-create layer) ===
            # Re-create layer dengan cost_parts asli (preserve harga & tanggal)
            for part in cost_parts:
                # cost_parts adalah list of dict: {qty, harga, expiry, tanggal}
                part_qty = int(part.get('qty', 0))
                part_harga = Decimal(str(part.get('harga', 0)))
                part_expiry = part.get('expiry')
                part_tanggal_str = part.get('tanggal')
                # Parse tanggal kalau string ISO
                if isinstance(part_tanggal_str, str):
                    try:
                        part_tanggal = datetime.fromisoformat(part_tanggal_str)
                    except ValueError:
                        part_tanggal = original.created_at or datetime.now(timezone.utc)
                else:
                    part_tanggal = part_tanggal_str or original.created_at or datetime.now(timezone.utc)

                db.add(StokKartuLayer(
                    barang_id=original.barang_id,
                    gudang_id=original.gudang_id,
                    qty_masuk=part_qty,
                    qty_sisa=part_qty,
                    harga_satuan=part_harga,
                    tanggal_masuk=part_tanggal,
                    tanggal_kedaluwarsa=part_expiry,
                    # Reversal punya ref_module yang sama dengan original
                    ref_module=original.ref_module,
                    ref_no=f"REV-{original.ref_no}" if original.ref_no else None,
                    ref_id=original.ref_id,
                ))

    # === 5. Reverse stock balance ===
    if original_is_masuk:
        # Asli MASUK → reverse: KELUAR
        pos.qty -= qty
        pos.nilai -= total_nilai
    else:
        # Asli KELUAR → reverse: MASUK
        pos.qty += qty
        pos.nilai += total_nilai
    db.flush()

    # === 6. Update master barang.stok ===
    if original_is_masuk:
        barang.stok -= qty
    else:
        barang.stok += qty
    # Recalculate harga_pokok untuk AVERAGE
    if metode == 'AVERAGE' and barang.stok > 0:
        # harga_pokok = total_nilai saat ini / stok saat ini
        # Setelah reversal, total nilai sudah di-update di pos.nilai (per gudang).
        # Untuk master, gunakan sum semua gudang.
        total_global_nilai = (
            db.query(func.sum(StockBalance.nilai))
            .filter_by(barang_id=barang.id)
            .scalar() or Decimal(0)
        )
        barang.harga_pokok = _money(total_global_nilai / barang.stok)
    db.flush()

    # === 7. Create reversal mutasi record ===
    reversal = StokMutasi(
        barang_id=original.barang_id,
        tipe=reverse_tipe,
        qty=qty,
        saldo_sebelum=pos.qty + qty if original_is_masuk else pos.qty - qty,  # before reverse
        saldo_sesudah=barang.stok,
        ref_module=original.ref_module,
        ref_no=f"REV-{original.ref_no}" if original.ref_no else None,
        ref_id=original.ref_id,
        gudang_id=original.gudang_id,
        keterangan=f"REVERSAL: {reason} (orig: {original.ref_no or str(original.id)[:8]})",
        harga_satuan=harga_satuan,
        total_nilai=total_nilai,
        saldo_nilai_sebelum=pos.nilai + total_nilai if original_is_masuk else pos.nilai - total_nilai,
        saldo_nilai_sesudah=pos.nilai,
        warehouse_qty_before=pos.qty + qty if original_is_masuk else pos.qty - qty,
        warehouse_qty_after=pos.qty,
        global_value_after=(
            db.query(func.sum(StockBalance.nilai)).filter_by(barang_id=barang.id).scalar()
            or Decimal(0)
        ),
        # Snapshot akun yang sama dengan asli
        inventory_account_id=original.inventory_account_id,
        expense_account_id=original.expense_account_id,
        # Cost parts (untuk FIFO/FEFO — preserve original layer info)
        cost_parts=cost_parts,
        # === Link ke mutasi asli ===
        reversal_of_id=original.id,
    )
    db.add(reversal)
    db.flush()

    logger.info(
        f"StokMutasi reversal created: ref={reversal.ref_no} | "
        f"original={original.ref_no} | tipe={reverse_tipe.value} | "
        f"qty={qty} | nilai={total_nilai} | gudang={original.gudang_id}"
    )
    return reversal


@atomic_accounting_write
def reverse_pemindahan(
    db: Session,
    pemindahan_id: UUID,
    user_id: UUID,
    reason: str = "Pembatalan pemindahan",
):
    """Reverse pemindahan barang yang sudah di-approve.

    Logic:
    1. Load PemindahanBarang, cek status == DISETUJUI (sudah approved)
    2. Cari 2 StokMutasi yang dibuat saat approve_pemindahan:
       - Pemindahan keluar (dari gudang asal) — ref_module=INVENTORY_TRANSFER
       - Pemindahan masuk (ke gudang tujuan) — ref_module=INVENTORY_TRANSFER
    3. Reverse kedua mutasi via reverse_stock_movement()
       - Reverse mutasi MASUK (ke gudang tujuan) → akan jadi KELUAR (hapus layer)
       - Reverse mutasi KELUAR (dari gudang asal) → akan jadi MASUK (re-create layer)
    4. Set status PemindahanBarang menjadi BATAL (atau REVERSED kalau perlu state baru)
    5. (Optional) Reverse jurnal kalau ada

    Note: Order reversal penting!
    - Pertama reverse mutasi MASUK (ke gudang tujuan) — karena ini hapus layer
      yang dibuat saat pemindahan. Kalau dilakukan duluan, gudang tujuan akan
      punya stok yang cukup untuk di-kurangi.
    - Kedua reverse mutasi KELUAR (dari gudang asal) — karena ini re-create
      layer di gudang asal. Aman dilakukan setelah gudang tujuan selesai.
    """
    from app.models.transaksi.persediaan.pemindahan_barang import (
        PemindahanBarang, ProsesPemindahan, StatusPersediaan,
    )

    pemindahan = (
        db.query(PemindahanBarang)
        .filter(PemindahanBarang.id == pemindahan_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if pemindahan is None:
        raise ValueError(f"Pemindahan dengan ID {pemindahan_id} tidak ditemukan")

    if pemindahan.status != StatusPersediaan.DISETUJUI:
        raise ValueError(
            f"Pemindahan {pemindahan.no_pemindahan} status={pemindahan.status.value}, "
            f"tidak bisa di-reverse. Hanya pemindahan DISETUJUI yang bisa di-reverse. "
            f"Untuk pembatalan sebelum approve, gunakan cancel_pemindahan."
        )

    # Cek apakah sudah di-reverse
    if getattr(pemindahan, 'reversed_at', None) is not None:
        raise ValueError(f"Pemindahan {pemindahan.no_pemindahan} sudah di-reverse")

    # Cari 2 mutasi yang dibuat saat approve_pemindahan
    # ref_id = pemindahan.id, ref_module = INVENTORY_TRANSFER
    mutasi_list = (
        db.query(StokMutasi)
        .filter(
            StokMutasi.ref_id == pemindahan.id,
            StokMutasi.ref_module == 'INVENTORY_TRANSFER',
            StokMutasi.reversal_of_id.is_(None),  # exclude existing reversal
        )
        .all()
    )

    if not mutasi_list:
        raise ValueError(
            f"Tidak ditemukan mutasi stok untuk pemindahan {pemindahan.no_pemindahan}. "
            f"Mungkin pemindahan belum di-approve atau mutasi sudah di-reverse."
        )

    # Pisahkan: mutasi keluar (dari gudang asal) vs mutasi masuk (ke gudang tujuan)
    mutasi_keluar = next(
        (m for m in mutasi_list if m.tipe == TipeMutasiStok.PEMINDAHAN_KELUAR), None
    )
    mutasi_masuk = next(
        (m for m in mutasi_list if m.tipe == TipeMutasiStok.PEMINDAHAN_MASUK), None
    )

    # === Reverse dalam urutan yang aman ===
    # 1. Reverse mutasi MASUK (ke gudang tujuan) → akan jadi KELUAR
    if mutasi_masuk:
        try:
            reverse_stock_movement(
                db, mutasi_masuk.id, user_id, reason=f"{reason} (masuk ke gudang tujuan)"
            )
        except ValueError as e:
            raise ValueError(
                f"Gagal reverse mutasi MASUK pemindahan {pemindahan.no_pemindahan}: {e}"
            ) from e

    # 2. Reverse mutasi KELUAR (dari gudang asal) → akan jadi MASUK
    if mutasi_keluar:
        try:
            reverse_stock_movement(
                db, mutasi_keluar.id, user_id, reason=f"{reason} (keluar dari gudang asal)"
            )
        except ValueError as e:
            raise ValueError(
                f"Gagal reverse mutasi KELUAR pemindahan {pemindahan.no_pemindahan}: {e}. "
                f"Mutasi MASUK sudah di-reverse tapi KELUAR gagal — perlu rekonsiliasi manual."
            ) from e

    # === Set status pemindahan ===
    # Untuk sekarang pakai status BATAL. Kalau perlu state terpisah (REVERSED),
    # tambah enum value di StatusPersediaan di phase berikutnya.
    pemindahan.status = StatusPersediaan.BATAL
    db.add(pemindahan)
    db.flush()

    logger.info(
        f"Pemindahan {pemindahan.no_pemindahan} reversed: "
        f"2 mutasi stok di-reverse, status diubah ke BATAL"
    )
    return pemindahan
