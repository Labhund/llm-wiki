import asyncio
import time

import pytest

from llm_wiki.daemon.llm_queue import ActiveJob, LimitExceededError, LLMQueue


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
    assert queue.input_tokens_total == 0
    assert queue.output_tokens_total == 0

    queue.record_tokens(400, 100)   # weighted = 400 + 100*5 = 900
    queue.record_tokens(200, 50)    # weighted = 200 + 50*5  = 450

    assert queue.input_tokens_total == 600
    assert queue.output_tokens_total == 150
    assert queue.tokens_used == 750  # unweighted total


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


@pytest.mark.asyncio
async def test_hourly_weighted_rolling_window():
    """Rolling windows only count tokens within their respective periods."""
    queue = LLMQueue(max_concurrent=1)
    queue.record_tokens(100, 20)   # weighted = 100 + 20*5 = 200

    assert queue.hourly_weighted == 200
    assert queue.daily_weighted == 200

    # Inject an entry stale for hourly (>1h) but still within daily (24h).
    hourly_stale = time.monotonic() - 3601
    queue._hourly.appendleft((hourly_stale, 9999))
    assert queue.hourly_weighted == 200  # swept from hourly

    # Inject an entry stale for both hourly and daily (>24h).
    daily_stale = time.monotonic() - 86401
    queue._daily.appendleft((daily_stale, 9999))
    assert queue.daily_weighted == 200  # swept from daily


@pytest.mark.asyncio
async def test_maintenance_blocked_when_hourly_limit_exceeded():
    """Maintenance calls raise LimitExceededError when hourly limit is hit."""
    queue = LLMQueue(max_concurrent=2, hourly_limit=100)
    queue.record_tokens(50, 10)  # weighted = 50 + 50 = 100 → at limit

    async def dummy():
        return "ok"

    with pytest.raises(LimitExceededError, match="Hourly"):
        await queue.submit(dummy, priority="maintenance")


@pytest.mark.asyncio
async def test_query_allowed_when_hourly_limit_exceeded(caplog):
    """Query/ingest calls log a warning but proceed when hourly limit is hit."""
    import logging
    queue = LLMQueue(max_concurrent=2, hourly_limit=100)
    queue.record_tokens(50, 10)  # weighted = 100 → at limit

    async def dummy():
        return "ok"

    with caplog.at_level(logging.WARNING, logger="llm_wiki.daemon.llm_queue"):
        result = await queue.submit(dummy, priority="query")

    assert result == "ok"
    assert "Hourly weighted token limit reached" in caplog.text


@pytest.mark.asyncio
async def test_maintenance_blocked_when_daily_limit_exceeded():
    """Maintenance calls raise LimitExceededError when daily limit is hit."""
    queue = LLMQueue(max_concurrent=2, daily_limit=500)
    queue.record_tokens(400, 20)  # weighted = 400 + 100 = 500 → at limit

    async def dummy():
        return "ok"

    with pytest.raises(LimitExceededError, match="Daily"):
        await queue.submit(dummy, priority="maintenance")


@pytest.mark.asyncio
async def test_no_limit_enforcement_when_limits_not_set():
    """No LimitExceededError when limits are None (default)."""
    queue = LLMQueue(max_concurrent=2)
    queue.record_tokens(10_000_000, 10_000_000)

    async def dummy():
        return "ok"

    result = await queue.submit(dummy, priority="maintenance")
    assert result == "ok"


@pytest.mark.asyncio
async def test_active_jobs_contains_label():
    """active_jobs exposes the label of an in-flight job."""
    queue = LLMQueue(max_concurrent=2)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocking():
        started.set()
        await finish.wait()

    task = asyncio.create_task(
        queue.submit(blocking, priority="maintenance", label="adversary:verify:protein-dj")
    )
    await started.wait()

    jobs = queue.active_jobs
    assert len(jobs) == 1
    assert jobs[0].label == "adversary:verify:protein-dj"
    assert jobs[0].priority == "maintenance"
    assert jobs[0].elapsed_s >= 0.0

    finish.set()
    await task
    assert queue.active_jobs == []


@pytest.mark.asyncio
async def test_pending_count_while_waiting():
    """pending_count reflects tasks waiting for a semaphore slot."""
    queue = LLMQueue(max_concurrent=1)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocking():
        started.set()
        await finish.wait()

    # First task takes the slot; second must wait
    t1 = asyncio.create_task(queue.submit(blocking, label="job-1"))
    await started.wait()

    t2 = asyncio.create_task(queue.submit(blocking, label="job-2"))
    await asyncio.sleep(0)  # yield so t2 registers as pending

    assert queue.pending_count == 1
    assert queue.active_jobs[0].label == "job-1"

    finish.set()
    await t1
    await t2


@pytest.mark.asyncio
async def test_slots_total():
    queue = LLMQueue(max_concurrent=3)
    assert queue.slots_total == 3


@pytest.mark.asyncio
async def test_pending_count_restored_on_cancel_before_acquire():
    """CancelledError while waiting for semaphore does not leave pending stuck."""
    queue = LLMQueue(max_concurrent=1)
    hold = asyncio.Event()

    async def holder():
        await hold.wait()

    # Fill the slot
    hold_task = asyncio.create_task(queue.submit(holder))
    await asyncio.sleep(0)

    # Start waiter (will block on semaphore)
    wait_task = asyncio.create_task(queue.submit(holder, label="pending-job"))
    await asyncio.sleep(0)
    assert queue.pending_count == 1

    # Cancel the waiter before it acquires
    wait_task.cancel()
    try:
        await wait_task
    except asyncio.CancelledError:
        pass

    assert queue.pending_count == 0

    hold.set()
    await hold_task
