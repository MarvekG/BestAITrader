from typing import Any

from pydantic import BaseModel, Field


class SandboxLimits(BaseModel):
    """沙箱输出限制。"""

    stdout_max_bytes: int = Field(default=32768, ge=1, le=1_048_576)
    stderr_max_bytes: int = Field(default=16384, ge=1, le=1_048_576)


class ExecuteRequest(BaseModel):
    """Python 沙箱执行请求。"""

    code: str = Field(default="", max_length=500_000)
    limits: SandboxLimits = Field(default_factory=SandboxLimits)
    timeout_seconds: int | None = Field(default=None, ge=1)


class ExecuteResponse(BaseModel):
    """Python 沙箱执行响应。"""

    success: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    execution_time_ms: int = 0
    timed_out: bool = False
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
