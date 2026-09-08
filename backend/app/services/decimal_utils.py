"""
decimal_utils.py

Helper konversi angka yang aman terhadap nilai None/kosong dari request JSON.

Bug yang di-fix: field Optional (mis. `diskon: Optional[Decimal] = 0`) yang
dikirim frontend sebagai `null` akan tetap ADA sebagai key di dict hasil
`schema.model_dump()`, hanya value-nya `None`. Pola lama:

    diskon = Decimal(str(d.get("diskon", 0)))

`.get("diskon", 0)` HANYA pakai default 0 kalau key "diskon" tidak ada sama
sekali di dict. Karena key-nya memang ada (cuma None), .get() balikin None,
lalu str(None) == "None", dan Decimal("None") raise
`decimal.InvalidOperation: [<class 'decimal.ConversionSyntax'>]`.

Gunakan safe_decimal()/safe_int() di bawah ini untuk semua konversi field
numerik yang optional dari details_data/biaya_data.
"""

from decimal import Decimal
from typing import Any


def safe_decimal(value: Any, default: str = "0") -> Decimal:
    """Konversi value ke Decimal dengan aman. None atau "" -> default."""
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def safe_int(value: Any, default: int = 0) -> int:
    """Konversi value ke int dengan aman. None atau "" -> default."""
    if value is None or value == "":
        return default
    return int(value)