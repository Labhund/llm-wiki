from __future__ import annotations

import json
from pathlib import Path

from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride


def test_load_missing_file_returns_empty(tmp_path: Path):
    store = ManifestOverrides.load(tmp_path / "nope.json")
    assert store.get("any") is None


def test_set_and_get_round_trip(tmp_path: Path):
    path = tmp_path / "overrides.json"
    store = ManifestOverrides.load(path)
    override = PageOverride(
        tags=["bioinformatics", "validation"],
        summary_override="Validates sRNA embeddings via PCA + k-means",
        authority=0.74,
        last_corroborated="2026-04-01T12:00:00+00:00",
        read_count=12,
        usefulness=0.82,
        last_refreshed_read_count=10,
    )
    store.set("srna-embeddings", override)
    store.save()

    reloaded = ManifestOverrides.load(path)
    got = reloaded.get("srna-embeddings")
    assert got is not None
    assert got.tags == ["bioinformatics", "validation"]
    assert got.summary_override == "Validates sRNA embeddings via PCA + k-means"
    assert abs(got.authority - 0.74) < 1e-6
    assert got.last_corroborated == "2026-04-01T12:00:00+00:00"
    assert got.read_count == 12
    assert abs(got.usefulness - 0.82) < 1e-6
    assert got.last_refreshed_read_count == 10


def test_get_missing_returns_none(tmp_path: Path):
    store = ManifestOverrides.load(tmp_path / "x.json")
    assert store.get("nope") is None


def test_save_creates_atomic_file(tmp_path: Path):
    """save() writes the file (no temp leftovers in steady state)."""
    path = tmp_path / "overrides.json"
    store = ManifestOverrides.load(path)
    store.set("a", PageOverride(authority=0.5))
    store.save()

    assert path.exists()
    siblings = list(path.parent.iterdir())
    # No leftover .tmp files after a successful save
    assert all(not p.name.endswith(".tmp") for p in siblings)


def test_save_writes_valid_json(tmp_path: Path):
    path = tmp_path / "overrides.json"
    store = ManifestOverrides.load(path)
    store.set("a", PageOverride(tags=["x"], authority=0.5))
    store.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "a" in data
    assert data["a"]["tags"] == ["x"]
    assert data["a"]["authority"] == 0.5


def test_prune_removes_unknown_pages(tmp_path: Path):
    path = tmp_path / "overrides.json"
    store = ManifestOverrides.load(path)
    store.set("alive", PageOverride(authority=0.5))
    store.set("deleted", PageOverride(authority=0.3))
    store.prune({"alive"})
    store.save()

    reloaded = ManifestOverrides.load(path)
    assert reloaded.get("alive") is not None
    assert reloaded.get("deleted") is None


def test_delete_removes_one_entry(tmp_path: Path):
    store = ManifestOverrides.load(tmp_path / "x.json")
    store.set("a", PageOverride(authority=0.5))
    store.set("b", PageOverride(authority=0.3))
    store.delete("a")
    assert store.get("a") is None
    assert store.get("b") is not None


def test_creates_parent_dir_on_save(tmp_path: Path):
    path = tmp_path / "deep" / "nested" / "overrides.json"
    store = ManifestOverrides.load(path)
    store.set("a", PageOverride(authority=0.5))
    store.save()
    assert path.exists()


def test_save_concurrent_writes_do_not_corrupt(tmp_path):
    """Two concurrent saves must not produce a corrupt or empty file."""
    import threading
    overrides_path = tmp_path / "manifest_overrides.json"

    # Two ManifestOverrides instances pointing at the same file,
    # each writing different content.
    store_a = ManifestOverrides(overrides_path)
    store_a.set("page-a", PageOverride(tags=["tag-a"], authority=0.5))
    store_b = ManifestOverrides(overrides_path)
    store_b.set("page-b", PageOverride(tags=["tag-b"], authority=0.7))

    errors = []

    def save_a():
        try:
            for _ in range(20):
                store_a.save()
        except Exception as e:
            errors.append(e)

    def save_b():
        try:
            for _ in range(20):
                store_b.save()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=save_a)
    t2 = threading.Thread(target=save_b)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], f"save raised: {errors}"
    # File must be valid JSON (not empty, not partially-written)
    ManifestOverrides.load(overrides_path)
    assert overrides_path.exists()
    text = overrides_path.read_text(encoding="utf-8")
    json.loads(text)  # raises if corrupt

    # No leftover .tmp files
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"leftover temp files: {leftovers}"
