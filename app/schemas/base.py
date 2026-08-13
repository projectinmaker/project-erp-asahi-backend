from pydantic import ConfigDict, BaseModel
from pydantic.alias_generators import to_camel
from typing import Generic, TypeVar, List


class BaseSchema(BaseModel):
    """
    Base Pydantic schema.
    Mengubah snake_case menjadi camelCase secara otomatis untuk response ke Frontend.
    Contoh: 'induk_id' di DB akan menjadi 'indukId' di JSON.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,  # Tetap bisa terima snake_case jika di-parse manual
        from_attributes=True,  # Mengizinkan pembuatan objek dari SQLAlchemy model (orm_mode di v1)
    )

# ... (kode BaseSchema yang lama tetap ada di sini) ...

# Generic Type untuk Pagination
T = TypeVar('T')

class PaginatedResponse(BaseModel, Generic[T]):
    """Wrapper standar untuk response list yang punya pagination"""
    data: List[T]
    total: int
    skip: int
    limit: int

    class Config:
        alias_generator = to_camel
        populate_by_name = True
        from_attributes = True