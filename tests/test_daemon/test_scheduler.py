from __future__ import annotations

import asyncio

import pytest

from llm_wiki.daemon.scheduler import IntervalScheduler, ScheduledWorker, parse_interval


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("30s", 30.0),
        ("1s", 1.0),
        ("15m", 900.0),
        ("6h", 21600.0),
        ("12h", 43200.0),
        ("2d", 172800.0),
        ("0s", 0.0),
    ],
)
def test_parse_interval_valid(spec: str, expected: float):
    assert parse_interval(spec) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "abc",
        "5",          # missing unit
        "5x",         # unknown unit
        "h6",         # number after unit
        "-5m",        # negative
        "5.5h",       # fractional not supported in v1
        "5 hours",    # long-form units not supported
    ],
)
def test_parse_interval_invalid_raises(spec: str):
    with pytest.raises(ValueError):
        parse_interval(spec)


def test_parse_interval_strips_whitespace():
    assert parse_interval("  6h  ") == 21600.0


@pytest.mark.asyncio
async def test_scheduler_runs_worker_on_interval():
    """Worker runs once at start, then on each interval."""
    counter = {"n": 0}

    async def increment() -> None:
        counter["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(
        ScheduledWorker(name="incrementer", interval_seconds=0.1, coro_factory=increment)
    )

    await scheduler.start()
    await asyncio.sleep(0.35)
    await scheduler.stop()

    # 1 immediate run + ~3 interval runs (with slack for scheduling jitter)
    assert counter["n"] >= 3, f"expected >=3 runs, got {counter['n']}"


@pytest.mark.asyncio
async def test_scheduler_stop_halts_dispatch():
    """No more worker invocations after stop()."""
    counter = {"n": 0}

    async def increment() -> None:
        counter["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(
        ScheduledWorker(name="incrementer", interval_seconds=0.05, coro_factory=increment)
    )
    await scheduler.start()
    await asyncio.sleep(0.12)
    await scheduler.stop()

    snapshot = counter["n"]
    await asyncio.sleep(0.2)
    assert counter["n"] == snapshot, "worker fired after stop()"


@pytest.mark.asyncio
async def test_scheduler_register_multiple_workers():
    """Multiple workers run independently."""
    a_count = {"n": 0}
    b_count = {"n": 0}

    async def a() -> None:
        a_count["n"] += 1

    async def b() -> None:
        b_count["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("a", 0.1, a))
    scheduler.register(ScheduledWorker("b", 0.1, b))
    await scheduler.start()
    await asyncio.sleep(0.25)
    await scheduler.stop()

    assert a_count["n"] >= 2
    assert b_count["n"] >= 2


@pytest.mark.asyncio
async def test_scheduler_records_last_run_iso():
    """last_run_iso(name) returns an ISO 8601 timestamp after the worker has run."""
    async def noop() -> None:
        pass

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("noop", 1.0, noop))
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    last = scheduler.last_run_iso("noop")
    assert last is not None
    assert "T" in last  # ISO 8601 has a T separator
    assert "+00:00" in last or last.endswith("Z")


def test_scheduler_worker_names():
    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("a", 1.0, lambda: None))   # type: ignore[arg-type]
    scheduler.register(ScheduledWorker("b", 1.0, lambda: None))   # type: ignore[arg-type]
    assert scheduler.worker_names == ["a", "b"]


@pytest.mark.asyncio
async def test_scheduler_isolates_worker_errors():
    """A worker that raises does not crash sibling workers or its own loop."""
    good_count = {"n": 0}
    bad_count = {"n": 0}

    async def good() -> None:
        good_count["n"] += 1

    async def bad() -> None:
        bad_count["n"] += 1
        raise RuntimeError("simulated worker failure")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("bad", 0.1, bad))
    scheduler.register(ScheduledWorker("good", 0.1, good))
    await scheduler.start()
    await asyncio.sleep(0.35)
    await scheduler.stop()

    # Both workers should have continued running across the failure
    assert good_count["n"] >= 3
    assert bad_count["n"] >= 3, "bad worker should retry on next interval"


@pytest.mark.asyncio
async def test_scheduler_register_duplicate_name_raises():
    scheduler = IntervalScheduler()
    async def noop() -> None:
        pass
    scheduler.register(ScheduledWorker("dup", 1.0, noop))
    with pytest.raises(ValueError):
        scheduler.register(ScheduledWorker("dup", 2.0, noop))
