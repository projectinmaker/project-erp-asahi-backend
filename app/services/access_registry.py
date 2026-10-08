"""RBAC v2 — Registry permission, role template, dan endpoint→permission mapping.

Sumber tunggal (spesifikasi §5): backend mendefinisikan registry `module.resource.action`;
frontend memakai code yang sama via GET /access/permissions dan GET /auth/me/permissions.

Struktur:
- MODULES            : daftar modul (key → nama Indonesia).
- REGISTRY           : daftar (module, resource, action, nama, is_sensitive).
- ROLE_TEMPLATES     : template role (spesifikasi §6 + mockup WhatsApp).
- LEGACY_TEMPLATE    : mapping enum RolePengguna lama → template (parity migrasi §11).
- KIND_RESOURCE      : document_type workflow (nama tabel) → resource permission.
- infer_permission() : (router_module, endpoint_fn, method, path) → permission code.

Default deny: endpoint tanpa mapping hanya dapat diakses super admin (RBAC-13).
"""
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════
# Modul
# ═══════════════════════════════════════════════════════════════════════════

MODULES = {
    'system': 'Sistem',
    'master': 'Master Data',
    'sales': 'Penjualan',
    'purchase': 'Pembelian',
    'inventory': 'Persediaan',
    'finance': 'Kas & Bank',
    'accounting': 'Akuntansi',
    'asset': 'Aset Tetap',
    'reports': 'Laporan',
    'dashboard': 'Dashboard',
    'workflow': 'Antrean Persetujuan',
}

# Nama resource (untuk UI Role & Akses)
RESOURCE_NAMES = {
    # system
    'users': 'Pengguna', 'roles': 'Role Template', 'access': 'Role & Akses Karyawan',
    'audit': 'Audit Akses', 'organisation': 'Organisasi',
    # master
    'pelanggan': 'Pelanggan', 'supplier': 'Supplier', 'barang': 'Barang & Jasa',
    'gudang': 'Gudang', 'satuan': 'Satuan', 'kategori_barang': 'Kategori Barang',
    'kategori_aset': 'Kategori Aset', 'syarat_bayar': 'Syarat Pembayaran',
    'kasbank_akun': 'Akun Kas & Bank', 'setting_akun': 'Setting Akun', 'karyawan': 'Karyawan',
    'company_profile': 'Profil Perusahaan',
    'mata_uang': 'Mata Uang', 'alamat_pengiriman': 'Alamat Pengiriman',
    'rekening_bank': 'Rekening Bank',
    # sales
    'sales_order': 'Pesanan Penjualan', 'delivery': 'Pengiriman', 'sales_invoice': 'Invoice Penjualan',
    'sales_return': 'Retur Penjualan', 'ar_settlement': 'Pelunasan Piutang',
    'penawaran': 'Penawaran', 'tukar_faktur': 'Tukar Faktur',
    # purchase
    'purchase_order': 'Purchase Order', 'goods_receipt': 'Penerimaan Barang',
    'purchase_invoice': 'Invoice Pembelian', 'purchase_return': 'Retur Pembelian',
    'ap_settlement': 'Pelunasan Hutang',
    # inventory
    'stock': 'Stok', 'valuation': 'Valuasi Persediaan', 'transfer': 'Pemindahan Barang',
    'adjustment': 'Penyesuaian Stok', 'stock_request': 'Permintaan Barang',
    # finance
    'cash_payment': 'Pembayaran Kas', 'cash_receipt': 'Penerimaan Kas',
    'bank_transfer': 'Transfer Bank', 'bank_reconciliation': 'Rekonsiliasi Bank',
    # accounting
    'coa': 'Akun Perkiraan (COA)', 'opening_balance': 'Saldo Awal', 'journal': 'Jurnal Umum',
    'period': 'Periode Akuntansi',
    # asset
    'category': 'Kategori Aset', 'register': 'Daftar Aset', 'transaction': 'Transaksi Aset',
    'reconciliation': 'Rekonsiliasi Aset',
    # reports
    'financial': 'Laporan Keuangan', 'ledger': 'Buku Besar', 'ar_aging': 'Umur Piutang',
    'ap_aging': 'Umur Hutang', 'cashbank': 'Mutasi & Rekap Kas/Bank',
    'inventory_report': 'Laporan Persediaan', 'reconciliation_report': 'Rekonsiliasi & Kesehatan',
    # dashboard / workflow
    'operational': 'Dashboard Operasional', 'queue': 'Antrean Persetujuan',
}

