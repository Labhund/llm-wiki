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
    scheduler.register(ScheduledWorker("b", 2.0, lambda: None))   # type: ignore[arg-type]
    assert scheduler.worker_names == ["a", "b"]
    # workers_info() is the public accessor used by daemon status routes.
    assert scheduler.workers_info() == [("a", 1.0, None), ("b", 2.0, None)]


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
async def test_scheduler_tracks_last_attempt_on_failure():
    """last_attempt is set even when the worker fails; last_run is not."""
    async def failing():
        raise RuntimeError("oops")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("fail", 100.0, failing))
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert scheduler.last_attempt_iso("fail") is not None
    assert scheduler.last_run_iso("fail") is None  # never succeeded


@pytest.mark.asyncio
async def test_scheduler_increments_consecutive_failures():
    """consecutive_failures increments on each failed run."""
    call_count = {"n": 0}

    async def failing():
        call_count["n"] += 1
        raise RuntimeError("oops")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("fail", 0.05, failing))
    await scheduler.start()
    await asyncio.sleep(0.2)
    await scheduler.stop()

    assert scheduler.consecutive_failures("fail") >= 2


@pytest.mark.asyncio
async def test_scheduler_resets_consecutive_failures_on_success():
    """consecutive_failures resets to 0 after a successful run."""
    fail_until = {"n": 2}

    async def sometimes_fails():
        if fail_until["n"] > 0:
            fail_until["n"] -= 1
            raise RuntimeError("not yet")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("maybe", 0.05, sometimes_fails))
    await scheduler.start()
    await asyncio.sleep(0.4)
    await scheduler.stop()

    assert scheduler.consecutive_failures("maybe") == 0
    assert scheduler.last_run_iso("maybe") is not None


@pytest.mark.asyncio
async def test_scheduler_register_duplicate_name_raises():
    scheduler = IntervalScheduler()
    async def noop() -> None:
        pass
    scheduler.register(ScheduledWorker("dup", 1.0, noop))
    with pytest.raises(ValueError):
        scheduler.register(ScheduledWorker("dup", 2.0, noop))


@pytest.mark.asyncio
async def test_scheduler_skips_worker_when_backend_unreachable():
    """Worker is skipped (not failed) when health probe fails."""
    call_count = {"n": 0}

    async def worker_fn():
        call_count["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker(
        name="probe-test",
        interval_seconds=100.0,
        coro_factory=worker_fn,
        health_probe_url="http://127.0.0.1:19999/v1",  # nothing listening here
    ))
    await scheduler.start()
    await asyncio.sleep(0.15)  # enough for the initial run attempt
    await scheduler.stop()

    # Worker should NOT have been called — backend unreachable
    assert call_count["n"] == 0
    # Not a failure — consecutive_failures stays 0
    assert scheduler.consecutive_failures("probe-test") == 0
    # last_attempt NOT set for a skipped run
    assert scheduler.last_attempt_iso("probe-test") is None


@pytest.mark.asyncio
async def test_scheduler_runs_worker_when_no_probe_url():
    """Worker without a health_probe_url runs normally."""
    call_count = {"n": 0}

    async def worker_fn():
        call_count["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker(
        name="no-probe",
        interval_seconds=100.0,
        coro_factory=worker_fn,
        health_probe_url=None,
    ))
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_daemon_server_registers_auditor_worker(sample_vault, tmp_path):
    """Starting DaemonServer registers and runs the auditor worker."""
    from pathlib import Path
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.server import DaemonServer

    sock = tmp_path / "test.sock"
    config = WikiConfig(
        maintenance=MaintenanceConfig(auditor_interval="1s"),
    )
    server = DaemonServer(sample_vault, sock, config=config)
    await server.start()
    try:
        # Auditor should run immediately on start, before the first interval
        await asyncio.sleep(0.2)
        assert "auditor" in server._scheduler.worker_names
        assert server._scheduler.last_run_iso("auditor") is not None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_daemon_server_registers_librarian_workers(sample_vault, tmp_path):
    """Starting DaemonServer registers librarian + authority_recalc workers."""
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.server import DaemonServer

    sock = tmp_path / "librarian.sock"
    config = WikiConfig(
        maintenance=MaintenanceConfig(
            auditor_interval="1h",
            librarian_interval="1h",
            authority_recalc="1h",
        ),
    )
    server = DaemonServer(sample_vault, sock, config=config)
    await server.start()
    try:
        names = set(server._scheduler.worker_names)
        assert "auditor" in names
        assert "librarian" in names
        assert "authority_recalc" in names
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_daemon_server_registers_adversary_worker(sample_vault, tmp_path):
    """Starting DaemonServer registers the adversary worker."""
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.server import DaemonServer

    sock = tmp_path / "adversary.sock"
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_interval="1h"))
    server = DaemonServer(sample_vault, sock, config=config)
    await server.start()
    try:
        names = set(server._scheduler.worker_names)
        assert "adversary" in names
        # All four workers from 5b + 5c + 5d should be registered
        assert {"auditor", "librarian", "authority_recalc", "adversary"} <= names
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_scheduler_status_route(sample_vault, tmp_path):
    """The scheduler-status route returns workers + last-run timestamps."""
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.client import DaemonClient
    from llm_wiki.daemon.server import DaemonServer

    sock_path = tmp_path / "sched-status.sock"
    config = WikiConfig(maintenance=MaintenanceConfig(auditor_interval="1s"))
    server = DaemonServer(sample_vault, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        # Wait for the auditor to run at least once
        await asyncio.sleep(0.2)

        client = DaemonClient(sock_path)
        resp = client.request({"type": "scheduler-status"})

        assert resp["status"] == "ok"
        workers = resp["workers"]
        assert isinstance(workers, list)
        names = {w["name"] for w in workers}
        assert "auditor" in names

        auditor = next(w for w in workers if w["name"] == "auditor")
        assert "interval_seconds" in auditor
        assert auditor["interval_seconds"] == 1.0
        assert auditor["last_run"] is not None
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
