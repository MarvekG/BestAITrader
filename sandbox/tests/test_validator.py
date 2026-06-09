import pytest

from app.services.validator import validate_python_code


def test_validate_python_code_allows_data_analysis_imports() -> None:
    """验证沙箱服务端校验允许数据分析依赖导入。"""
    code = """
import numpy as np
import pandas as pd
result = pd.DataFrame({"x": np.array([1, 2, 3])}).sum().to_dict()
"""
    validate_python_code(code)


@pytest.mark.parametrize(
    ("code", "error_fragment"),
    [
        ("import os\nresult = 1", "Import not allowed"),
        ("result = open('x')", "Call not allowed"),
        ("result = (1).__class__", "Attribute access not allowed"),
    ],
)
def test_validate_python_code_rejects_dangerous_patterns(code: str, error_fragment: str) -> None:
    """验证沙箱服务端校验拒绝危险代码模式。"""
    with pytest.raises(Exception) as exc_info:
        validate_python_code(code)
    assert error_fragment in str(exc_info.value)
