from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.data_storage import StockBasic
from app.models.memory_document import MemoryDocument
from app.models.user import User


async def _seed_documents(session_factory):
    async with session_factory() as db:
        user = (await db.execute(select(User))).scalar_one()
        other_user = User(
            username="memory_documents_other",
            email="memory_documents_other@example.com",
            password_hash="hashed",
        )
        db.add(other_user)
        await db.flush()
        db.add(
            StockBasic(
                stock_code="000001.SZ",
                name="平安银行",
                industry="银行",
                market="SZSE",
            )
        )
        db.add_all(
            [
                MemoryDocument(
                    user_id=user.id,
                    stock_code="000001.SZ",
                    content="# 平安银行\n\n等待行业相对强度确认。",
                    size_chars=19,
                    version=2,
                    created_at=datetime(2026, 9, 1, 10, 0),
                    updated_at=datetime(2026, 9, 3, 10, 0),
                ),
                MemoryDocument(
                    user_id=other_user.id,
                    stock_code="600519.SH",
                    content="other user document",
                    size_chars=19,
                    version=1,
                    created_at=datetime(2026, 9, 1, 11, 0),
                    updated_at=datetime(2026, 9, 2, 11, 0),
                ),
            ]
        )
        await db.commit()


def test_memory_documents_api_requires_authentication(client):
    response = client.get("/api/v1/memory-documents")

    assert response.status_code == 401


def test_memory_documents_api_lists_owned_summaries_and_reads_full_document(
    client,
    auth_headers,
    test_db,
    run_async,
):
    run_async(_seed_documents(test_db))

    list_response = client.get(
        "/api/v1/memory-documents?keyword=平安&page_size=1",
        headers=auth_headers,
    )

    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["total"] == 1
    assert listed["max_chars"] == 8000
    assert len(listed["items"]) == 1
    assert listed["items"][0]["stock_code"] == "000001.SZ"
    assert listed["items"][0]["stock_name"] == "平安银行"
    assert listed["items"][0]["version"] == 2
    assert "content" not in listed["items"][0]

    detail_response = client.get(
        "/api/v1/memory-documents/000001.SZ",
        headers=auth_headers,
    )

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["stock_name"] == "平安银行"
    assert detail["content"] == "# 平安银行\n\n等待行业相对强度确认。"
    assert detail["size_chars"] == 19
    assert detail["version"] == 2
    assert detail["max_chars"] == 8000


def test_memory_documents_api_returns_not_found_for_missing_or_unowned_document(
    client,
    auth_headers,
    test_db,
    run_async,
):
    run_async(_seed_documents(test_db))

    missing_response = client.get(
        "/api/v1/memory-documents/600519.SH",
        headers=auth_headers,
    )

    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "记忆文档不存在"


@pytest.mark.asyncio
async def test_memory_document_service_orders_and_filters_by_owner(async_db_session, async_create_user):
    user = await async_create_user(async_db_session, username="memory_documents_service")
    async_db_session.add_all(
        [
            MemoryDocument(
                user_id=user.id,
                stock_code="600519.SH",
                content="# 贵州茅台",
                size_chars=6,
                version=1,
                created_at=datetime(2026, 9, 1, 10, 0),
                updated_at=datetime(2026, 9, 3, 10, 0),
            ),
            MemoryDocument(
                user_id=user.id,
                stock_code="000001.SZ",
                content="# 平安银行",
                size_chars=6,
                version=3,
                created_at=datetime(2026, 9, 1, 10, 0),
                updated_at=datetime(2026, 9, 2, 10, 0),
            ),
        ]
    )
    await async_db_session.commit()

    from app.ai.memory_documents.service import memory_document_service

    result = await memory_document_service.list_documents(
        user_id=user.id,
        keyword="000001",
    )

    assert result["total"] == 1
    assert result["items"][0]["stock_code"] == "000001.SZ"
