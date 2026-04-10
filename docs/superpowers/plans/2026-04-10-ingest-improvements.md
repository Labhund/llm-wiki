# Ingest Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix ingest timeouts by streaming progress frames to the CLI, and make dry-run fast by stopping after concept extraction.

**Architecture:** `IngestAgent.ingest()` gains an `on_progress` async callback called at key stages. The server's `_handle_ingest_stream` method writes those frames directly to the socket writer as they arrive; MCP callers use the existing single-response path unchanged. Dry-run returns early after concept extraction — no page-content LLM calls. The CLI renders a TTY spinner plus one `[DONE]` line per concept; non-TTY output is plain `[PROGRESS]`/`[DONE]`/`[SUMMARY]` lines suitable for piping.

**Tech Stack:** Python asyncio, Unix socket IPC (length-prefix framing already in `daemon/protocol.py`), `threading.RLock` for spinner/print coordination, `itertools.cycle` for braille frames.

**Spec:** `docs/superpowers/specs/2026-04-10-ingest-improvements-design.md`

---

## File Map

| File | Change |
|------|--------|
| `src/llm_wiki/ingest/agent.py` | Lightweight dry-run (early return after concept extraction) + `on_progress` callback |
| `src/llm_wiki/daemon/server.py` | Extract `_ingest_result_to_response` helper; add `_handle_ingest_stream`; route `stream:true` in `_handle_client` |
| `src/llm_wiki/daemon/client.py` | Add `stream_ingest_sync(msg, on_frame)` |
| `src/llm_wiki/cli/main.py` | Add `_Spinner`; update `ingest` command for streaming + new dry-run output |
| `tests/test_ingest/test_agent.py` | Tests for lightweight dry-run and `on_progress` callback |
| `tests/test_ingest/test_ingest_route.py` | Tests for streaming route (progress frames + done frame) |
| `tests/test_daemon/test_client.py` | Test for `stream_ingest_sync` |
| `tests/test_cli/test_commands.py` | Tests for streaming CLI output and new dry-run output |

---

## Task 1: Set up worktree

**Files:** none (git only)

- [ ] **Step 1: Create the feature worktree**

```bash
git worktree add ../llm-wiki-ingest feat/ingest-improvements
cd ../llm-wiki-ingest
```

- [ ] **Step 2: Verify you're on the right branch**

```bash
git branch
```
Expected: `* feat/ingest-improvements`

---

## Task 2: Lightweight dry-run

Stop dry-run after concept extraction. Remove section details from server response and CLI output.

**Files:**
- Modify: `src/llm_wiki/ingest/agent.py:143-201`
- Modify: `src/llm_wiki/daemon/server.py:1213-1244` (dry-run response block)
- Modify: `src/llm_wiki/cli/main.py:318-335` (dry-run output block)
- Test: `tests/test_ingest/test_agent.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ingest/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_dry_run_makes_only_one_llm_call(tmp_path: Path):
    """Dry-run stops after concept extraction — no page-content LLM calls."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions. k-means clusters data.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
        {"name": "k-means", "title": "K-Means", "passages": ["k-means clusters data."]},
    ])
    mock_llm = MockLLMClient([concept_response])  # only 1 response scripted
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path, dry_run=True)

    assert len(mock_llm.calls) == 1   # concept extraction only
    assert result.concepts_found == 2


@pytest.mark.asyncio
async def test_dry_run_returns_previews_without_sections(tmp_path: Path):
    """Dry-run ConceptPreview has name/title/is_update/passages but no sections."""
    (tmp_path / "raw").mkdir()
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    # One existing page, one new
    (wiki_dir / "pca.md").write_text("---\ntitle: PCA\n---\n\nExisting.")

    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA content. K-Means content.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA content."]},
        {"name": "k-means", "title": "K-Means", "passages": ["K-Means content."]},
    ])
    mock_llm = MockLLMClient([concept_response])
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path, dry_run=True)

    assert len(result.concepts_planned) == 2
    pca = next(c for c in result.concepts_planned if c.name == "pca")
    km = next(c for c in result.concepts_planned if c.name == "k-means")

    assert pca.is_update is True
    assert km.is_update is False
    assert pca.passages == ["PCA content."]
    assert pca.sections == []   # no section generation in dry-run
    assert km.sections == []
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_ingest/test_agent.py::test_dry_run_makes_only_one_llm_call \
       tests/test_ingest/test_agent.py::test_dry_run_returns_previews_without_sections -v
```
Expected: both FAIL (dry-run currently makes 1 + N calls and tries to generate sections)

