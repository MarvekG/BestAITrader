import asyncio

import pytest

from app.services.limiter import EngineLimiter


@pytest.mark.asyncio
async def test_engine_limiter_waits_when_limit_is_reached() -> None:
    limiter = EngineLimiter(max_pages=1)
    release = asyncio.Event()
    second_started = asyncio.Event()

    async def hold_slot() -> None:
        async with limiter.acquire():
            await release.wait()

    async def wait_for_slot() -> None:
        async with limiter.acquire():
            second_started.set()

    first_task = asyncio.create_task(hold_slot())
    await asyncio.sleep(0)
    second_task = asyncio.create_task(wait_for_slot())
    await asyncio.sleep(0)

    assert not second_started.is_set()

    release.set()
    await asyncio.wait_for(second_started.wait(), timeout=1)
    await asyncio.gather(first_task, second_task)
