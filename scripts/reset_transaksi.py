#!/usr/bin/env python3
"""
reset_transaksi.py — Reset data transaksi ASAHI BOOKS ERP
==========================================================

Menghapus SEMUA data transaksi hasil test per modul (jurnal, penjualan
(termasuk Penawaran & Tukar Faktur), pembelian, kas & bank, persediaan,
aset tetap, pelunasan, workflow, histori dokumen terhapus, dsb.) TANPA
menyentuh:

  - Akun Perkiraan (COA) + snapshot saldo awalnya
  - Setting Akun + Setting Aplikasi global (app_setting, mis. metode
    valuasi)
  - Master data menu Pengaturan (Pelanggan, Supplier, Barang + konversi
    satuan, Satuan, Gudang, Kategori Barang, Kategori Aset, Syarat Bayar,
    Biaya Tambahan, Kas/Bank Akun, Karyawan)
  - Pengguna + password + RBAC (roles, permissions, override)
  - Organisasi + Klasifikasi Arus Kas

Semua penghapusan berjalan dalam SATU TRANSAKSI database. Jumlah baris
semua tabel master dihitung sebelum & sesudah; kalau ada yang berubah,
seluruh operasi di-ROLLBACK otomatis (tidak ada setengah-reset).

Usage:
    cd project-erp-asahi-backend
    python3 scripts/reset_transaksi.py --dry-run    # lihat rencana dulu
    python3 scripts/reset_transaksi.py              # interaktif (ketik RESET)
    python3 scripts/reset_transaksi.py --yes        # tanpa konfirmasi
    python3 scripts/reset_transaksi.py --list       # lihat klasifikasi tabel

    # Windows (PowerShell):
    python scripts\\reset_transaksi.py --dry-run

    # Dengan Poetry:
    poetry run python scripts/reset_transaksi.py --dry-run

Opsi:
    --db URL              Override DATABASE_URL untuk sekali jalan.
    --dry-run             Tampilkan rencana + jumlah baris, tanpa menghapus.
    --yes                 Langsung eksekusi tanpa konfirmasi interaktif.
    --keep-stok           Jangan sentuh modul persediaan sama sekali
                          (dokumen stok, mutasi, kartu stok, saldo gudang,
                          dan kolom barang.stok). Berguna kalau saldo awal
                          stok sudah di-set rapi dan tidak mau diulang.
                          Catatan: link Sales Order di permintaan_barang
                          (kolom sales_order_id) otomatis di-NULL-kan karena
                          dokumen SO ikut dihapus.
    --reset-saldo-awal    Selain menghapus jurnal saldo awal, snapshot
                          akun_perkiraan.saldo juga dinol-kan.
    --reset-kas-saldo     Nol-kan snapshot saldo kas/bank (kas_bank_akun.saldo).
    --list                Tampilkan klasifikasi tabel KEEP/WIPE lalu keluar.

Efek setelah reset (yang diharapkan):
    - Nomor dokumen otomatis mulai dari -001 lagi (dihitung dari data
      tersisa di database) — termasuk Penawaran (PEN-) dan Tukar Faktur
      (TF-).
    - Semua periode kembali terbuka (penutupan_periode dihapus).
    - Halaman Saldo Awal tampak "belum di-set" karena jurnal SALDO_AWAL
      ikut terhapus — input ulang kapan saja lewat menu.
    - Idempotency-Key lama bisa dipakai ulang (cache idempotent_operation
      dikosongkan).
    - Login user / password / role tidak berubah sama sekali.

Changelog:
    2026-10-01  Selaras dengan update #3–#5:
                + WIPE : penawaran, penawaran_detail (update #3),
                         tukar_faktur, tukar_faktur_detail (update #4)
                + KEEP : app_setting (update #2 — setting global,
                         mis. metode valuasi)
                + --keep-stok kini otomatis me-NULL-kan
                  permintaan_barang.sales_order_id (link SO update #4)
                  karena tabel sales_order ikut dihapus.

Catatan keamanan:
    - Script TIDAK perlu dijalankan bersamaan dengan backend berhenti,
      tapi pastikan tidak ada user yang sedang aktif melakukan transaksi.
    - Tabel baru dari migrasi masa depan yang belum terdaftar di klasifikasi
      akan membuat script MENOLAK JALAN (fail-safe) — tambahkan dulu ke
      KEEP_TABLES / WIPE_TABLES di bawah.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Tambahkan root project ke sys.path (sama pola dengan bootstrap_admin.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ============================================================
# KLASIFIKASI TABEL — sumber kebenaran script ini
# ============================================================

# Tabel modul persediaan yang bisa DIPERTAHANKAN lewat --keep-stok
STOK_TABLES = frozenset({
    "permintaan_barang",
    "pemindahan_barang",
    "penyesuaian_stok",
    "stok_mutasi",
    "stok_kartu_layer",
    "stock_balance",
})

# Master / konfigurasi — TIDAK PERNAH disentuh
KEEP_TABLES = frozenset({
    # COA & setting akuntansi + setting aplikasi global
    "akun_perkiraan",
    "setting_akun",
    "app_setting",  # key-value global (update #2: METODE_VALUASI, dsb.)
    # Master data (menu Pengaturan)
    "kategori_barang",
    "satuan",
    "gudang",
    "kategori_aset",
    "syarat_bayar",
    "biaya_tambahan",
    "pelanggan",
    "supplier",
    "barang",
    "barang_satuan",
    "kas_bank_akun",
    "karyawan",
    # Pengguna & RBAC v2
    "pengguna",
    "roles",
    "permissions",
    "role_permissions",
    "user_roles",
    "user_permission_overrides",
    "access_audit_logs",
    # Organisasi & klasifikasi arus kas
    "organization_unit",
    "cash_flow_classification",
})

# Transaksi — dikosongkan saat reset
WIPE_TABLES = frozenset({
    # Jurnal
    "jurnal_umum",
    "jurnal_detail",
    # Kas & Bank
    "penerimaan_kas",
    "penerimaan_rincian",
    "pembayaran_kas",
    "pembayaran_rincian",
    "transfer_bank",
    "rekonsiliasi_bank",
    "rekonsiliasi_bank_detail",
    # Penjualan (Penawaran & Tukar Faktur termasuk — update #3/#4)
    "penawaran",
    "penawaran_detail",
    "sales_order",
    "sales_order_detail",
    "pengiriman_barang",
    "pengiriman_barang_detail",
    "sales_invoice",
    "sales_invoice_detail",
    "sales_retur",
    "sales_retur_detail",
    "tukar_faktur",
    "tukar_faktur_detail",
    # Pembelian
    "purchase_order",
    "purchase_order_detail",
    "penerimaan_barang",
    "penerimaan_barang_detail",
    "purchase_invoice",
    "purchase_invoice_detail",
    "purchase_retur",
    "purchase_retur_detail",
    "purchase_invoice_receipt_match",
    # Aset Tetap
    "aset_tetap",
    "asset_event",
    # Pelunasan, workflow, histori, audit transaksional, dsb.
    "transaksi_biaya",
    "payment_allocation",
    "penutupan_periode",
    "document_workflow",
    "workflow_event",
    "idempotent_operation",
    "deleted_document_log",
    "document_organization",
    "reporting_audit",
}) | STOK_TABLES

# Grup tampilan (urutan yang rapi untuk output konsol)
WIPE_GROUPS = [
    ("Jurnal", ["jurnal_umum", "jurnal_detail"]),
    ("Kas & Bank", [
        "penerimaan_kas", "penerimaan_rincian", "pembayaran_kas", "pembayaran_rincian",
        "transfer_bank", "rekonsiliasi_bank", "rekonsiliasi_bank_detail",
    ]),
    ("Penjualan", [
        "penawaran", "penawaran_detail",
        "sales_order", "sales_order_detail", "pengiriman_barang", "pengiriman_barang_detail",
        "sales_invoice", "sales_invoice_detail", "sales_retur", "sales_retur_detail",
        "tukar_faktur", "tukar_faktur_detail",
    ]),
    ("Pembelian", [
        "purchase_order", "purchase_order_detail", "penerimaan_barang", "penerimaan_barang_detail",
        "purchase_invoice", "purchase_invoice_detail", "purchase_retur", "purchase_retur_detail",
        "purchase_invoice_receipt_match",
    ]),
    ("Persediaan", [
        "permintaan_barang", "pemindahan_barang", "penyesuaian_stok",
        "stok_mutasi", "stok_kartu_layer", "stock_balance",
    ]),
    ("Aset Tetap", ["aset_tetap", "asset_event"]),
    ("Pelunasan / Workflow / Histori", [
        "transaksi_biaya", "payment_allocation", "penutupan_periode",
        "document_workflow", "workflow_event", "idempotent_operation",
        "deleted_document_log", "document_organization", "reporting_audit",
    ]),
]

KEEP_GROUPS = [
    ("COA & Setting", ["akun_perkiraan", "setting_akun", "app_setting"]),
    ("Master Data", [
        "barang", "barang_satuan", "pelanggan", "supplier", "kategori_barang",
        "satuan", "gudang", "kategori_aset", "syarat_bayar", "biaya_tambahan",
        "karyawan",
    ]),
    ("Kas/Bank Akun & Pengguna", ["kas_bank_akun", "pengguna"]),
    ("RBAC (role & permission)", [
        "roles", "permissions", "role_permissions", "user_roles",
        "user_permission_overrides", "access_audit_logs",
    ]),
    ("Organisasi", ["organization_unit", "cash_flow_classification"]),
]


class SafetyAbort(Exception):
    """Dilempar ketika verifikasi keamanan gagal -> seluruh transaksi di-rollback."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reset_transaksi.py",
        description="Reset data transaksi Asahi Books ERP (master & pengaturan tetap utuh).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh:\n"
            "  python3 scripts/reset_transaksi.py --dry-run\n"
            "  python3 scripts/reset_transaksi.py --yes\n"
            "  python3 scripts/reset_transaksi.py --keep-stok --yes\n"
            "  python3 scripts/reset_transaksi.py --db postgresql://user:pass@host:5432/dbname\n"
        ),
    )
    parser.add_argument(
        "--db", default=None,
        help="Override DATABASE_URL sekali jalan (contoh: postgresql://asahi_dev:asahi_dev_123@localhost:5432/asahi_erp_dev)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Tampilkan rencana & jumlah baris tanpa menghapus apa pun")
    parser.add_argument("--yes", action="store_true", help="Langsung eksekusi tanpa konfirmasi interaktif")
    parser.add_argument(
        "--keep-stok", action="store_true",
        help="Jangan sentuh modul persediaan (dokumen stok, mutasi, kartu stok, saldo gudang, barang.stok)",
    )
    parser.add_argument(
        "--reset-saldo-awal", action="store_true",
        help="Nol-kan snapshot akun_perkiraan.saldo (default: dipertahankan)",
    )
    parser.add_argument(
        "--reset-kas-saldo", action="store_true",
        help="Nol-kan snapshot saldo kas/bank (kas_bank_akun.saldo)",
    )
    parser.add_argument("--list", action="store_true", help="Tampilkan klasifikasi tabel KEEP/WIPE lalu keluar")
    return parser.parse_args()