# Nama aksi (untuk UI)
ACTION_NAMES = {
    'view': 'Lihat', 'create': 'Tambah', 'edit': 'Ubah', 'delete': 'Hapus',
    'cancel': 'Batalkan', 'submit': 'Submit', 'approve': 'Approve',
    'execute': 'Eksekusi', 'post': 'Posting', 'reverse': 'Balik Jurnal',
    'print': 'Cetak', 'export': 'Export', 'import': 'Import', 'reconcile': 'Rekonsiliasi',
    'close': 'Tutup Periode', 'reopen': 'Buka Periode',
}

# Aksi yang dianggap sensitif secara default (spec §7: wajib reason + audit)
SENSITIVE_ACTIONS = {'approve', 'post', 'reverse', 'close', 'reopen', 'reconcile', 'execute'}


def _res(module: str, resource: str, actions: list, sensitive_extra: tuple = ()):
    """Bangun entri registry untuk satu resource."""
    out = []
    for a in actions:
        out.append((
            module, resource, a,
            f"{ACTION_NAMES.get(a, a)} {RESOURCE_NAMES.get(resource, resource)}",
            a in SENSITIVE_ACTIONS or a in sensitive_extra,
        ))
    return out


REGISTRY: list = (
    # ── System ────────────────────────────────────────────────────────────
    _res('system', 'users', ['view', 'create', 'edit', 'delete'])
    + _res('system', 'roles', ['view', 'create', 'edit'], sensitive_extra=('edit',))
    + _res('system', 'access', ['view', 'edit'], sensitive_extra=('view', 'edit'))
    + _res('system', 'audit', ['view'])
    + _res('system', 'organisation', ['view', 'edit'])
    # ── Master ────────────────────────────────────────────────────────────
    + _res('master', 'pelanggan', ['view', 'create', 'edit', 'delete', 'export', 'import'])
    + _res('master', 'supplier', ['view', 'create', 'edit', 'delete', 'export', 'import'])
    + _res('master', 'barang', ['view', 'create', 'edit', 'delete', 'export', 'import'])
    + _res('master', 'gudang', ['view', 'create', 'edit', 'delete'])
    + _res('master', 'satuan', ['view', 'create', 'edit', 'delete'])
    + _res('master', 'kategori_barang', ['view', 'create', 'edit', 'delete'])
    + _res('master', 'kategori_aset', ['view', 'create', 'edit', 'delete'])
    + _res('master', 'syarat_bayar', ['view', 'create', 'edit', 'delete'])
    + _res('master', 'kasbank_akun', ['view', 'create', 'edit', 'delete'], sensitive_extra=('create', 'edit', 'delete'))
    + _res('master', 'setting_akun', ['view', 'edit'], sensitive_extra=('edit',))
    + _res('master', 'karyawan', ['view', 'create', 'edit', 'delete'])
    + _res('master', 'company_profile', ['view', 'edit'], sensitive_extra=('edit',))  # update ASAHI: identitas cetak/PDF
    + _res('master', 'mata_uang', ['view', 'create', 'edit', 'delete'], sensitive_extra=('create', 'edit', 'delete'))  # update ASAHI #3: dropdown Currency SO/PO
    + _res('master', 'alamat_pengiriman', ['view', 'create', 'edit', 'delete'], sensitive_extra=('create', 'edit', 'delete'))  # update ASAHI #3: gudang tujuan PO
    + _res('master', 'rekening_bank', ['view', 'create', 'edit', 'delete'], sensitive_extra=('create', 'edit', 'delete'))  # update ASAHI #6: rekening bank cetak Invoice Penjualan
    # ── Sales ─────────────────────────────────────────────────────────────
    + _res('sales', 'sales_order', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'print', 'export'])
    + _res('sales', 'penawaran', ['view', 'create', 'edit', 'cancel', 'print'])
    + _res('sales', 'tukar_faktur', ['view', 'create', 'edit', 'cancel', 'print'])  # TANPA submit (tanpa workflow — Update #4)
    + _res('sales', 'sales_invoice', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'print', 'export'])
    + _res('sales', 'sales_return', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'export'])
    + _res('sales', 'delivery', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'execute', 'reverse', 'print', 'export'])
    + _res('sales', 'ar_settlement', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'export'])
    # ── Purchase ──────────────────────────────────────────────────────────
    + _res('purchase', 'purchase_order', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'print', 'export'])
    + _res('purchase', 'goods_receipt', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'execute', 'reverse', 'print', 'export'])
    + _res('purchase', 'purchase_invoice', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'export'])
    + _res('purchase', 'purchase_return', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'export'])
    + _res('purchase', 'ap_settlement', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'export'])
    # ── Inventory ─────────────────────────────────────────────────────────
    + _res('inventory', 'stock', ['view', 'reconcile', 'export'])
    + _res('inventory', 'valuation', ['view', 'export'])
    + _res('inventory', 'transfer', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'execute', 'reverse', 'export'])
    + _res('inventory', 'adjustment', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'export'])
    + _res('inventory', 'stock_request', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'export'])
    # ── Finance (Kas & Bank) ──────────────────────────────────────────────
    + _res('finance', 'cash_payment', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'export'])
    + _res('finance', 'cash_receipt', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'export'])
    + _res('finance', 'bank_transfer', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'reverse', 'export'])
    + _res('finance', 'bank_reconciliation', ['view', 'create', 'edit', 'cancel', 'execute', 'export'])
    # ── Accounting ────────────────────────────────────────────────────────
    + _res('accounting', 'coa', ['view', 'create', 'edit', 'delete', 'export'])
    + _res('accounting', 'opening_balance', ['view', 'edit'], sensitive_extra=('edit',))
    + _res('accounting', 'journal', ['view', 'create', 'edit', 'cancel', 'submit', 'approve', 'post', 'export'])
    + _res('accounting', 'period', ['view', 'close', 'reopen'], sensitive_extra=('close', 'reopen'))
    # ── Asset ─────────────────────────────────────────────────────────────
    + _res('asset', 'category', ['view', 'create', 'edit', 'delete'])
    + _res('asset', 'register', ['view', 'create', 'edit', 'delete'])
    + _res('asset', 'transaction', ['view', 'create', 'cancel', 'submit', 'approve', 'post', 'export'])
    + _res('asset', 'reconciliation', ['view', 'export'])
    # ── Reports ───────────────────────────────────────────────────────────
    + _res('reports', 'financial', ['view', 'export'])
    + _res('reports', 'ledger', ['view', 'export'])
    + _res('reports', 'ar_aging', ['view', 'export'])
    + _res('reports', 'ap_aging', ['view', 'export'])
    + _res('reports', 'cashbank', ['view', 'export'])
    + _res('reports', 'inventory_report', ['view', 'export'])
    + _res('reports', 'reconciliation_report', ['view', 'export'])
    # ── Dashboard & Workflow ───────────────────────────────────────────────
    + _res('dashboard', 'operational', ['view'])
    + _res('workflow', 'queue', ['view'])
)

