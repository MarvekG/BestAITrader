import math
from typing import Any
from app.core.logger import logger
from uuid import UUID
from datetime import datetime, date

def sanitize_for_json(obj: Any, path: str = "root") -> Any:
    """
    Recursively sanitize a Python object for JSON serialization.
    Specifically handles NaN and Infinity values by converting them to None.
    Logs warnings when such values are found.
    
    Args:
        obj: The object to sanitize (dict, list, float, etc.)
        path: Current path for logging context (e.g. "root.key1[0]")
        
    Returns:
        The sanitized object safe for JSON serialization.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            logger.warning(f"⚠️ JSON Sanitize: Found {obj} at '{path}'. Converting to None.")
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_for_json(v, path=f"{path}.{k}") for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v, path=f"{path}[{i}]") for i, v in enumerate(obj)]
    elif isinstance(obj, tuple):
        return tuple(sanitize_for_json(v, path=f"{path}[{i}]") for i, v in enumerate(obj))
    elif isinstance(obj, (UUID, datetime, date)):
        return str(obj)
    elif hasattr(obj, 'isoformat'):
        return obj.isoformat()
    else:
        return obj

