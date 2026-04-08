from __future__ import annotations

from pathlib import Path

from llm_wiki.daemon.snapshot import PageSnapshotStore


def test_snapshot_set_and_get(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.set("srna-embeddings", "Original content.\n")
    assert store.get("srna-embeddings") == "Original content.\n"


def test_snapshot_get_missing_returns_none(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    assert store.get("nope") is None


def test_snapshot_overwrite(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.set("foo", "v1")
    store.set("foo", "v2")
    assert store.get("foo") == "v2"


def test_snapshot_remove(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.set("foo", "bar")
    store.remove("foo")
    assert store.get("foo") is None


def test_snapshot_remove_missing_is_noop(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.remove("nope")  # must not raise


def test_snapshot_creates_dir_on_demand(tmp_path: Path):
    state_dir = tmp_path / "fresh-state"
    store = PageSnapshotStore(state_dir)
    store.set("page", "content")
    assert (state_dir / "snapshots").is_dir()


def test_snapshot_unicode_content_round_trip(tmp_path: Path):
    store = PageSnapshotStore(tmp_path)
    store.set("page", "α β γ — café résumé\n")
    assert store.get("page") == "α β γ — café résumé\n"