- [ ] **Step 3: Restructure `ingest()` in agent.py**

In `src/llm_wiki/ingest/agent.py`, add `Awaitable, Callable` to the `typing` import:

```python
from typing import TYPE_CHECKING, Awaitable, Callable
```

Replace the block from `if not concepts:` through the end of the `for concept in concepts:` loop (approximately lines 152–202) with:

```python
        if not concepts:
            logger.info("No concepts identified in %s", source_path)
            return result

        # Dry-run: stop here — no page-content generation
        if dry_run:
            for concept in concepts:
                page_path = wiki_dir / f"{concept.name}.md"
                result.concepts_planned.append(ConceptPreview(
                    name=concept.name,
                    title=concept.title,
                    is_update=page_path.exists(),
                    passages=concept.passages,
                    sections=[],
                ))
            return result

        # Live ingest: generate page content and write
        for i, concept in enumerate(concepts):
            page_messages = compose_page_content_messages(
                concept_title=concept.title,
                passages=concept.passages,
                source_ref=source_ref,
            )
            page_response = await self._llm.complete(
                page_messages, temperature=0.5, priority="ingest"
            )
            sections = parse_page_content(page_response.content)
            if not sections:
                logger.warning(
                    "No sections generated for concept %r from %s",
                    concept.name, source_path,
                )
                continue

            if write_service is not None:
                await self._write_via_service(
                    write_service, wiki_dir, concept, sections, source_ref,
                    author=author, connection_id=connection_id, result=result,
                )
            else:
                # Legacy direct-write path
                wiki_dir.mkdir(parents=True, exist_ok=True)
                written = write_page(
                    wiki_dir, concept.name, concept.title, sections, source_ref,
                )
                if written.was_update:
                    result.pages_updated.append(concept.name)
                else:
                    result.pages_created.append(concept.name)
```

- [ ] **Step 4: Run the new tests — expect PASS**

```bash
pytest tests/test_ingest/test_agent.py::test_dry_run_makes_only_one_llm_call \
       tests/test_ingest/test_agent.py::test_dry_run_returns_previews_without_sections -v
```
Expected: both PASS

- [ ] **Step 5: Run the full agent test suite — expect no regressions**

```bash
pytest tests/test_ingest/test_agent.py -v
```
Expected: all PASS

- [ ] **Step 6: Update the server dry-run response shape**

In `src/llm_wiki/daemon/server.py`, find the dry-run response block inside `_handle_ingest` (the block starting with `if dry_run:`). Replace the inner `concepts` list-building loop:

```python
        # Dry-run response
        if dry_run:
            concepts = []
            for cp in result.concepts_planned:
                concepts.append({
                    "name": cp.name,
                    "title": cp.title,
                    "action": "update" if cp.is_update else "create",
                    "passage_count": len(cp.passages),
                })
            return {
                "status": "ok",
                "dry_run": True,
                "source_path": str(source_path),
                "source_chars": result.source_chars,
                "concepts_found": result.concepts_found,
                "extraction_warning": result.extraction_warning,
                "concepts": concepts,
                "message": "DRY RUN — no pages written",
            }
```

(The old code built `sections` per concept and included `section_count`, `content_chars`, `sections` — remove all of that.)

- [ ] **Step 7: Update the CLI dry-run output**

In `src/llm_wiki/cli/main.py`, replace the `if dry_run:` block in the `ingest` command (currently lines ~318–335) with:

