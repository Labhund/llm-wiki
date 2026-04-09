import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.watcher import FileWatcher


@pytest.mark.asyncio
async def test_detects_new_file(sample_vault: Path):
    changes_detected = []

    async def on_change(changed, removed):
        changes_detected.append(("change", changed, removed))

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.2)
    await watcher.start()
    await asyncio.sleep(0.3)

    (sample_vault / "new-page.md").write_text("# New Page\n\nContent.")
    await asyncio.sleep(0.5)

    await watcher.stop()
    assert len(changes_detected) >= 1


@pytest.mark.asyncio
async def test_detects_modified_file(sample_vault: Path):
    changes_detected = []

    async def on_change(changed, removed):
        changes_detected.append(("change", changed, removed))

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.2)
    await watcher.start()
    await asyncio.sleep(0.3)

    existing = sample_vault / "wiki" / "bioinformatics" / "srna-embeddings.md"
    existing.write_text(existing.read_text() + "\nAppended content.")
    await asyncio.sleep(0.5)

    await watcher.stop()
    assert len(changes_detected) >= 1


@pytest.mark.asyncio
async def test_ignores_hidden_dirs(sample_vault: Path):
    changes_detected = []

    async def on_change(changed, removed):
        changes_detected.append(("change", changed, removed))

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.2)
    await watcher.start()
    await asyncio.sleep(0.3)

    hidden = sample_vault / ".obsidian"
    hidden.mkdir()
    (hidden / "config.md").write_text("ignored")
    await asyncio.sleep(0.5)

    await watcher.stop()
    assert len(changes_detected) == 0


@pytest.mark.asyncio
async def test_detects_deleted_file(sample_vault: Path):
    changes_detected = []

    async def on_change(changed, removed):
        changes_detected.append(("change", changed, removed))

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.2)
    await watcher.start()
    await asyncio.sleep(0.3)

    (sample_vault / "wiki" / "no-structure.md").unlink()
    await asyncio.sleep(0.5)

    await watcher.stop()
    assert len(changes_detected) >= 1
    last_change = changes_detected[-1]
    removed_files = last_change[2]
    assert any("no-structure" in str(p) for p in removed_files)