REGISTRY_CODES = {f"{m}.{r}.{a}" for m, r, a, _, _ in REGISTRY}


# ═══════════════════════════════════════════════════════════════════════════
# Role template (spesifikasi §6 + mockup "CONTOH ROLE TEMPLATE")
# ═══════════════════════════════════════════════════════════════════════════

def _expand(patterns: list) -> set:
    """Ekspansi pattern 'sales.*' / 'sales.sales_order.view' ke kumpulan code registry.

    Update ASAHI #5: wildcard tengah 'reports.*.view' kini diekspansi benar —
    sebelumnya pattern ini dianggap kode literal (tidak pernah cocok) sehingga
    template FINANCE_STAFF & MANAGEMENT_VIEWER kehilangan seluruh izin laporan.
    """
    out = set()
    for p in patterns:
        if p == '*':
            out |= REGISTRY_CODES
        elif p.endswith('.*'):
            prefix = p[:-1]  # 'sales.'
            out |= {c for c in REGISTRY_CODES if c.startswith(prefix)}
        elif '*' in p:
            # Wildcard tengah: 'module.*.action' / 'module.resource.*' per segmen.
            parts = p.split('.')
            if len(parts) == 3:
                m, r, a = parts
                for c in REGISTRY_CODES:
                    cm, cr, ca = c.split('.')
                    if (m == '*' or cm == m) and (r == '*' or cr == r) and (a == '*' or ca == a):
                        out.add(c)
        else:
            if p in REGISTRY_CODES:
                out.add(p)
    return out


