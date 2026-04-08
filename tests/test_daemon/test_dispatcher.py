from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.dispatcher import ChangeDispatcher


@pytest.mark.asyncio
async def test_dispatcher_debounces_rapid_submissions(tmp_path: Path):
    """Three rapid submits within the debounce window collapse into one dispatch."""
    fired: list[Path] = []

    async def on_settled(path: Path) -> None:
        fired.append(path)

    dispatcher = ChangeDispatcher(debounce_secs=0.1, on_settled=on_settled)
    p = tmp_path / "x.md"

    dispatcher.submit(p)
    await asyncio.sleep(0.04)
    dispatcher.submit(p)
    await asyncio.sleep(0.04)
    dispatcher.submit(p)

    await asyncio.sleep(0.2)
    assert fired == [p]

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatcher_independent_paths_dispatch_in_parallel(tmp_path: Path):
    """Different paths debounce independently."""
    fired: list[Path] = []

    async def on_settled(path: Path) -> None:
        fired.append(path)

    dispatcher = ChangeDispatcher(debounce_secs=0.1, on_settled=on_settled)
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"

    dispatcher.submit(a)
    dispatcher.submit(b)
    await asyncio.sleep(0.2)

    assert set(fired) == {a, b}

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatcher_stop_cancels_pending(tmp_path: Path):
    """stop() cancels any pending dispatches before they fire."""
    fired: list[Path] = []

    async def on_settled(path: Path) -> None:
        fired.append(path)

    dispatcher = ChangeDispatcher(debounce_secs=0.5, on_settled=on_settled)
    dispatcher.submit(tmp_path / "x.md")
    await dispatcher.stop()

    await asyncio.sleep(0.6)
    assert fired == []


@pytest.mark.asyncio
async def test_dispatcher_isolates_callback_errors(tmp_path: Path):
    """A callback that raises does not break subsequent dispatches."""
    call_count = {"n": 0}

    async def flaky_callback(path: Path) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated handler failure")

    dispatcher = ChangeDispatcher(debounce_secs=0.05, on_settled=flaky_callback)
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"

    dispatcher.submit(a)
    await asyncio.sleep(0.15)
    dispatcher.submit(b)
    await asyncio.sleep(0.15)

    assert call_count["n"] == 2  # both dispatched even though the first raised

    await dispatcher.stop()
