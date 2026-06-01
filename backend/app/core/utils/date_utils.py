from datetime import datetime
from typing import Optional


def normalize_compact_date(value: Optional[str]) -> Optional[str]:
    """
    Normalize common date string formats to YYYYMMDD.
    """
    if value in (None, ""):
        return value

    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(value), fmt).strftime("%Y%m%d")
        except ValueError:
            continue

    raise ValueError(f"Unsupported date format: {value}")