ROLE_TEMPLATES = {
    'SUPER_ADMIN': {
        'name': 'Super Admin',
        'description': 'Akses penuh seluruh sistem dan pengaturan user/role. '
                       'Tidak membatalkan aturan akuntansi (maker-checker, periode terkunci).',
        'is_system': True,
        'super': True,  # short-circuit: seluruh registry + endpoint belum terpetakan
        'permissions': set(),
    },
    'IT_ADMIN': {
        'name': 'Admin IT',
        'description': 'Provisioning user, role, konfigurasi sistem, log — tanpa data finansial.',
        'is_system': True,
        'super': False,
        'permissions': _expand([
            'system.users.*', 'system.roles.*', 'system.access.*', 'system.audit.view',
            'system.organisation.*', 'dashboard.operational.view',
            'master.company_profile.view', 'master.mata_uang.view', 'master.alamat_pengiriman.view', 'master.rekening_bank.view',
            'master.mata_uang.create', 'master.mata_uang.edit', 'master.mata_uang.delete',
            'master.alamat_pengiriman.create', 'master.alamat_pengiriman.edit', 'master.alamat_pengiriman.delete',
            'master.rekening_bank.create', 'master.rekening_bank.edit', 'master.rekening_bank.delete',
            'master.company_profile.edit',
        ]),
    },
    'HR_ADMIN': {
        'name': 'Admin HR',
        'description': 'Kelola data karyawan dan linkage akun user — tanpa jurnal/AP/AR/bank.',
        'is_system': True,
        'super': False,
        'permissions': _expand([
            'master.karyawan.*', 'system.users.view', 'system.users.create', 'system.users.edit',
            'dashboard.operational.view', 'master.company_profile.view', 'master.mata_uang.view', 'master.alamat_pengiriman.view',
        ]),
    },
    'FINANCE_MANAGER': {
        'name': 'Manajer Keuangan',
        'description': 'Pengelolaan keuangan & akuntansi penuh termasuk approve, posting, dan laporan. '
                       'Tidak dapat mengubah permission (diri sendiri maupun orang lain).',
        'is_system': True,
        'super': False,
        'permissions': _expand([
            'finance.*', 'accounting.*', 'sales.*', 'purchase.*', 'inventory.*', 'asset.*',
            'reports.*', 'master.*', 'dashboard.operational.view', 'workflow.queue.view',
            'system.audit.view', 'system.organisation.view', 'system.organisation.edit',
        ]),
    },
    'FINANCE_STAFF': {
        'name': 'Staf Keuangan',
        'description': 'Input draft keuangan, settlement, dan laporan sesuai scope. '
                       'Tidak melakukan approval/posting (wajib checker).',
        'is_system': True,
        'super': False,
        'permissions': _expand([
            'finance.cash_payment.view', 'finance.cash_payment.create', 'finance.cash_payment.edit',
            'finance.cash_payment.cancel', 'finance.cash_payment.submit',
            'finance.cash_receipt.view', 'finance.cash_receipt.create', 'finance.cash_receipt.edit',
            'finance.cash_receipt.cancel', 'finance.cash_receipt.submit',
            'finance.bank_transfer.view', 'finance.bank_transfer.create', 'finance.bank_transfer.edit',
            'finance.bank_transfer.cancel', 'finance.bank_transfer.submit',
            'finance.bank_reconciliation.view', 'finance.bank_reconciliation.create',
            'finance.bank_reconciliation.edit', 'finance.bank_reconciliation.cancel',
            'finance.bank_reconciliation.execute',
            'sales.ar_settlement.view', 'sales.ar_settlement.create', 'sales.ar_settlement.edit',
            'sales.ar_settlement.cancel', 'sales.ar_settlement.submit',
            'purchase.ap_settlement.view', 'purchase.ap_settlement.create', 'purchase.ap_settlement.edit',
            'purchase.ap_settlement.cancel', 'purchase.ap_settlement.submit',
            'accounting.journal.view', 'accounting.journal.create', 'accounting.journal.edit',
            'accounting.journal.submit',
            'accounting.coa.view', 'accounting.opening_balance.view', 'accounting.period.view',
            'sales.sales_order.view', 'sales.sales_invoice.view', 'sales.sales_return.view', 'sales.delivery.view',
            'sales.penawaran.view',
            'sales.tukar_faktur.view',
            'purchase.purchase_order.view', 'purchase.goods_receipt.view', 'purchase.purchase_invoice.view',
            'purchase.purchase_return.view',
            'inventory.stock.view', 'inventory.valuation.view', 'inventory.transfer.view',
            'inventory.adjustment.view', 'inventory.stock_request.view',
            'asset.category.view', 'asset.register.view', 'asset.transaction.view', 'asset.reconciliation.view',
            'master.company_profile.view', 'master.mata_uang.view', 'master.alamat_pengiriman.view', 'master.rekening_bank.view',
            'reports.*.view',
            'dashboard.operational.view',
        ]),
    },
    'PURCHASING_STAFF': {
        'name': 'Staf Pembelian',
        'description': 'Proses pembelian (PO, penerimaan, invoice draft) dan supplier. '
                       'Tidak melakukan payment/posting GL.',
        'is_system': True,
        'super': False,
        'permissions': _expand([
            'purchase.purchase_order.view', 'purchase.purchase_order.create', 'purchase.purchase_order.edit',
            'purchase.purchase_order.cancel', 'purchase.purchase_order.submit', 'purchase.purchase_order.print',
            'purchase.goods_receipt.view', 'purchase.goods_receipt.create', 'purchase.goods_receipt.edit',
            'purchase.goods_receipt.cancel', 'purchase.goods_receipt.submit', 'purchase.goods_receipt.print',
            'purchase.purchase_invoice.view', 'purchase.purchase_invoice.create', 'purchase.purchase_invoice.edit',
            'purchase.purchase_invoice.cancel', 'purchase.purchase_invoice.submit',
            'purchase.purchase_return.view', 'purchase.purchase_return.create', 'purchase.purchase_return.edit',
            'purchase.purchase_return.cancel', 'purchase.purchase_return.submit',
            'master.supplier.view', 'master.supplier.create', 'master.supplier.edit',
            'master.supplier.export', 'master.supplier.import',
            'master.barang.view', 'master.gudang.view',
            'master.company_profile.view', 'master.mata_uang.view', 'master.alamat_pengiriman.view', 'master.rekening_bank.view',
            'inventory.stock.view',
            'dashboard.operational.view',
        ]),
    },
    'SALES_STAFF': {
        'name': 'Staf Penjualan',
        'description': 'Proses penjualan (SO, pengiriman, invoice draft) dan pelanggan. '
                       'Tidak melakukan payment/posting GL.',
        'is_system': True,
        'super': False,
        'permissions': _expand([
            'sales.sales_order.view', 'sales.sales_order.create', 'sales.sales_order.edit',
            'sales.sales_order.cancel', 'sales.sales_order.submit', 'sales.sales_order.print',
            'sales.penawaran.view', 'sales.penawaran.create', 'sales.penawaran.edit',
            'sales.penawaran.cancel', 'sales.penawaran.print',
            'sales.tukar_faktur.view', 'sales.tukar_faktur.create', 'sales.tukar_faktur.edit',
            'sales.tukar_faktur.cancel', 'sales.tukar_faktur.print',
            'sales.delivery.view', 'sales.delivery.create', 'sales.delivery.edit',
            'sales.delivery.cancel', 'sales.delivery.submit', 'sales.delivery.print',
            'sales.sales_invoice.view', 'sales.sales_invoice.create', 'sales.sales_invoice.edit',
            'sales.sales_invoice.cancel', 'sales.sales_invoice.submit', 'sales.sales_invoice.print',
            'sales.sales_return.view', 'sales.sales_return.create', 'sales.sales_return.edit',
            'sales.sales_return.cancel', 'sales.sales_return.submit',
            'master.pelanggan.view', 'master.pelanggan.create', 'master.pelanggan.edit',
            'master.pelanggan.export', 'master.pelanggan.import',
            'master.barang.view', 'master.gudang.view', 'master.satuan.view',
            'master.kategori_barang.view', 'master.syarat_bayar.view',
            'master.company_profile.view', 'master.mata_uang.view', 'master.alamat_pengiriman.view', 'master.rekening_bank.view',
            'inventory.stock.view',
            'dashboard.operational.view',
        ]),
    },
    'WAREHOUSE_STAFF': {
        'name': 'Staf Gudang',
        'description': 'Eksekusi pengiriman/penerimaan, pengelolaan stok dan gudang. '
                       'Tanpa nilai finansial sensitif (AR/AP/laporan keuangan).',
        'is_system': True,
        'super': False,
        'permissions': _expand([
            'inventory.transfer.view', 'inventory.transfer.create', 'inventory.transfer.edit',
            'inventory.transfer.cancel', 'inventory.transfer.submit',
            'inventory.adjustment.view', 'inventory.adjustment.create', 'inventory.adjustment.edit',
            'inventory.adjustment.cancel', 'inventory.adjustment.submit',
            'inventory.stock_request.view', 'inventory.stock_request.create', 'inventory.stock_request.edit',
            'inventory.stock_request.cancel', 'inventory.stock_request.submit',
            'inventory.stock.view', 'inventory.valuation.view',
            'purchase.goods_receipt.view', 'purchase.goods_receipt.create', 'purchase.goods_receipt.edit',
            'purchase.goods_receipt.cancel', 'purchase.goods_receipt.submit', 'purchase.goods_receipt.execute',
            'sales.delivery.view', 'sales.delivery.create', 'sales.delivery.edit',
            'sales.delivery.cancel', 'sales.delivery.submit', 'sales.delivery.execute',
            'master.gudang.view', 'master.gudang.create', 'master.gudang.edit',
            'master.barang.view', 'master.barang.create', 'master.barang.edit',
            'master.barang.export', 'master.barang.import',
            'master.satuan.view', 'master.satuan.create', 'master.satuan.edit',
            'master.kategori_barang.view',
            'master.company_profile.view', 'master.mata_uang.view', 'master.alamat_pengiriman.view', 'master.rekening_bank.view',
            'dashboard.operational.view',
        ]),
    },
    'MANAGEMENT_VIEWER': {
        'name': 'Viewer Management',
        'description': 'Hanya melihat dashboard/laporan (view only) sesuai scope.',
        'is_system': True,
        'super': False,
        'permissions': _expand(['dashboard.operational.view', 'reports.*.view', 'master.company_profile.view', 'master.mata_uang.view', 'master.alamat_pengiriman.view', 'master.rekening_bank.view']),
    },
}