```python
    if dry_run:
        click.echo("DRY RUN — no pages written")
        click.echo(f"Source: {resp['source_path']} ({resp['source_chars']} chars)")
        if resp.get("extraction_warning"):
            click.echo(f"  Warning: {resp['extraction_warning']}")
        for c in resp.get("concepts", []):
            action = "UPD" if c["action"] == "update" else "NEW"
            click.echo(f"  [{action}] {c['name']}  \"{c['title']}\"  ({c['passage_count']} passages)")
        click.echo(f"{resp['concepts_found']} concepts total")
        return
```

- [ ] **Step 8: Write and run a CLI dry-run test**

Add to `tests/test_cli/test_commands.py`:

```python
def test_ingest_dry_run_output(daemon_for_cli, monkeypatch, tmp_path):
    """Dry-run output shows concept list without section details."""
    from llm_wiki.daemon.client import DaemonClient

    def fake_request(self, msg):
        return {
            "status": "ok",
            "dry_run": True,
            "source_path": msg["source_path"],
            "source_chars": 1000,
            "extraction_warning": None,
            "concepts_found": 2,
            "concepts": [
                {"name": "pca", "title": "PCA", "action": "create", "passage_count": 3},
                {"name": "k-means", "title": "K-Means", "action": "update", "passage_count": 2},
            ],
        }

    monkeypatch.setattr(DaemonClient, "request", fake_request)

    vault_path = daemon_for_cli
    source = vault_path / "test.md"
    source.write_text("# Test")

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--dry-run", "--vault", str(vault_path)])
    assert result.exit_code == 0, result.output
    assert "[NEW]" in result.output
    assert "[UPD]" in result.output
    assert "2 concepts total" in result.output
    assert "section" not in result.output
    assert "content_chars" not in result.output
```

Run it:

```bash
pytest tests/test_cli/test_commands.py::test_ingest_dry_run_output -v
```
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/llm_wiki/ingest/agent.py \
        src/llm_wiki/daemon/server.py \
        src/llm_wiki/cli/main.py \
        tests/test_ingest/test_agent.py \
        tests/test_cli/test_commands.py
git commit -m "feat: lightweight dry-run stops after concept extraction"
```

---

## Task 3: on_progress callback in IngestAgent

**Files:**
- Modify: `src/llm_wiki/ingest/agent.py`
- Test: `tests/test_ingest/test_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ingest/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_on_progress_callback_receives_correct_frames(tmp_path: Path):
    """on_progress receives extracting → concepts_found → concept_done frames in order."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions. k-means clusters data.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
        {"name": "k-means", "title": "K-Means", "passages": ["k-means clusters data."]},
    ])
    pca_sections = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "PCA [[raw/paper.md]]."},
    ])
    km_sections = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "k-means [[raw/paper.md]]."},
    ])

    mock_llm = MockLLMClient([concept_response, pca_sections, km_sections])
    agent = IngestAgent(mock_llm, WikiConfig())

    frames: list[dict] = []

    async def capture(frame: dict) -> None:
        frames.append(frame)

    await agent.ingest(source, tmp_path, on_progress=capture)

    stages = [f["stage"] for f in frames]
    assert stages[0] == "extracting"
    assert stages[1] == "concepts_found"
    assert frames[1]["count"] == 2
    assert stages[2] == "concept_done"
    assert frames[2]["name"] == "pca"
    assert frames[2]["action"] in ("created", "updated")
    assert frames[2]["num"] == 1
    assert frames[2]["total"] == 2
    assert stages[3] == "concept_done"
    assert frames[3]["name"] == "k-means"
    assert frames[3]["num"] == 2


@pytest.mark.asyncio
async def test_on_progress_none_is_safe(tmp_path: Path):
    """on_progress=None (default) works — no errors, result is correct."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    source = tmp_path / "raw" / "paper.md"
    source.write_text("# Paper\n\nPCA reduces dimensions.")

    concept_response = _concept_json([
        {"name": "pca", "title": "PCA", "passages": ["PCA reduces dimensions."]},
    ])
    pca_sections = _sections_json([
        {"name": "overview", "heading": "Overview", "content": "PCA [[raw/paper.md]]."},
    ])
    mock_llm = MockLLMClient([concept_response, pca_sections])
    agent = IngestAgent(mock_llm, WikiConfig())

    result = await agent.ingest(source, tmp_path)  # no on_progress kwarg

    assert result.pages_created == ["pca"]
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_ingest/test_agent.py::test_on_progress_callback_receives_correct_frames \
       tests/test_ingest/test_agent.py::test_on_progress_none_is_safe -v
