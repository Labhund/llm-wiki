import asyncio

import pytest

from llm_wiki.daemon.writer import WriteCoordinator


@pytest.mark.asyncio
async def test_lock_serializes_same_page():
    """Writes to the same page are serialized."""
    coordinator = WriteCoordinator()
    order = []

    async def write(name: str, value: str):
        async with coordinator.lock_for(name):
            order.append(f"{value}_start")
            await asyncio.sleep(0.1)
            order.append(f"{value}_end")

    await asyncio.gather(
        write("page-a", "first"),
        write("page-a", "second"),
    )
    # One must complete before the other starts
    assert order.index("first_end") < order.index("second_start") or \
           order.index("second_end") < order.index("first_start")


@pytest.mark.asyncio
async def test_different_pages_parallel():
    """Writes to different pages can run concurrently."""
    coordinator = WriteCoordinator()
    timestamps = {}

    async def write(name: str):
        async with coordinator.lock_for(name):
            timestamps[f"{name}_start"] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.1)
            timestamps[f"{name}_end"] = asyncio.get_event_loop().time()

    await asyncio.gather(write("page-a"), write("page-b"))

    # Both should have started before either finished (parallel execution)
    # Total time should be ~0.1s not ~0.2s
    a_start = timestamps["page-a_start"]
    b_start = timestamps["page-b_start"]
    a_end = timestamps["page-a_end"]
    # b should start before a ends (they overlap)
    assert b_start < a_end


@pytest.mark.asyncio
async def test_reentrant_different_pages():
    coordinator = WriteCoordinator()
    results = []

    async def write(name: str):
        async with coordinator.lock_for(name):
            results.append(name)

    await asyncio.gather(
        write("a"), write("b"), write("c"),
    )
    assert sorted(results) == ["a", "b", "c"]
