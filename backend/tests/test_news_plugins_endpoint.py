import pytest

from app.api.endpoints import news_plugins


class _UploadFileStub:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


@pytest.mark.asyncio
async def test_upload_news_plugins_supports_multiple_files(monkeypatch):
    async def fake_upload(file):
        return {
            "status": "success",
            "message": f"uploaded {file.filename}",
            "module_name": file.filename.removesuffix(".py"),
        }

    monkeypatch.setattr(news_plugins, "_upload_single_news_plugin", fake_upload)

    result = await news_plugins.upload_external_news_plugin(
        files=[
            _UploadFileStub("alpha.py", b"NAME = 'Alpha'\n"),
            _UploadFileStub("beta.py", b"NAME = 'Beta'\n"),
        ],
    )

    assert result == {
        "status": "success",
        "message": "Uploaded 2 plugins, 0 failed.",
        "success_count": 2,
        "failed_count": 0,
        "items": [
            {
                "status": "success",
                "message": "uploaded alpha.py",
                "module_name": "alpha",
                "filename": "alpha.py",
            },
            {
                "status": "success",
                "message": "uploaded beta.py",
                "module_name": "beta",
                "filename": "beta.py",
            },
        ],
    }


@pytest.mark.asyncio
async def test_upload_news_plugins_reports_partial_success(monkeypatch):
    async def fake_upload(file):
        if file.filename == "alpha.py":
            return {
                "status": "success",
                "message": "uploaded alpha.py",
                "module_name": "alpha",
            }
        return {
            "status": "error",
            "message": "upload failed",
            "module_name": "beta",
        }

    monkeypatch.setattr(news_plugins, "_upload_single_news_plugin", fake_upload)

    result = await news_plugins.upload_external_news_plugin(
        files=[
            _UploadFileStub("alpha.py", b"NAME = 'Alpha'\n"),
            _UploadFileStub("beta.py", b"NAME = 'Beta'\n"),
        ],
    )

    assert result["status"] == "partial_success"
    assert result["success_count"] == 1
    assert result["failed_count"] == 1
    assert result["items"][1]["filename"] == "beta.py"