```
Expected: `test_on_progress_callback_receives_correct_frames` FAILS (no `on_progress` param yet), `test_on_progress_none_is_safe` PASSES.

- [ ] **Step 3: Add the on_progress parameter and call sites to agent.py**

In `src/llm_wiki/ingest/agent.py`, update the `ingest()` signature:

```python
    async def ingest(
        self,
        source_path: Path,
        vault_root: Path,
        *,
        author: str = "cli",
        connection_id: str = "cli",
        write_service: "PageWriteService | None" = None,
        dry_run: bool = False,
        source_type: str = "paper",
        on_progress: "Callable[[dict], Awaitable[None]] | None" = None,
    ) -> IngestResult:
```

Add progress calls in the body. After companion init and before `extraction = await extract_text(...)`:

```python
        if on_progress:
            await on_progress({"stage": "extracting"})
```

After `concepts = parse_concept_extraction(response.content)` and before `if not concepts:`:

```python
        if on_progress:
            await on_progress({"stage": "concepts_found", "count": len(concepts)})
```

In the live ingest loop, after the write (both `write_service` and legacy paths), add:

```python
            if on_progress:
                created_delta = len(result.pages_created) - created_before
                action = "created" if created_delta > 0 else "updated"
                await on_progress({
                    "stage": "concept_done",
                    "name": concept.name,
                    "title": concept.title,
                    "action": action,
                    "num": i + 1,
                    "total": len(concepts),
                })
```

You'll need to capture `created_before = len(result.pages_created)` at the top of each loop iteration, before the write calls:

```python
        for i, concept in enumerate(concepts):
            ...  # (page_messages, page_response, sections, continue-if-empty)

            created_before = len(result.pages_created)

            if write_service is not None:
                await self._write_via_service(...)
            else:
                ...

            if on_progress:
                action = "created" if len(result.pages_created) > created_before else "updated"
                await on_progress({
                    "stage": "concept_done",
                    "name": concept.name,
                    "title": concept.title,
                    "action": action,
                    "num": i + 1,
                    "total": len(concepts),
                })
```

- [ ] **Step 4: Run the tests — expect PASS**

```bash
pytest tests/test_ingest/test_agent.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/ingest/agent.py tests/test_ingest/test_agent.py
git commit -m "feat: add on_progress callback to IngestAgent.ingest()"
```

---

## Task 4: Server streaming handler

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Test: `tests/test_ingest/test_ingest_route.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ingest/test_ingest_route.py`:

```python
async def _stream_request(sock_path: Path, msg: dict) -> list[dict]:
    """Read all frames from a streaming ingest request."""
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    frames = []
    try:
        await write_message(writer, msg)
        while True:
            frame = await read_message(reader)
            frames.append(frame)
            if frame.get("type") in ("done", "error"):
                break
    finally:
        writer.close()
        await writer.wait_closed()
    return frames


@pytest.mark.asyncio
async def test_ingest_stream_route_sends_progress_and_done(server_with_ingest, monkeypatch):
    """Streaming ingest sends progress frames then a done frame."""
    server, sock_path = server_with_ingest

    async def fake_ingest(self_agent, source_path, vault_root, *, on_progress=None, **kwargs):
        if on_progress:
            await on_progress({"stage": "extracting"})
            await on_progress({"stage": "concepts_found", "count": 2})
            await on_progress({
                "stage": "concept_done", "name": "foo", "title": "Foo",
                "action": "created", "num": 1, "total": 2,
            })
            await on_progress({
                "stage": "concept_done", "name": "bar", "title": "Bar",
                "action": "updated", "num": 2, "total": 2,
            })
        from llm_wiki.ingest.agent import IngestResult
        from pathlib import Path as _Path
        result = IngestResult(
            source_path=_Path(source_path),
            pages_created=["foo"],
            pages_updated=["bar"],
        )
        return result

    monkeypatch.setattr("llm_wiki.ingest.agent.IngestAgent.ingest", fake_ingest)

    frames = await _stream_request(sock_path, {
        "type": "ingest",
        "source_path": "/any/path.md",
        "author": "test",
        "connection_id": "test-conn",
        "stream": True,
    })

    types = [f["type"] for f in frames]
    assert types == ["progress", "progress", "progress", "progress", "done"]
    assert frames[0]["stage"] == "extracting"
    assert frames[1]["stage"] == "concepts_found"
    assert frames[1]["count"] == 2
    assert frames[2]["stage"] == "concept_done"
    assert frames[2]["name"] == "foo"
    assert frames[4]["status"] == "ok"
    assert frames[4]["pages_created"] == 1
    assert frames[4]["pages_updated"] == 1


