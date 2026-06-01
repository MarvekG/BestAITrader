from __future__ import annotations

import json
from typing import Any


def stable_json_dumps(data: Any, *, indent: int | None = None) -> str:
    """Serialize JSON-like data with stable key ordering for cache-friendly prompts."""
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        indent=indent,
    )
