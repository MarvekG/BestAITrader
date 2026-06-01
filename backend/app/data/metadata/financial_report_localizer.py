from typing import Any, Dict, Optional

from app.data.metadata.field_labels import get_table_field_label


def drop_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: drop_nulls(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [drop_nulls(item) for item in value if item is not None]
    return value


def _localize_nested_value(value: Any, table: str) -> Any:
    if isinstance(value, dict):
        localized_dict = {}
        for nested_key, nested_value in value.items():
            if nested_value is None:
                continue
            localized_key = get_table_field_label(table, nested_key)
            if localized_key in localized_dict and localized_key != nested_key:
                localized_dict[nested_key] = _localize_nested_value(nested_value, table)
                continue
            localized_dict[localized_key] = _localize_nested_value(nested_value, table)
        return localized_dict
    if isinstance(value, list):
        return [_localize_nested_value(item, table) for item in value if item is not None]
    return value


def localize_financial_report_payload(
    raw_data: Optional[Dict[str, Any]],
    table: str,
) -> Optional[Dict[str, Any]]:
    if not raw_data:
        return raw_data

    localized = {}
    for key, value in raw_data.items():
        if value is None:
            continue
        if key in {"data", "meta", "_meta"}:
            localized[key] = _localize_nested_value(value, table)
            continue

        localized_key = get_table_field_label(table, key)
        if localized_key in localized and localized_key != key:
            localized[key] = _localize_nested_value(value, table)
            continue
        localized[localized_key] = _localize_nested_value(value, table)
    return drop_nulls(localized)


def localize_financial_report_data_field(
    record: Dict[str, Any],
    table: str,
) -> Dict[str, Any]:
    if not isinstance(record, dict):
        return record

    localized_record = dict(record)
    data_payload = localized_record.get("data")
    if isinstance(data_payload, dict):
        localized_record["data"] = _localize_nested_value(data_payload, table)
    elif isinstance(data_payload, list):
        localized_record["data"] = _localize_nested_value(data_payload, table)
    return drop_nulls(localized_record)
