from sqlalchemy import Column, String, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import BaseModel
from app.models.base import BaseMixin


class SettingAkun(BaseModel, BaseMixin):
    __tablename__ = "setting_akun"
    __table_args__ = (
        UniqueConstraint("key", name="uq_setting_akun_key"),
    )

    key = Column(String(100), nullable=False, unique=True, index=True)
    label = Column(String(200), nullable=False)
    akun_perkiraan_id = Column(UUID(as_uuid=True), ForeignKey("akun_perkiraan.id"), nullable=False)

    # Relationships
    akun_perkiraan = relationship("AkunPerkiraan")