# Mapping enum role lama → template (parity migrasi §11.3; fallback bila user
# belum punya user_roles — mis. dibuat sebelum RBAC v2 aktif).
LEGACY_TEMPLATE = {
    'ADMINISTRATOR': 'SUPER_ADMIN',
    'MANAJER_KEUANGAN': 'FINANCE_MANAGER',
    'STAFF_AKUNTANSI': 'FINANCE_STAFF',
    'STAFF_PENJUALAN': 'SALES_STAFF',
    'STAFF_GUDANG': 'WAREHOUSE_STAFF',
}


# ═══════════════════════════════════════════════════════════════════════════
# document_type workflow (nama tabel) → resource permission (spec §8.4)
# ═══════════════════════════════════════════════════════════════════════════

KIND_RESOURCE = {
    'sales_order': 'sales.sales_order',
    'sales_invoice': 'sales.sales_invoice',
    'sales_retur': 'sales.sales_return',
    'penawaran': 'sales.penawaran',
    'tukar_faktur': 'sales.tukar_faktur',
    'purchase_order': 'purchase.purchase_order',
    'purchase_invoice': 'purchase.purchase_invoice',
    'purchase_retur': 'purchase.purchase_return',
    'pengiriman_barang': 'sales.delivery',
    'penerimaan_barang': 'purchase.goods_receipt',
    'penyesuaian_stok': 'inventory.adjustment',
    'pemindahan_barang': 'inventory.transfer',
    'permintaan_barang': 'inventory.stock_request',
    'pembayaran_kas': 'finance.cash_payment',
    'penerimaan_kas': 'finance.cash_receipt',
    'transfer_bank': 'finance.bank_transfer',
    'jurnal_umum': 'accounting.journal',
    'asset_event': 'asset.transaction',
}


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint → permission code
# ═══════════════════════════════════════════════════════════════════════════

