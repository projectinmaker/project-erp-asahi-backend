"""=== Update #071 — konfigurasi persetujuan (Preferensi ▸ Persetujuan) ===

Maker-checker tidak lagi di-hardcode per endpoint/role: tiap JENIS dokumen
punya saklar "perlu persetujuan" + daftar approver (user dan/atau role)
yang diatur Super Admin lewat modul Preferensi ▸ Persetujuan.

- Jenis dokumen yang TIDAK diaktifkan → dokumen langsung final saat dibuat
  (auto-final; pola Update #043/#071 sebelumnya).
- Jenis dokumen yang diaktifkan → dokumen menunggu persetujuan (1 tahap);
  sekali approve → final + posting/eksekusi otomatis.

Default: SEMUA jenis dokumen non-aktif (tidak ada seed) — perilaku setelah
update = semua dokumen langsung final, lalu Super Admin mengaktifkan
selektif sesuai kebutuhan.

Hanya jenis dokumen yang daftar modulnya sudah punya kolom "Persetujuan"
(Kas & Bank, Persediaan, Aset Tetap, Jurnal Umum) yang diekspos konfigurasi
(SUPPORTED_KINDS di approval_config_service); backend sendiri siap untuk
jenis apa pun bila UI modul lain ditambahkan nanti.
"""
from sqlalchemy import Boolean, Column, String, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.database import BaseModel
from app.models.base import BaseMixin


class ApprovalConfig(BaseModel, BaseMixin):
    __tablename__ = 'approval_config'
    __table_args__ = (
        UniqueConstraint('document_type', name='uq_approval_config_document_type'),
    )
    document_type = Column(String(40), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    updated_by = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=True)


class ApprovalApprover(BaseModel, BaseMixin):
    __tablename__ = 'approval_approver'
    __table_args__ = (
        # Satu baris = SATU penunjuk approver: user ATAU role (tidak keduanya).
        CheckConstraint('num_nonnulls(user_id, role_code) = 1', name='ck_approval_approver_target'),
        UniqueConstraint('document_type', 'user_id', name='uq_approval_approver_user'),
        UniqueConstraint('document_type', 'role_code', name='uq_approval_approver_role'),
    )
    document_type = Column(String(40), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('pengguna.id'), nullable=True)
    role_code = Column(String(50), nullable=True)
