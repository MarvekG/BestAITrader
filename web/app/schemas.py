from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EngineType(StrEnum):
    """网页渲染引擎类型。"""

    CLOAKBROWSER = "cloakbrowser"
    PATCHRIGHT = "patchright"
    CAMOUFOX = "camoufox"


class ReturnType(StrEnum):
    """网页内容返回格式。"""

    HTML = "html"
    MARKDOWN = "markdown"


class FetchRequest(BaseModel):
    """网页抓取请求体。"""

    url: str = Field(min_length=1)
    selectors: list[str] = Field(default_factory=list)
    markdown_clean_regexes: list[str] = Field(default_factory=list)
    engine: EngineType = EngineType.CLOAKBROWSER
    return_type: ReturnType = ReturnType.MARKDOWN
    timeout_ms: int | None = Field(default=None, ge=1_000, le=300_000)
    wait_after_ms: int | None = Field(default=None, ge=0, le=120_000)

    model_config = ConfigDict(extra="forbid")

    @field_validator("selectors", "markdown_clean_regexes", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: object) -> list[str]:
        """
        将字符串数组字段规范化为去空白后的列表。

        Args:
            value: 原始字段值。

        Returns:
            去除空字符串后的字符串列表。

        Raises:
            ValueError: 字段不是字符串列表。
        """
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("value must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]


class FetchResponse(BaseModel):
    """网页抓取响应体。"""

    success: bool
    url: str
    engine: EngineType
    return_type: ReturnType
    final_url: str | None = None
    status: int | None = None
    title: str | None = None
    selectors: list[str] = Field(default_factory=list)
    selected_element_count: int | None = None
    content: str | None = None
    content_length: int | None = None
    source_html_length: int | None = None
    content_source: str | None = None
    error: str | None = None


class PdfDownloadRequest(BaseModel):
    """PDF 下载请求体。"""

    url: str = Field(min_length=1)
    engine: EngineType = EngineType.CLOAKBROWSER
    timeout: float | None = Field(default=None, ge=1.0, le=300.0)

    model_config = ConfigDict(extra="forbid")