# router_module → {prefix nama fungsi → resource}
_ROUTER_PREFIXES = {
    'penjualan': [
        ('penawaran', 'sales.penawaran'),
        ('tukar_faktur', 'sales.tukar_faktur'),
        ('sales_invoice', 'sales.sales_invoice'),
        ('sales_order', 'sales.sales_order'),
        ('sales_retur', 'sales.sales_return'),
        ('pengiriman', 'sales.delivery'),
        ('invoice_belum_bayar', 'sales.ar_settlement'),
    ],
    'pembelian': [
        ('purchase_order', 'purchase.purchase_order'),
        ('purchase_invoice', 'purchase.purchase_invoice'),
        ('purchase_retur', 'purchase.purchase_return'),
        ('penerimaan', 'purchase.goods_receipt'),
        ('invoice_belum_bayar', 'purchase.ap_settlement'),
    ],
    'kas_bank': [
        ('pembayaran', 'finance.cash_payment'),
        ('penerimaan', 'finance.cash_receipt'),
        ('transfer', 'finance.bank_transfer'),
    ],
    'rekonsiliasi_bank': [
        ('rekonsiliasi', 'finance.bank_reconciliation'),
    ],
    'persediaan': [
        ('penyesuaian', 'inventory.adjustment'),
        ('pemindahan', 'inventory.transfer'),
        ('permintaan', 'inventory.stock_request'),
        ('valuation', 'inventory.valuation'),
    ],
    'master': [
        ('pelanggan_from_coa', 'master.pelanggan'),
        ('pelanggan_coa', 'master.pelanggan'),
        ('pelanggan', 'master.pelanggan'),
        ('supplier', 'master.supplier'),
        ('barang_satuan', 'master.barang'),
        ('barang', 'master.barang'),
        ('gudang', 'master.gudang'),
        ('syarat_bayar', 'master.syarat_bayar'),
        ('kategori_aset', 'master.kategori_aset'),
        ('kas_bank_akun', 'master.kasbank_akun'),
        ('setting_akun', 'master.setting_akun'),
        ('company_profile', 'master.company_profile'),
        ('mata_uang', 'master.mata_uang'),
        ('alamat_pengiriman', 'master.alamat_pengiriman'),
        ('rekening_bank', 'master.rekening_bank'),
        ('kategori_barang', 'master.kategori_barang'),
        ('kategori', 'master.kategori_barang'),
        ('satuan', 'master.satuan'),
    ],
}