def _count(conn, table: str) -> int:
    from sqlalchemy import text
    return int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())


def _delete_order(metadata, wipe: frozenset[str]) -> list[str]:
    """Urutan DELETE child-dulu (tabel yang MERUJUK sebelum tabel yang DIRUJUK).

    Dihitung otomatis dari graf FK di metadata, jadi selalu benar walau
    schema berkembang. Self-reference (mis. jurnal_umum.reversal_of_id)
    diabaikan — satu statement DELETE FROM menghapus sekaligus sehingga
    tidak menyisakan referensi menggantung.
    """
    import graphlib

    graph: dict[str, set[str]] = {}
    for t in wipe:
        deps: set[str] = set()
        for fk in metadata.tables[t].foreign_keys:
            ref = fk.column.table.name
            if ref in wipe and ref != t:
                deps.add(ref)
        graph[t] = deps

    try:
        # static_order: tabel yang DIRUJUK duluan -> dibalik agar child duluan
        order = list(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as exc:
        raise SystemExit(
            f"[ERROR] Siklus FK antar tabel transaksi terdeteksi: {exc}\n"
            "         Hapus manual dengan DELETE berurutan atau laporkan ke developer."
        )
    order.reverse()
    return order


def _fk_keep_to_wipe(metadata, keep: frozenset[str], wipe: frozenset[str]) -> list[tuple[str, str]]:
    """Kolom FK di tabel KEEP yang menunjuk tabel WIPE (harus di-NULL-kan dulu).

    Hanya relevan pada mode --keep-stok (mis. penyesuaian_stok.jurnal_umum_id
    -> jurnal_umum). Kalau ada yang NOT NULL, script menolak jalan karena
    data yang dipertahankan akan menggantung.
    """
    out: list[tuple[str, str]] = []
    for t in sorted(keep):
        for fk in metadata.tables[t].foreign_keys:
            if fk.column.table.name in wipe:
                col = fk.parent
                if not col.nullable:
                    raise SystemExit(
                        f"[ERROR] Kolom {t}.{col.name} NOT NULL dan menunjuk tabel transaksi "
                        f"({fk.column.table.name}) yang akan dihapus. Mode --keep-stok tidak aman "
                        "untuk schema ini — jalankan tanpa --keep-stok."
                    )
                out.append((t, col.name))
    return out


def _sequences(conn, wipe: frozenset[str]) -> list[str]:
    """Daftar sequence milik tabel yang dihapus (untuk RESTART dari 1)."""
    from sqlalchemy import text
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT s.relname
            FROM pg_class s
            JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
            JOIN pg_class t ON t.oid = d.refobjid
            WHERE s.relkind = 'S'
              AND t.relname = ANY(:tbls)
              AND t.relnamespace = current_schema()::regnamespace
            """
        ),
        {"tbls": sorted(wipe)},
    ).scalars().all()
    return sorted(rows)


def _print_header(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def _print_plan(
    args: argparse.Namespace,
    masked_url: str,
    wipe: frozenset[str],
    keep: frozenset[str],
    wipe_counts: dict[str, int],
    keep_counts: dict[str, int],
    null_fks: list[tuple[str, str]],
    sequences: list[str],
    delete_order: list[str],
) -> None:
    _print_header("ASAHI BOOKS ERP — RESET DATA TRANSAKSI")
    print(f"  Database : {masked_url}")
    print(f"  Mode     : {'KEEP-STOK (persediaan dipertahankan)' if args.keep_stok else 'RESET PENUH (stok juga direset)'}")
    print(f"  Wipe     : {len(wipe)} tabel transaksi")
    print(f"  Keep     : {len(keep)} tabel master/pengaturan")

    print("\n  YANG AKAN DIHAPUS (data transaksi):")
    for group_name, tables in WIPE_GROUPS:
        tables = [t for t in tables if t in wipe]
        if not tables:
            continue
        shown = [f"{t} ({wipe_counts[t]})" for t in tables if wipe_counts.get(t, 0) > 0]
        empty_n = len([t for t in tables if wipe_counts.get(t, 0) == 0])
        line = f"    {group_name:<34}"
        if shown:
            print(f"{line}{', '.join(shown)}")
        else:
            print(f"{line}(semua sudah kosong)")
        if empty_n and not shown:
            pass
    total_rows = sum(wipe_counts.values())
    nonempty = len([t for t, c in wipe_counts.items() if c > 0])
    print(f"\n    TOTAL: {total_rows} baris di {nonempty} tabel berisi data "
          f"({len(wipe) - nonempty} tabel sudah kosong)")

    print("\n  YANG DIPERTAHANKAN (tidak disentuh):")
    for group_name, tables in KEEP_GROUPS:
        tables = [t for t in tables if t in keep]
        if not tables:
            continue
        shown = ", ".join(f"{t} ({keep_counts[t]})" for t in tables)
        print(f"    {group_name:<34}{shown}")
    if args.keep_stok:
        stok_kept = [t for t in STOK_TABLES if t in keep]
        shown = ", ".join(f"{t} ({keep_counts[t]})" for t in stok_kept)
        print(f"    {'Persediaan (--keep-stok)':<34}{shown}")

    print("\n  TINDAKAN TAMBAHAN:")
    if null_fks:
        for t, c in null_fks:
            print(f"    - {t}.{c} di-NULL-kan (data stok yang dipertahankan tidak menggantung)")
    else:
        print("    - (tidak ada FK yang perlu di-NULL-kan)")
    if not args.keep_stok:
        print("    - barang.stok dinol-kan (snapshot stok hasil test)")
    else:
        print("    - barang.stok DIPERTAHANKAN (--keep-stok)")
    if sequences:
        print(f"    - {len(sequences)} sequence ID transaksi restart dari 1")
    if args.reset_saldo_awal:
        print("    - akun_perkiraan.saldo dinol-kan (--reset-saldo-awal)")
    else:
        print("    - snapshot saldo awal COA dipertahankan (jurnal SALDO_AWAL ikut terhapus)")
    if args.reset_kas_saldo:
        print("    - kas_bank_akun.saldo dinol-kan (--reset-kas-saldo)")
    else:
        print("    - snapshot saldo kas/bank dipertahankan")

    if delete_order:
        print(f"\n  Urutan hapus (child dulu): {' > '.join(delete_order[:8])}"
              + (" > ..." if len(delete_order) > 8 else ""))


def _confirm(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    try:
        jawab = input("\n  Ketik RESET (huruf besar) untuk melanjutkan, Enter/Ctrl+C untuk batal: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return jawab.strip() == "RESET"


def main() -> int:
    args = parse_args()

    # Override env SEBELUM import app.* (config di-cache dengan lru_cache)
    if args.db:
        os.environ["DATABASE_URL"] = args.db

    # Import ditunda supaya override --db efektif.
    # pkgutil dipakai (bukan cuma `import app.models`) karena ada model yang
    # TIDAK diimport di app/models/__init__.py (mis. rekonsiliasi_bank) —
    # walk_packages memastikan SEMUA tabel terdaftar di metadata.
    import importlib
    import pkgutil

    from sqlalchemy import create_engine, text
    from app.config import get_settings
    from app.database import BaseModel
    import app.models as models_pkg  # noqa: F401

    for mod in pkgutil.walk_packages(models_pkg.__path__, prefix="app.models."):
        importlib.import_module(mod.name)

    metadata = BaseModel.metadata
    metadata_tables = set(metadata.tables.keys())

    # ---------- Validasi klasifikasi (fail-safe) ----------
    known = KEEP_TABLES | WIPE_TABLES
    unknown = metadata_tables - known
    if unknown:
        print("[ERROR] Tabel di schema belum terklasifikasi (tambahkan ke KEEP_TABLES / WIPE_TABLES):")
        for t in sorted(unknown):
            print(f"        - {t}")
        return 2
    missing = known - metadata_tables
    if missing:
        print("[ERROR] Tabel di klasifikasi tidak ada di schema (migrasi belum jalan?):")
        for t in sorted(missing):
            print(f"        - {t}")
        return 2

    keep = frozenset(KEEP_TABLES)
    wipe = frozenset(WIPE_TABLES)
    if args.keep_stok:
        keep = keep | STOK_TABLES
        wipe = wipe - STOK_TABLES

    if args.list:
        _print_header("KLASIFIKASI TABEL")
        print(f"  KEEP ({len(keep)}):")
        for g, tables in KEEP_GROUPS:
            tables = [t for t in tables if t in keep]
            if tables:
                print(f"    {g:<34}{', '.join(tables)}")
        if args.keep_stok:
            print(f"    {'Persediaan (--keep-stok)':<34}{', '.join(sorted(STOK_TABLES))}")
        print(f"\n  WIPE ({len(wipe)}):")
        for g, tables in WIPE_GROUPS:
            tables = [t for t in tables if t in wipe]
            if tables:
                print(f"    {g:<34}{', '.join(tables)}")
        return 0

    # ---------- Siapkan engine ----------
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)
    masked_url = engine.url.render_as_string(hide_password=True)

    delete_order = _delete_order(metadata, wipe)
    null_fks = _fk_keep_to_wipe(metadata, keep, wipe)

    # ---------- Fase 1: baca (tanpa mengubah apa pun) ----------
    try:
        with engine.connect() as conn:
            wipe_counts = {t: _count(conn, t) for t in wipe}
            keep_counts = {t: _count(conn, t) for t in keep}
            sequences = _sequences(conn, wipe)
    except Exception as exc:
        print(f"[ERROR] Tidak bisa connect ke database: {exc}")
        print(f"        Target: {masked_url}")
        return 2

    _print_plan(args, masked_url, wipe, keep, wipe_counts, keep_counts, null_fks, sequences, delete_order)

    if args.dry_run:
        print("\n  [DRY-RUN] Tidak ada yang dihapus. Jalankan tanpa --dry-run untuk eksekusi.")
        return 0

    if not _confirm(args):
        print("  Dibatalkan — database tidak diubah.")
        return 1

    # ---------- Fase 2: eksekusi dalam SATU transaksi ----------
    try:
        with engine.connect() as conn:
            with conn.begin():
                # Hitung ulang di dalam transaksi (nilai otoritatif)
                keep_before = {t: _count(conn, t) for t in keep}

                # 1. NULL-kan FK dari tabel keep -> wipe (mode --keep-stok)
                for t, c in null_fks:
                    conn.execute(text(f'UPDATE "{t}" SET "{c}" = NULL'))

                # 2. DELETE berurutan child-dulu
                deleted_rows = 0
                touched_tables = 0
                for t in delete_order:
                    res = conn.execute(text(f'DELETE FROM "{t}"'))
                    if res.rowcount and res.rowcount > 0:
                        deleted_rows += res.rowcount
                        touched_tables += 1

                # 3. Restart sequence ID transaksi
                for seq in sequences:
                    conn.execute(text(f'ALTER SEQUENCE "{seq}" RESTART WITH 1'))

                # 4. Snapshot master hasil transaksi
                if not args.keep_stok:
                    conn.execute(text("UPDATE barang SET stok = 0"))
                if args.reset_saldo_awal:
                    conn.execute(text("UPDATE akun_perkiraan SET saldo = 0"))
                if args.reset_kas_saldo:
                    conn.execute(text("UPDATE kas_bank_akun SET saldo = 0"))

                # 5. Verifikasi: tabel master TIDAK BOLEH berubah jumlah barisnya
                keep_after = {t: _count(conn, t) for t in keep}
                changed = {t: (keep_before[t], keep_after[t]) for t in keep if keep_before[t] != keep_after[t]}
                if changed:
                    raise SafetyAbort(
                        "Tabel master berubah jumlah baris (seharusnya tidak disentuh): "
                        + ", ".join(f"{t}: {b} -> {a}" for t, (b, a) in changed.items())
                    )

                # 6. Verifikasi: semua tabel wipe benar-benar kosong
                leftover = {t: _count(conn, t) for t in wipe}
                leftover = {t: c for t, c in leftover.items() if c > 0}
                if leftover:
                    raise SafetyAbort(
                        "Masih ada baris tersisa di tabel transaksi: "
                        + ", ".join(f"{t}: {c}" for t, c in leftover.items())
                    )
    except SafetyAbort as exc:
        # conn.begin() context sudah me-ROLLBACK otomatis
        print(f"\n[ERROR] Verifikasi keamanan gagal — SEMUA perubahan di-ROLLBACK.")
        print(f"        {exc}")
        return 2
    except KeyboardInterrupt:
        print("\n[ERROR] Dibatalkan di tengah jalan — SEMUA perubahan di-ROLLBACK.")
        return 1
    except Exception as exc:
        print(f"\n[ERROR] Gagal & di-ROLLBACK: {exc}")
        return 2

    # ---------- Ringkasan hasil ----------
    _print_header("RESET SELESAI")
    print(f"  [OK] {deleted_rows} baris dihapus dari {touched_tables} tabel")
    print(f"  [OK] {len(keep)} tabel master/pengaturan terverifikasi tidak berubah")
    if sequences:
        print(f"  [OK] {len(sequences)} sequence ID transaksi restart dari 1")
    if null_fks:
        for t, c in null_fks:
            print(f"  [OK] {t}.{c} di-NULL-kan (data stok dipertahankan)")
    print()
    print("  Selanjutnya:")
    print("   - Login tetap pakai user & password yang sama (tidak berubah).")
    print("   - Nomor dokumen otomatis mulai dari -001 lagi.")
    print("   - Halaman Saldo Awal akan tampak 'belum di-set' — input ulang kapan saja.")
    print("   - Kalau tampilan laporan masih memuat data lama, refresh / restart backend")
    print("     untuk membersihkan cache memori.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