@pytest.mark.asyncio
async def test_ingest_stream_route_missing_source_path_returns_error(server_with_ingest):
    """Streaming ingest validates required fields, sends error frame."""
    server, sock_path = server_with_ingest

    frames = await _stream_request(sock_path, {
        "type": "ingest",
        "connection_id": "test-conn",
        "stream": True,
        # source_path missing
    })

    assert len(frames) == 1
    assert frames[0]["status"] == "error"
    assert "source_path" in frames[0]["message"]
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_ingest/test_ingest_route.py::test_ingest_stream_route_sends_progress_and_done \
       tests/test_ingest/test_ingest_route.py::test_ingest_stream_route_missing_source_path_returns_error -v
```
Expected: both FAIL (no streaming route yet)

- [ ] **Step 3: Extract `_ingest_result_to_response` helper in server.py**

After `_handle_ingest` (around line 1282), add this private method to `DaemonServer`:

```python
    def _ingest_result_to_response(self, result: "IngestResult") -> dict:
        """Build the live-ingest response dict from an IngestResult.

        Shared by _handle_ingest (sync response) and _handle_ingest_stream
        (done frame). The streaming path adds "type": "done" on top.
        """
        cap = self._config.mcp.ingest_response_max_pages
        all_pages = result.pages_created + result.pages_updated
        truncated = len(all_pages) > cap
        shown = set(all_pages[:cap]) if truncated else set(all_pages)
        warnings = []
        if truncated:
            warnings.append({
                "code": "response-truncated",
                "total_affected": len(all_pages),
                "shown": cap,
                "message": (
                    f"{len(all_pages)} pages affected, showing the first {cap}. "
                    f"Use wiki_lint to see the full attention map."
                ),
            })
        if result.extraction_warning:
            warnings.append({
                "code": "extraction-quality",
                "message": result.extraction_warning,
            })
        response = {
            "status": "ok",
            "pages_created": len(result.pages_created),
            "pages_updated": len(result.pages_updated),
            "created": [n for n in result.pages_created if n in shown],
            "updated": [n for n in result.pages_updated if n in shown],
            "concepts_found": result.concepts_found,
        }
        if truncated:
            response["truncated"] = True
            response["shown"] = cap
        if warnings:
            response["warnings"] = warnings
        return response
```

Then update `_handle_ingest` to use it. Replace the live-ingest response block (the `cap = ...` through `return response` lines, approximately 1247–1282) with:

```python
        return self._ingest_result_to_response(result)
