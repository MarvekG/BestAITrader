from __future__ import annotations

from typing import Any


def status_payload(data_status: str, **kwargs: Any) -> dict[str, Any]:
    payload = {"data_status": data_status}
    payload.update(kwargs)
    return payload


def wrap_dict_section(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return status_payload("missing")
    wrapped = dict(payload)
    wrapped.setdefault("data_status", "available")
    return wrapped


def wrap_list_section(
    items: list[dict[str, Any]] | None,
    *,
    empty_status: str = "missing",
    include_count: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    normalized_items = items or []
    payload_kwargs = dict(kwargs)
    payload_kwargs["items"] = normalized_items
    if include_count:
        payload_kwargs["item_count"] = len(normalized_items)
    data_status = "available" if normalized_items else empty_status
    return status_payload(data_status, **payload_kwargs)


def wrap_snapshot_section(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return status_payload("missing")
    wrapped = dict(payload)
    wrapped.setdefault("data_status", "available")
    return wrapped