# Endpoint eksplisit (fn name → permission code) — yang tak tertangkap pola.
_EXPLICIT = {
    # master dropdown & khusus
    'get_coa_dropdown': 'accounting.coa.view',
    'get_barang_dropdown': 'master.barang.view',
    'get_pelanggan_dropdown': 'master.pelanggan.view',
    'get_supplier_dropdown': 'master.supplier.view',
    'get_kas_bank_dropdown': 'master.kasbank_akun.view',
    'get_barang_inventory_accounts': 'master.barang.view',
    'get_barang_akun_pilihan': 'master.barang.view',
    'sync_kas_bank_akun': 'master.kasbank_akun.edit',
    'get_pelanggan_coa': 'master.pelanggan.view',
    'create_pelanggan_from_coa': 'master.pelanggan.create',
    # master — export & import Excel (Update #5)
    'export_barang': 'master.barang.export',
    'barang_import_template': 'master.barang.view',
    'import_barang': 'master.barang.import',
    'export_pelanggan': 'master.pelanggan.export',
    'pelanggan_import_template': 'master.pelanggan.view',
    'import_pelanggan': 'master.pelanggan.import',
    'export_supplier': 'master.supplier.export',
    'supplier_import_template': 'master.supplier.view',
    'import_supplier': 'master.supplier.import',
    # coa
    'get_saldo_awal': 'accounting.opening_balance.view',
    'save_saldo_awal': 'accounting.opening_balance.edit',
    'get_next_kode': 'accounting.coa.view',
    'migration_preview': 'accounting.coa.view',
    'migration_apply': 'accounting.coa.edit',
    # coa — registry tipe akun (revisi form COA)
    'get_account_types': 'accounting.coa.view',
    'get_eligible_parents': 'accounting.coa.view',
    'preview_coa': 'accounting.coa.view',
    # jurnal
    'list_ref_modules': 'accounting.journal.view',
    'create_jurnal_manual': 'accounting.journal.create',
    'update_manual': 'accounting.journal.edit',
    # rekonsiliasi bank (detail sub-resource)
    'add_detail': 'finance.bank_reconciliation.create',
    'update_detail': 'finance.bank_reconciliation.edit',
    'remove_detail': 'finance.bank_reconciliation.edit',
    'preview_saldo_buku': 'finance.bank_reconciliation.view',
    # stok kartu
    'get_stok_kartu': 'inventory.stock.view',
    'get_stok_kartu_summary': 'inventory.stock.view',
    'get_valuasi_options': 'inventory.valuation.view',
    'rekalkulasi_stok_kartu': 'inventory.stock.edit',
    'reconcile_stock': 'inventory.stock.reconcile',
    'reconcile_inventory_ledger': 'inventory.stock.reconcile',
    'get_inventory_valuation_summary': 'inventory.valuation.view',
    # aset tetap
    'get_rekonsiliasi_aset': 'asset.reconciliation.view',
    'get_ringkasan_rekonsiliasi_aset': 'asset.reconciliation.view',
    'hapus_aset': 'asset.register.delete',
    'set_perbaikan': 'asset.register.edit',
    'aktifkan_kembali': 'asset.register.edit',
    # aset transaksi (asset_cycle)
    'create_asset_event': 'asset.transaction.create',
    'list_asset_events': 'asset.transaction.view',
    'get_asset_event': 'asset.transaction.view',
    'cancel_asset_event': 'asset.transaction.cancel',
    # periode
    'get_periode_list': 'accounting.period.view',
    'check_periode_status': 'accounting.period.view',
    'get_pre_close_readiness': 'accounting.period.view',
    'get_gl_reconciliation': 'accounting.period.view',
    'tutup_periode': 'accounting.period.close',
    'buka_periode': 'accounting.period.reopen',
    # laporan
    'get_neraca_saldo': 'reports.financial.view',
    'get_perubahan_modal': 'reports.financial.view',
    'get_laba_rugi': 'reports.financial.view',
    'get_neraca': 'reports.financial.view',
    'get_arus_kas': 'reports.financial.view',
    'validate_financial_reports': 'reports.financial.view',
    'get_buku_besar': 'reports.ledger.view',
    'get_mutasi_kas': 'reports.cashbank.view',
    'get_mutasi_bank': 'reports.cashbank.view',
    'get_rekap_kas_bank': 'reports.cashbank.view',
    'get_umur_piutang': 'reports.ar_aging.view',
    'get_umur_hutang': 'reports.ap_aging.view',
    'get_rekonsiliasi_persediaan': 'reports.inventory_report.view',
    'get_ringkasan_rekonsiliasi_persediaan': 'reports.inventory_report.view',
    'get_audit_persediaan': 'reports.inventory_report.view',
    'get_grni_reconciliation_endpoint': 'reports.reconciliation_report.view',
    'get_cashflow_vs_bs_reconciliation_endpoint': 'reports.reconciliation_report.view',
    'get_equity_vs_bs_reconciliation_endpoint': 'reports.reconciliation_report.view',
    'get_accounting_health_endpoint': 'reports.reconciliation_report.view',
    # dashboard
    'get_dashboard_summary': 'dashboard.operational.view',
    # workflow
    'get_queue': 'workflow.queue.view',
    'get_capabilities': 'workflow.queue.view',
    # organisasi
    'list_organization_units': 'system.organisation.view',
    'create_organization_unit': 'system.organisation.edit',
    'edit_organization_unit': 'system.organisation.edit',
    'get_document_organization': 'system.organisation.view',
    'assign_document_organization': 'system.organisation.edit',
    'list_reporting_audit': 'system.audit.view',
    'list_cashflow_classifications': 'system.organisation.view',
    'set_cashflow_classification': 'system.organisation.edit',
    # pelunasan (jenis via path: piutang/hutang)
    'get_outstanding': None,       # dinamis via path
    'get_invoice_settlement': None,
    'create_pelunasan': None,
    'get_pelunasan': None,
    'update_pelunasan': None,
    'export_tagihan': None,        # dinamis via path → .export (Update #5)
    # persediaan — export stok (Update #5)
    'export_stok': 'inventory.stock.export',
}

