"""memory_documents 存储层单元测试。"""

import pytest

from app.ai.memory_documents import store
from app.core.config import settings
from app.models.user import User


async def _create_user(db, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_read_document_returns_empty_marker_when_missing(test_db, async_db_session):
    user = await _create_user(async_db_session, "mem_doc_user_a")

    result = await store.read_document(user_id=user.id, stock_code="600519.SH")

    assert result == {"exists": False, "content": "", "size_chars": 0, "version": 0}


@pytest.mark.asyncio
async def test_update_document_creates_row_on_first_write(test_db, async_db_session):
    user = await _create_user(async_db_session, "mem_doc_user_b")

    result = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="# 贵州茅台(600519.SH) 经验\n- 2026-08: 高位放量需减仓。",
        base_version=0,
    )

    assert result["success"] is True
    assert result["status"] == "success"
    assert result["memory_id"].startswith("md_")
    assert result["stock_code"] == "600519.SH"
    assert result["version"] == 1

    doc = await store.read_document(user_id=user.id, stock_code="600519.SH")
    assert doc["exists"] is True
    assert doc["content"].startswith("# 贵州茅台(600519.SH) 经验")
    assert doc["version"] == 1
    assert doc["size_chars"] == len(doc["content"])


@pytest.mark.asyncio
async def test_update_document_rejects_stale_base_version_when_missing(test_db, async_db_session):
    user = await _create_user(async_db_session, "mem_doc_user_c")

    result = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="content",
        base_version=2,
    )

    assert result["success"] is False
    assert "not found" in result["error"]
    assert result["version"] == 0


@pytest.mark.asyncio
async def test_update_document_increments_version_on_rewrite(test_db, async_db_session):
    user = await _create_user(async_db_session, "mem_doc_user_d")

    first = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="version one",
        base_version=0,
    )
    second = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="version two",
        base_version=first["version"],
    )

    assert first["version"] == 1
    assert second["success"] is True
    assert second["version"] == 2
    doc = await store.read_document(user_id=user.id, stock_code="600519.SH")
    assert doc["content"] == "version two"
    assert doc["version"] == 2


@pytest.mark.asyncio
async def test_update_document_detects_version_conflict_and_returns_latest(test_db, async_db_session):
    user = await _create_user(async_db_session, "mem_doc_user_e")

    first = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="latest content",
        base_version=0,
    )
    conflict = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="stale overwrite",
        base_version=0,
    )

    assert first["success"] is True
    assert conflict["success"] is False
    assert "version conflict" in conflict["error"]
    assert conflict["content"] == "latest content"
    assert conflict["version"] == first["version"]

    doc = await store.read_document(user_id=user.id, stock_code="600519.SH")
    assert doc["content"] == "latest content"


@pytest.mark.asyncio
async def test_update_document_rejects_empty_content(test_db, async_db_session):
    user = await _create_user(async_db_session, "mem_doc_user_f")

    result = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="   ",
        base_version=0,
    )

    assert result["success"] is False
    assert "empty" in result["error"]


@pytest.mark.asyncio
async def test_update_document_enforces_size_limit(test_db, async_db_session, monkeypatch):
    user = await _create_user(async_db_session, "mem_doc_user_g")
    monkeypatch.setattr(settings, "MEMORY_DOC_MAX_CHARS", 10)

    result = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="x" * 11,
        base_version=0,
    )

    assert result["success"] is False
    assert "exceeds limit" in result["error"]
    assert result["size_chars"] == 11
    assert result["max_chars"] == 10


@pytest.mark.asyncio
async def test_update_document_resolves_concurrent_first_write_race(test_db, async_db_session, monkeypatch):
    """并发首写撞唯一约束时，后提交者收到冲突响应和最新全文，而不是异常。

    通过让加载查询返回空来模拟“SELECT 发生在对方事务提交前”的交错时序，
    使 INSERT 走到唯一约束冲突。
    """
    user = await _create_user(async_db_session, "mem_doc_user_j")

    first = await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="winner content",
        base_version=0,
    )
    assert first["success"] is True

    original_load = store._load_document

    async def _load_missing_before_commit(db, *, user_id, stock_code):
        return None

    monkeypatch.setattr(store, "_load_document", _load_missing_before_commit)
    try:
        loser = await store.update_document(
            user_id=user.id,
            stock_code="600519.SH",
            content="loser content",
            base_version=0,
        )
    finally:
        monkeypatch.setattr(store, "_load_document", original_load)

    assert loser["success"] is False
    assert "created concurrently" in loser["error"]
    assert loser["content"] == "winner content"
    assert loser["version"] == first["version"]

    doc = await store.read_document(user_id=user.id, stock_code="600519.SH")
    assert doc["content"] == "winner content"


@pytest.mark.asyncio
async def test_documents_are_isolated_per_stock_for_same_user(test_db, async_db_session):
    user = await _create_user(async_db_session, "mem_doc_user_k")

    await store.update_document(
        user_id=user.id,
        stock_code="600519.SH",
        content="maotai memory",
        base_version=0,
    )

    other = await store.read_document(user_id=user.id, stock_code="000001.SZ")
    assert other["exists"] is False

    conflict = await store.update_document(
        user_id=user.id,
        stock_code="000001.SZ",
        content="wrong stock stale write",
        base_version=0,
    )
    assert conflict["success"] is True
    assert conflict["version"] == 1


@pytest.mark.asyncio
async def test_documents_are_isolated_per_user(test_db, async_db_session):
    user_a = await _create_user(async_db_session, "mem_doc_user_h")
    user_b = await _create_user(async_db_session, "mem_doc_user_i")

    await store.update_document(
        user_id=user_a.id,
        stock_code="600519.SH",
        content="user a memory",
        base_version=0,
    )

    doc_b = await store.read_document(user_id=user_b.id, stock_code="600519.SH")
    assert doc_b["exists"] is False

    conflict_b = await store.update_document(
        user_id=user_b.id,
        stock_code="600519.SH",
        content="user b stale write",
        base_version=1,
    )
    assert conflict_b["success"] is False
