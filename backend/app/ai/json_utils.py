from __future__ import annotations

import json
from typing import Any


def stable_json_dumps(data: Any, *, indent: int | None = None) -> str:
    """稳定序列化 JSON 数据，减少提示词中的无效空白。

    Args:
        data: 可 JSON 序列化的数据。
        indent: 缩进空格数；为空时输出紧凑 JSON。

    Returns:
        键顺序稳定的 JSON 字符串。
    """
    separators = None if indent is not None else (",", ":")
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        indent=indent,
        separators=separators,
    )