# Router yang permission-nya ditegakkan di SERVICE (bukan router gate).
_SERVICE_ENFORCED = {'workflow'}


def _action_for(method: str, fn: str) -> Optional[str]:
    if method in ('GET', 'HEAD', 'OPTIONS'):
        return 'view'
    if method in ('PUT', 'PATCH'):
        return 'edit'
    if method == 'DELETE':
        return 'delete'
    # POST
    if fn.startswith(('create_', 'add_', 'login')):
        return 'create'
    if fn.startswith(('cancel_', 'void_')) or fn in ('batal', 'batal_rekonsiliasi'):
        return 'cancel'
    if fn.startswith('reverse_'):
        return 'reverse'
    if fn.startswith('approve_'):
        return 'approve'
    if fn in ('complete_rekonsiliasi',):
        return 'execute'
    if fn == 'tutup_periode':
        return 'close'
    if fn == 'buka_periode':
        return 'reopen'
    return 'edit'


def infer_permission(router_module: str, fn: str, method: str, path: str) -> Optional[str]:
    """Hitung permission code untuk sebuah endpoint. None = belum terpetakan
    (default deny untuk non-super-admin — RBAC-13)."""
    if router_module in _SERVICE_ENFORCED:
        return 'workflow.queue.view' if fn in ('get_queue', 'get_capabilities') else None
    if router_module == 'auth':
        return None  # login/init-admin publik; tidak lewat module_access

    # Pelunasan: jenis (piutang/hutang) ada di path
    if router_module == 'pelunasan':
        if fn in _EXPLICIT and _EXPLICIT[fn] is None:
            base = 'sales.ar_settlement' if 'piutang' in path else 'purchase.ap_settlement'
            act = 'view' if method == 'GET' else _action_for(method, fn)
            if fn.startswith('export_'):
                act = 'export'  # export tagihan piutang/hutang (Update #5)
            code = f"{base}.{act}"
            return code if code in REGISTRY_CODES else None
        if fn in _EXPLICIT:
            return _EXPLICIT[fn]

    if fn in _EXPLICIT and _EXPLICIT[fn] is not None:
        return _EXPLICIT[fn]

    # Prefix resource per router
    prefixes = _ROUTER_PREFIXES.get(router_module)
    if prefixes:
        for prefix, resource in prefixes:
            if fn.startswith(prefix) or f"_{prefix}" in f"_{fn}":
                act = _action_for(method, fn)
                code = f"{resource}.{act}"
                return code if code in REGISTRY_CODES else None

    # Router sederhana satu-resource
    simple = {
        'pengguna': 'system.users',
        'karyawan': 'master.karyawan',
        'coa': 'accounting.coa',
        'aset_tetap': 'asset.register',
        'asset_cycle': 'asset.transaction',
        'dashboard': 'dashboard.operational',
        'jurnal': 'accounting.journal',
    }
    if router_module in simple:
        act = _action_for(method, fn)
        code = f"{simple[router_module]}.{act}"
        return code if code in REGISTRY_CODES else None

    return None