```

- [ ] **Step 4: Add `_handle_ingest_stream` to server.py**

Add this method to `DaemonServer`, below `_handle_ingest`:

```python
    async def _handle_ingest_stream(
        self,
        request: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a streaming ingest request, writing frames directly to writer."""
        if "source_path" not in request:
            await write_message(writer, {
                "status": "error", "message": "Missing required field: source_path"
            })
            return
        if "connection_id" not in request:
            await write_message(writer, {
                "status": "error", "message": "Missing required field: connection_id"
            })
            return

        from llm_wiki.ingest.agent import IngestAgent
        from llm_wiki.traverse.llm_client import LLMClient

        author = request.get("author", "cli")
        connection_id = request["connection_id"]
        source_path = Path(request["source_path"])
        source_type = request.get("source_type", "paper")
        backend = self._config.llm.resolve("ingest")
        llm = LLMClient(
            self._llm_queue,
            model=backend.model,
            api_base=backend.api_base,
            api_key=backend.api_key,
        )
        agent = IngestAgent(llm, self._config)

        concepts_written = 0

        async def on_progress(frame: dict) -> None:
            nonlocal concepts_written
            await write_message(writer, {"type": "progress", **frame})
            if frame.get("stage") == "concept_done":
                concepts_written += 1

        try:
            result = await agent.ingest(
                source_path, self._vault_root,
                author=author,
                connection_id=connection_id,
                write_service=self._page_write_service,
                dry_run=False,
                source_type=source_type,
                on_progress=on_progress,
            )
        except Exception as exc:
            logger.exception("Streaming ingest failed after %d concepts", concepts_written)
            await write_message(writer, {
                "type": "error",
                "status": "error",
                "message": str(exc),
                "concepts_written": concepts_written,
            })
            return
        finally:
            try:
                await self.rescan()
            except Exception:
                logger.warning("Failed to rescan vault after streaming ingest")

        done_frame = self._ingest_result_to_response(result)
        done_frame["type"] = "done"
        await write_message(writer, done_frame)
```

Note: `Path` should already be imported at the top of server.py; `asyncio` is imported; `logger` is module-level. Add `from pathlib import Path` at the top if not already present.

- [ ] **Step 5: Route `stream:true` in `_handle_client`**

In `_handle_client` (around line 431), change:

```python
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await read_message(reader)
            if request.get("type") == "ingest" and request.get("stream"):
                await self._handle_ingest_stream(request, writer)
                return
            response = await self._route(request)
            await write_message(writer, response)
        except Exception as exc:
            try:
                await write_message(writer, {"status": "error", "message": str(exc)})
            except Exception:
                pass
            logger.exception("Error handling request")
        finally:
            writer.close()
            await writer.wait_closed()
```

- [ ] **Step 6: Run the tests — expect PASS**

```bash
pytest tests/test_ingest/test_ingest_route.py -v
```
Expected: all PASS (including the two new streaming tests)

- [ ] **Step 7: Run broader test suite to check for regressions**

```bash
pytest tests/test_ingest/ tests/test_daemon/test_server.py -v
```
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_ingest/test_ingest_route.py
git commit -m "feat: add streaming ingest handler (_handle_ingest_stream)"
```

---

## Task 5: Client `stream_ingest_sync`

**Files:**
- Modify: `src/llm_wiki/daemon/client.py`
- Test: `tests/test_daemon/test_client.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_daemon/test_client.py`:

```python
def test_stream_ingest_sync_receives_all_frames(tmp_path):
    """stream_ingest_sync calls on_frame for each frame including done."""
    import asyncio
    import threading

    sock_path = tmp_path / "stream_test.sock"

    async def run_server():
        from llm_wiki.daemon.protocol import read_message, write_message as wm

        async def handle(reader, writer):
            await read_message(reader)  # consume the request
            await wm(writer, {"type": "progress", "stage": "extracting"})
            await wm(writer, {"type": "progress", "stage": "concepts_found", "count": 1})
            await wm(writer, {"type": "done", "status": "ok", "pages_created": 1,
                               "pages_updated": 0, "created": ["foo"],
                               "updated": [], "concepts_found": 1})
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle, path=str(sock_path))
        return server

    loop = asyncio.new_event_loop()
    server = loop.run_until_complete(run_server())
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    try:
        client = DaemonClient(sock_path)
        frames = []
        client.stream_ingest_sync({"type": "ingest", "stream": True}, frames.append)

        assert len(frames) == 3
        assert frames[0] == {"type": "progress", "stage": "extracting"}
        assert frames[1]["stage"] == "concepts_found"
        assert frames[2]["type"] == "done"
        assert frames[2]["status"] == "ok"
    finally:
        loop.call_soon_threadsafe(server.close)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/test_daemon/test_client.py::test_stream_ingest_sync_receives_all_frames -v
```
Expected: FAIL (`DaemonClient` has no `stream_ingest_sync`)

- [ ] **Step 3: Add `stream_ingest_sync` to client.py**

In `src/llm_wiki/daemon/client.py`, add this method to `DaemonClient` after `_sync_request`:

```python
    def stream_ingest_sync(
        self, msg: dict, on_frame: "Callable[[dict], None]"
    ) -> None:
        """Send an ingest request and call on_frame for each response frame.

        Reads frames until type is 'done' or 'error'. Raises if on_frame raises.
        Uses a 300-second per-frame timeout — enough for slow LLM backends.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(300.0)
        try:
            sock.connect(str(self._socket_path))
            write_message_sync(sock, msg)
            while True:
                frame = read_message_sync(sock)
                on_frame(frame)
                if frame.get("type") in ("done", "error"):
                    break
        finally:
            sock.close()
```

Add to the top-level imports in client.py:
```python
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    pass
```
(Or add `Callable` to an existing typing import if one is already there.)

- [ ] **Step 4: Run the test — expect PASS**

```bash
pytest tests/test_daemon/test_client.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/client.py tests/test_daemon/test_client.py
git commit -m "feat: add stream_ingest_sync to DaemonClient"
```

---

## Task 6: CLI streaming ingest command

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Test: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli/test_commands.py`:

```python
def test_ingest_streaming_output(daemon_for_cli, monkeypatch, tmp_path):
    """CLI ingest prints [PROGRESS], [DONE], [SUMMARY] lines (non-TTY mode)."""
    from llm_wiki.daemon.client import DaemonClient

    def fake_stream(self, msg, on_frame):
        on_frame({"type": "progress", "stage": "extracting"})
        on_frame({"type": "progress", "stage": "concepts_found", "count": 2})
        on_frame({
            "type": "progress", "stage": "concept_done",
            "name": "boltz-diffusion", "title": "Boltz Diffusion", "action": "created",
            "num": 1, "total": 2,
        })
        on_frame({
            "type": "progress", "stage": "concept_done",
            "name": "structure-prediction", "title": "Structure Prediction", "action": "updated",
            "num": 2, "total": 2,
        })
        on_frame({
            "type": "done", "status": "ok",
            "pages_created": 1, "pages_updated": 1,
            "created": ["boltz-diffusion"], "updated": ["structure-prediction"],
            "concepts_found": 2,
        })

    monkeypatch.setattr(DaemonClient, "stream_ingest_sync", fake_stream)

    vault_path = daemon_for_cli
    source = vault_path / "paper.md"
    source.write_text("# Test paper")

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--vault", str(vault_path)])

    assert result.exit_code == 0, result.output
    assert "[PROGRESS] concepts_found: 2" in result.output
    assert "[DONE] boltz-diffusion (created)" in result.output
    assert "[DONE] structure-prediction (updated)" in result.output
    assert "[SUMMARY] 1 created, 1 updated" in result.output
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/test_cli/test_commands.py::test_ingest_streaming_output -v
```
Expected: FAIL (ingest command doesn't call `stream_ingest_sync` yet)

- [ ] **Step 3: Add `_Spinner` class to cli/main.py**

Add this class near the top of `src/llm_wiki/cli/main.py`, after the imports and before the `cli` group definition:

```python
import itertools
import sys
import threading
import time


class _Spinner:
    """Braille spinner for TTY progress display.

    Uses a threading.RLock so concept lines can be printed atomically
    without interleaving with spinner writes.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self._label = ""
        self._running = False
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._line_width = 0

    def start(self, label: str = "") -> None:
        self._label = label
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, label: str) -> None:
        with self._lock:
            self._label = label

    def print_line(self, line: str) -> None:
        """Clear spinner, print a line, let spinner resume."""
        with self._lock:
            sys.stdout.write("\r" + " " * (self._line_width + 2) + "\r")
            sys.stdout.flush()
            print(line)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        with self._lock:
            sys.stdout.write("\r" + " " * (self._line_width + 2) + "\r")
            sys.stdout.flush()

    def _spin(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if not self._running:
                break
            with self._lock:
                line = f"{frame}  {self._label}"
                self._line_width = len(line)
                sys.stdout.write(f"\r{line}")
                sys.stdout.flush()
            time.sleep(0.08)
```

- [ ] **Step 4: Update the `ingest` command**

In `src/llm_wiki/cli/main.py`, replace the `ingest` function body (keeping the `@cli.command()` and `@click` decorators intact). The new body:

```python
def ingest(source_path: Path, vault_path: Path, dry_run: bool) -> None:
    """Ingest a source document — extracts concepts and creates wiki pages."""
    import uuid as _uuid
    client = _get_client(vault_path)
    msg: dict = {
        "type": "ingest",
        "source_path": str(source_path.resolve()),
        "author": "cli",
        "connection_id": _uuid.uuid4().hex,
        "dry_run": dry_run,
    }

    if dry_run:
        resp = client.request(msg)
        if resp["status"] != "ok":
            raise click.ClickException(resp.get("message", "Ingest failed"))
        click.echo("DRY RUN — no pages written")
        click.echo(f"Source: {resp['source_path']} ({resp['source_chars']} chars)")
        if resp.get("extraction_warning"):
            click.echo(f"  Warning: {resp['extraction_warning']}")
        for c in resp.get("concepts", []):
            action = "UPD" if c["action"] == "update" else "NEW"
            click.echo(f"  [{action}] {c['name']}  \"{c['title']}\"  ({c['passage_count']} passages)")
        click.echo(f"{resp['concepts_found']} concepts total")
        return

    # Live streaming ingest
    msg["stream"] = True
    is_tty = sys.stdout.isatty()
    spinner: _Spinner | None = _Spinner() if is_tty else None
    error_seen: list[str] = []

    def on_frame(frame: dict) -> None:
        ftype = frame.get("type")
        stage = frame.get("stage", "")

        if ftype == "progress":
            if stage == "extracting":
                if spinner:
                    spinner.start("Extracting...")
                # no output line for extracting — spinner alone is enough
            elif stage == "concepts_found":
                count = frame["count"]
                if spinner:
                    spinner.update(f"Found {count} concept(s) — writing pages...")
                click.echo(f"[PROGRESS] concepts_found: {count}")
            elif stage == "concept_done":
                name = frame["name"]
                action = frame["action"]
                line = f"[DONE] {name} ({action})"
                if spinner:
                    spinner.print_line(line)
                else:
                    click.echo(line)

        elif ftype == "done":
            if spinner:
                spinner.stop()
            created = frame.get("pages_created", 0)
            updated = frame.get("pages_updated", 0)
            click.echo(f"[SUMMARY] {created} created, {updated} updated")
            if frame.get("warnings"):
                for w in frame["warnings"]:
                    click.echo(f"  Warning: {w['message']}")

        elif ftype == "error":
            if spinner:
                spinner.stop()
            msg_text = frame.get("message", "Unknown error")
            written = frame.get("concepts_written", 0)
            error_seen.append(f"{msg_text} ({written} concept(s) written before error)")

    client.stream_ingest_sync(msg, on_frame)

    if error_seen:
        raise click.ClickException(error_seen[0])
```

- [ ] **Step 5: Run the new test — expect PASS**

```bash
pytest tests/test_cli/test_commands.py::test_ingest_streaming_output -v
```
Expected: PASS

- [ ] **Step 6: Run the full CLI test suite — expect no regressions**

```bash
pytest tests/test_cli/ -v
```
Expected: all PASS

- [ ] **Step 7: Run the full test suite**

```bash
pytest -x -q
```
Expected: all PASS. If any failure, fix before committing.

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_commands.py
git commit -m "feat: streaming progress display for ingest CLI"
```

---

## Verification

After all tasks complete:

```bash
# Full suite green
pytest -q

# Manual smoke — dry-run should be fast (1 LLM call)
llm-wiki ingest --dry-run boltz2.pdf

# Manual smoke — live ingest should show spinner + [DONE] lines
llm-wiki ingest boltz2.pdf

# Pipe test — output should be clean lines, no escape codes
llm-wiki ingest boltz2.pdf | grep DONE
```
