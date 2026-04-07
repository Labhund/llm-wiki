import asyncio

import pytest

from llm_wiki.daemon.llm_queue import LLMQueue


@pytest.mark.asyncio
async def test_basic_submit():
    queue = LLMQueue(max_concurrent=2)

    async def dummy_task():
        return 42

    result = await queue.submit(dummy_task, priority="query")
    assert result == 42


@pytest.mark.asyncio
async def test_concurrency_limited():
    """Only max_concurrent tasks should run at once."""
    queue = LLMQueue(max_concurrent=1)
    running = []
    max_running = 0

    async def tracked_task(task_id: int):
        nonlocal max_running
        running.append(task_id)
        max_running = max(max_running, len(running))
        await asyncio.sleep(0.1)
        running.remove(task_id)
        return task_id

    results = await asyncio.gather(
        queue.submit(tracked_task, priority="query", task_id=1),
        queue.submit(tracked_task, priority="query", task_id=2),
        queue.submit(tracked_task, priority="query", task_id=3),
    )
    assert sorted(results) == [1, 2, 3]
    assert max_running == 1


@pytest.mark.asyncio
async def test_token_accounting():
    queue = LLMQueue(max_concurrent=2)
    assert queue.tokens_used == 0

    queue.record_tokens(500)
    queue.record_tokens(300)
    assert queue.tokens_used == 800


@pytest.mark.asyncio
async def test_active_count():
    queue = LLMQueue(max_concurrent=2)
    assert queue.active_count == 0

    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocking_task():
        started.set()
        await finish.wait()

    task = asyncio.create_task(queue.submit(blocking_task, priority="query"))
    await started.wait()
    assert queue.active_count == 1

    finish.set()
    await task
    assert queue.active_count == 0
