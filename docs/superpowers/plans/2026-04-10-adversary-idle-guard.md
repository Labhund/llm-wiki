# Adversary Idle Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the adversary background worker from making LLM calls when the vault has not changed since the last run, eliminating idle spend on stable vaults.

**Architecture:** Three changes in sequence: (1) add config field for force-recheck window; (2) thread `vault.raw_dir` through claim extraction so hardcoded `"raw/"` strings are replaced by the configured prefix; (3) add a vault mtime guard to `AdversaryAgent.run()` that returns early when nothing has changed, writing a timestamp file after each real run.

**Tech Stack:** Python stdlib (`time`, `os`, `tempfile`), existing `MaintenanceConfig` / `VaultConfig` dataclasses, pytest + pytest-asyncio.

**Worktree:** `.worktrees/feat-adversary-idle-guard` on branch `feat/adversary-idle-guard`

---

### Task 1: Add `adversary_force_recheck_days` to `MaintenanceConfig`

**Files:**
- Modify: `src/llm_wiki/config.py` (MaintenanceConfig dataclass, ~line 122)
- Test: `tests/test_config.py` (create if it doesn't exist, otherwise append)

- [ ] **Step 1: Write the failing test**

`tests/test_config.py` already exists. First, amend its existing import line (currently `from llm_wiki.config import WikiConfig, IngestConfig`) to also include `MaintenanceConfig`:

```python
from llm_wiki.config import WikiConfig, IngestConfig, MaintenanceConfig
```

Then append these three test functions to the bottom of the file:

```python
def test_adversary_force_recheck_days_default():
    config = MaintenanceConfig()
    assert config.adversary_force_recheck_days == 30


def test_adversary_force_recheck_days_configurable():
    config = MaintenanceConfig(adversary_force_recheck_days=7)
    assert config.adversary_force_recheck_days == 7


def test_wiki_config_inherits_force_recheck_default():
    config = WikiConfig()
    assert config.maintenance.adversary_force_recheck_days == 30
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd .worktrees/feat-adversary-idle-guard
python -m pytest tests/test_config.py::test_adversary_force_recheck_days_default tests/test_config.py::test_adversary_force_recheck_days_configurable tests/test_config.py::test_wiki_config_inherits_force_recheck_default -v
```

Expected: `AttributeError: 'MaintenanceConfig' object has no attribute 'adversary_force_recheck_days'`

- [ ] **Step 3: Add the field to `MaintenanceConfig`**

In `src/llm_wiki/config.py`, add one line inside `MaintenanceConfig` after the existing `synthesis_authority_boost` field:

```python
    # Adversary idle guard — bypass mtime check after this many idle days
    adversary_force_recheck_days: int = 30
```

The full class should end:

```python
    # Synthesis authority boost — multiplier applied to synthesis pages in
    # compute_authority(). >1.0 boosts, <1.0 penalises. 1.0 = no effect.
    synthesis_authority_boost: float = 1.5
    # Adversary idle guard — bypass mtime check after this many idle days
    adversary_force_recheck_days: int = 30
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_config.py::test_adversary_force_recheck_days_default tests/test_config.py::test_adversary_force_recheck_days_configurable tests/test_config.py::test_wiki_config_inherits_force_recheck_default -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/config.py tests/test_config.py
git commit -m "feat(config): add adversary_force_recheck_days to MaintenanceConfig (default 30)"
```

---

### Task 2: Thread `raw_dir` through `extract_claims()`

**Files:**
- Modify: `src/llm_wiki/adversary/claim_extractor.py`
- Test: `tests/test_adversary/test_claim_extractor.py` (append new tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adversary/test_claim_extractor.py`:

```python
def test_extract_claims_custom_raw_dir(tmp_path: Path):
    """extract_claims with a custom raw_dir matches that prefix, not 'raw/'."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The result is positive [[sources/smith-2026.pdf]].\n"
    ))
    # With default raw_dir="raw", should find no claims (prefix mismatch)
    assert extract_claims(page) == []
    # With raw_dir="sources", should find the claim
    claims = extract_claims(page, raw_dir="sources")
    assert len(claims) == 1
    assert claims[0].citation == "sources/smith-2026.pdf"


def test_extract_claims_custom_raw_dir_ignores_default_prefix(tmp_path: Path):
    """When raw_dir is customised, the default 'raw/' prefix is NOT matched."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The result is positive [[raw/smith-2026.pdf]].\n"
    ))
    # Explicitly passing raw_dir="sources" — should NOT match [[raw/...]]
    claims = extract_claims(page, raw_dir="sources")
    assert claims == []


def test_extract_claims_raw_dir_strips_trailing_slash(tmp_path: Path):
    """raw_dir="raw/" (with trailing slash) works identically to "raw"."""
    page = _make_page(tmp_path, (
        "---\ntitle: Test\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The algorithm converges [[raw/jones.md]].\n"
    ))
    claims = extract_claims(page, raw_dir="raw/")
    assert len(claims) == 1
    assert claims[0].citation == "raw/jones.md"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_adversary/test_claim_extractor.py::test_extract_claims_custom_raw_dir tests/test_adversary/test_claim_extractor.py::test_extract_claims_custom_raw_dir_ignores_default_prefix tests/test_adversary/test_claim_extractor.py::test_extract_claims_raw_dir_strips_trailing_slash -v
```

Expected: FAIL — `extract_claims()` does not accept a `raw_dir` argument.

- [ ] **Step 3: Update `extract_claims()` in `claim_extractor.py`**

Replace the module-level constant and function signature:

```python
# Remove this line:
# _TRAILING_RAW_CITATION_RE = re.compile(
#     r"\[\[(raw/[^\]|]+)(?:\|[^\]]+)?\]\]\s*[.!?]?\s*$"
# )


def extract_claims(page: "Page", raw_dir: str = "raw") -> list[Claim]:
    """Extract all verifiable claims from a parsed page.

    A claim is a sentence inside a section body that ends with a
    [[<raw_dir>/...]] citation. Code blocks (``` or ~~~), %% marker lines,
    and headings are excluded. The page's frontmatter `source` field
    is NOT counted as a claim — only body content is.

    Args:
        page: Parsed wiki page.
        raw_dir: The raw sources directory prefix used in citations
            (e.g. "raw" matches [[raw/...]]). Defaults to "raw".
            Pass ``config.vault.raw_dir.rstrip("/")`` at call sites
            that have access to config.
    """
    prefix = re.escape(raw_dir.rstrip("/"))
    citation_re = re.compile(
        rf"\[\[({prefix}/[^\]|]+)(?:\|[^\]]+)?\]\]\s*[.!?]?\s*$"
    )
    claims: list[Claim] = []
    page_slug = page.path.stem

    for section in page.sections:
        sentences = _extract_body_sentences(section.content)
        for sentence in sentences:
            match = citation_re.search(sentence)
            if match is None:
                continue
            citation = match.group(1)
            claims.append(Claim(
                page=page_slug,
                section=section.name,
                text=sentence.strip(),
                citation=citation,
            ))
    return claims
```

- [ ] **Step 4: Run all claim extractor tests**

```bash
python -m pytest tests/test_adversary/test_claim_extractor.py -v
```

Expected: all pass (existing tests still pass because default `raw_dir="raw"` is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/adversary/claim_extractor.py tests/test_adversary/test_claim_extractor.py
git commit -m "feat(adversary): thread raw_dir param through extract_claims(); dynamic regex"
```

---

### Task 3: Thread `raw_dir` through `AdversaryAgent`

**Files:**
- Modify: `src/llm_wiki/adversary/agent.py`
- Test: `tests/test_adversary/test_agent.py` (append new test)

Three locations to fix in `agent.py`. All are inside `AdversaryAgent.run()`:
- `extract_claims(page)` call → `extract_claims(page, raw_dir=raw_prefix)`
- `raw_dir = self._vault_root / "raw"` (line ~95) → use `raw_prefix`
- `f"raw/{md_file.name}"` and `f"raw/{binary.name}"` (lines ~102, ~106) → use `raw_prefix`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_adversary/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_adversary_respects_configured_raw_dir(tmp_path: Path, _clean_state):
    """When vault.raw_dir is 'sources/', claims citing [[sources/...]] are found
    and the unread-source upweighting scans sources/ not raw/."""
    # Set up vault with sources/ instead of raw/
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    (sources_dir / "smith-2026.md").write_text(
        "# Smith 2026\n\nThe k-means algorithm uses k=10 clusters.\n"
    )
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "k-means.md").write_text(
        "---\ntitle: K-Means\n---\n\n"
        "%% section: method %%\n## Method\n\n"
        "The algorithm uses k=10 clusters [[sources/smith-2026.md]].\n"
    )
    _clean_state.append(_state_dir_for(tmp_path))

    from llm_wiki.config import VaultConfig
    config = WikiConfig(
        maintenance=MaintenanceConfig(adversary_claims_per_run=5),
        vault=VaultConfig(raw_dir="sources/"),
    )
    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.9, "explanation": "Matches."}'
    )
    vault = Vault.scan(tmp_path)
    queue = IssueQueue(tmp_path / "wiki")
    agent = AdversaryAgent(vault, tmp_path, stub, queue, config)

    result = await agent.run()

    # The claim was found and verified (LLM was called)
    assert result.claims_checked == 1
    assert len(result.validated) == 1
    assert len(stub.calls) == 1
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
python -m pytest tests/test_adversary/test_agent.py::test_adversary_respects_configured_raw_dir -v
```

Expected: FAIL — `result.claims_checked == 0` because the agent still looks under `raw/`.

- [ ] **Step 3: Update `AdversaryAgent.run()` in `agent.py`**

Replace the three hardcoded occurrences in `run()`. The updated segment (from after `if not all_claims: return result` down through the unread_sources block) should look like:

```python
    async def run(self) -> AdversaryResult:
        result = AdversaryResult()
        entries = self._vault.manifest_entries()
        if not entries:
            return result

        raw_prefix = self._config.vault.raw_dir.rstrip("/")

        # 1. Extract claims from every non-synthesis page
        all_claims: list[Claim] = []
        for name in entries:
            page = self._vault.read_page(name)
            if page is None:
                continue
            if page.frontmatter.get("type") == "synthesis":
                continue  # resonance agent handles synthesis pages; adversary skips them
            all_claims.extend(extract_claims(page, raw_dir=raw_prefix))

        if not all_claims:
            return result

        # 2. Sample
        n = self._config.maintenance.adversary_claims_per_run
        now = datetime.datetime.now(datetime.timezone.utc)

        # Build unread sources set for adversary upweighting
        unread_sources: set[str] = set()
        raw_dir = self._vault_root / raw_prefix
        if raw_dir.is_dir():
            from llm_wiki.ingest.source_meta import read_frontmatter
            for md_file in raw_dir.glob("*.md"):
                fm = read_frontmatter(md_file)
                if fm.get("reading_status") == "unread":
                    unread_sources.add(f"{raw_prefix}/{md_file.name}")
                    for ext in (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"):
                        binary = md_file.with_suffix(ext)
                        if binary.exists():
                            unread_sources.add(f"{raw_prefix}/{binary.name}")

        sampled = sample_claims(
            all_claims, entries, n=n, rng=self._rng, now=now,
            unread_sources=unread_sources,
            unread_weight=self._config.maintenance.adversary_unread_weight,
        )

        # 3. Verify each
        for claim in sampled:
            await self._process_claim(claim, result, now)

        return result
```

- [ ] **Step 4: Run all adversary agent tests**

```bash
python -m pytest tests/test_adversary/test_agent.py tests/test_adversary/test_integration.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/adversary/agent.py tests/test_adversary/test_agent.py
git commit -m "feat(adversary): thread vault.raw_dir through agent — fix 3 hardcoded 'raw/' strings"
```

---

### Task 4: Vault mtime guard — core logic

**Files:**
- Modify: `src/llm_wiki/adversary/agent.py` (add 3 private methods, update imports)
- Test: `tests/test_adversary/test_agent.py` (append unit tests for guard methods)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adversary/test_agent.py`:

```python
def _make_agent(tmp_path: Path, *, force_recheck_days: int = 30) -> AdversaryAgent:
    """Helper: agent on a vault with one wiki page, no raw sources."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(exist_ok=True)
    (wiki_dir / "page.md").write_text("---\ntitle: Page\n---\n\nContent.\n")
    config = WikiConfig(
        maintenance=MaintenanceConfig(
            adversary_claims_per_run=5,
            adversary_force_recheck_days=force_recheck_days,
        ),
    )
    vault = Vault.scan(tmp_path)
    stub = _StubLLM('{"verdict": "validated", "confidence": 0.9, "explanation": "x"}')
    return AdversaryAgent(vault, tmp_path, stub, IssueQueue(wiki_dir), config)


def test_vault_unchanged_no_ts_file(tmp_path: Path):
    """Returns False (run the adversary) when no timestamp file exists."""
    agent = _make_agent(tmp_path)
    assert agent._vault_unchanged_since_last_run() is False


def test_vault_unchanged_file_modified_after_ts(tmp_path: Path):
    """Returns False when a wiki file is newer than the stored timestamp."""
    import time as _time
    agent = _make_agent(tmp_path)
    # Write a timestamp from 60 seconds ago
    ts = _time.time() - 60
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    (agent._state_dir / "adversary_last_run.txt").write_text(str(ts))
    # Touch the wiki page to set its mtime to now
    page = tmp_path / "wiki" / "page.md"
    page.touch()
    assert agent._vault_unchanged_since_last_run() is False


def test_vault_unchanged_no_new_files(tmp_path: Path):
    """Returns True when no file is newer than the stored timestamp."""
    import time as _time
    agent = _make_agent(tmp_path)
    # Write page first, then store a timestamp that is newer than the page
    page = tmp_path / "wiki" / "page.md"
    page.touch()
    _time.sleep(0.05)  # ensure mtime < ts
    ts = _time.time()
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    (agent._state_dir / "adversary_last_run.txt").write_text(str(ts))
    assert agent._vault_unchanged_since_last_run() is True


def test_vault_unchanged_force_recheck_bypasses_guard(tmp_path: Path):
    """Returns False when force_recheck_days have elapsed, even with no file changes."""
    import time as _time
    agent = _make_agent(tmp_path, force_recheck_days=1)
    # Timestamp is 2 days ago — force-recheck window exceeded
    ts = _time.time() - (2 * 86400)
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    (agent._state_dir / "adversary_last_run.txt").write_text(str(ts))
    assert agent._vault_unchanged_since_last_run() is False


def test_record_last_run_ts_roundtrip(tmp_path: Path):
    """_record_last_run_ts() writes a float that _load_last_run_ts() reads back."""
    import time as _time
    agent = _make_agent(tmp_path)
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    before = _time.time()
    agent._record_last_run_ts()
    after = _time.time()
    ts = agent._load_last_run_ts()
    assert ts is not None
    assert before <= ts <= after


def test_load_last_run_ts_missing_file(tmp_path: Path):
    """Returns None when the timestamp file does not exist."""
    agent = _make_agent(tmp_path)
    assert agent._load_last_run_ts() is None


def test_load_last_run_ts_corrupt_file(tmp_path: Path):
    """Returns None when the timestamp file contains garbage."""
    agent = _make_agent(tmp_path)
    agent._state_dir.mkdir(parents=True, exist_ok=True)
    (agent._state_dir / "adversary_last_run.txt").write_text("not-a-float\n")
    assert agent._load_last_run_ts() is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_adversary/test_agent.py::test_vault_unchanged_no_ts_file tests/test_adversary/test_agent.py::test_vault_unchanged_file_modified_after_ts tests/test_adversary/test_agent.py::test_vault_unchanged_no_new_files tests/test_adversary/test_agent.py::test_vault_unchanged_force_recheck_bypasses_guard tests/test_adversary/test_agent.py::test_record_last_run_ts_roundtrip tests/test_adversary/test_agent.py::test_load_last_run_ts_missing_file tests/test_adversary/test_agent.py::test_load_last_run_ts_corrupt_file -v
```

Expected: `AttributeError` — methods don't exist yet.

- [ ] **Step 3: Add imports and three new methods to `AdversaryAgent`**

At the top of `agent.py`, add to the stdlib imports:

```python
import os
import tempfile
import time
```

Add these three methods to `AdversaryAgent` (after `__init__`, before `run`):

```python
    def _load_last_run_ts(self) -> float | None:
        """Return the stored Unix timestamp of the last adversary run, or None."""
        path = self._state_dir / "adversary_last_run.txt"
        try:
            return float(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return None

    def _record_last_run_ts(self) -> None:
        """Atomically write the current time as the last-run timestamp."""
        path = self._state_dir / "adversary_last_run.txt"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=self._state_dir, prefix=".adversary-ts-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(time.time()))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _vault_unchanged_since_last_run(self) -> bool:
        """Return True if no file in wiki/ or raw/ has changed since the last run.

        Always returns False on the first run (no stored timestamp).
        Also returns False when adversary_force_recheck_days have elapsed since
        the last run — ensuring periodic re-verification even on a static vault.
        Skips hidden files (names starting with '.').
        """
        ts = self._load_last_run_ts()
        if ts is None:
            return False
        force_days = self._config.maintenance.adversary_force_recheck_days
        if (time.time() - ts) > force_days * 86400:
            return False
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        raw_dir  = self._vault_root / self._config.vault.raw_dir.rstrip("/")
        for search_dir in (wiki_dir, raw_dir):
            if not search_dir.exists():
                continue
            for f in search_dir.rglob("*"):
                if f.is_file() and not f.name.startswith(".") and f.stat().st_mtime > ts:
                    return False
        return True
```

- [ ] **Step 4: Run the new tests**

```bash
python -m pytest tests/test_adversary/test_agent.py::test_vault_unchanged_no_ts_file tests/test_adversary/test_agent.py::test_vault_unchanged_file_modified_after_ts tests/test_adversary/test_agent.py::test_vault_unchanged_no_new_files tests/test_adversary/test_agent.py::test_vault_unchanged_force_recheck_bypasses_guard tests/test_adversary/test_agent.py::test_record_last_run_ts_roundtrip tests/test_adversary/test_agent.py::test_load_last_run_ts_missing_file tests/test_adversary/test_agent.py::test_load_last_run_ts_corrupt_file -v
```

Expected: 7 passed.

- [ ] **Step 5: Run full adversary test suite**

```bash
python -m pytest tests/test_adversary/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/adversary/agent.py tests/test_adversary/test_agent.py
git commit -m "feat(adversary): add vault mtime guard methods (_vault_unchanged_since_last_run, _load/_record_last_run_ts)"
```

---

### Task 5: Wire guard into `run()`

**Files:**
- Modify: `src/llm_wiki/adversary/agent.py` (update `run()`)
- Test: `tests/test_adversary/test_agent.py` (append integration-style tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adversary/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_idle_guard_skips_llm_on_stable_vault(tmp_path: Path, _clean_state):
    """Second run on an unchanged vault makes zero LLM calls."""
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.9, "explanation": "Matches."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    # First run: vault is fresh, guard has no timestamp → runs normally
    result1 = await agent.run()
    assert result1.claims_checked == 1
    calls_after_first = len(stub.calls)
    assert calls_after_first == 1

    # Second run: vault unchanged → guard fires → zero new LLM calls
    result2 = await agent.run()
    assert result2.claims_checked == 0
    assert len(stub.calls) == calls_after_first  # no new calls


@pytest.mark.asyncio
async def test_idle_guard_runs_after_wiki_change(tmp_path: Path, _clean_state):
    """Guard does not fire after a wiki file is touched."""
    import time as _time
    vault_root, page_path = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.9, "explanation": "Matches."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    # First run
    await agent.run()
    calls_after_first = len(stub.calls)

    # Simulate wiki change: touch the page so its mtime is after the stored ts
    _time.sleep(0.05)
    page_path.touch()

    # Second run: wiki file changed → guard does not fire → LLM called again
    result2 = await agent.run()
    assert result2.claims_checked == 1
    assert len(stub.calls) == calls_after_first + 1
```

Also append these three tests covering the cases where timestamp recording is critical:

```python
@pytest.mark.asyncio
async def test_idle_guard_force_recheck_bypasses_stable_vault(tmp_path: Path, _clean_state):
    """Guard is bypassed when force_recheck_days have elapsed, even with no file changes."""
    import time as _time
    vault_root, _ = _build_vault_with_one_claim(tmp_path)
    _clean_state.append(_state_dir_for(vault_root))
    # force_recheck_days=1; timestamp is 2 days old → guard bypassed → LLM called
    config = WikiConfig(maintenance=MaintenanceConfig(
        adversary_claims_per_run=5,
        adversary_force_recheck_days=1,
    ))
    stub = _StubLLM(
        '{"verdict": "validated", "confidence": 0.9, "explanation": "Matches."}'
    )
    vault = Vault.scan(vault_root)
    queue = IssueQueue(vault_root / "wiki")
    agent = AdversaryAgent(vault, vault_root, stub, queue, config)

    # Manually write an old timestamp (2 days ago) so force-recheck fires
    state_dir = _state_dir_for(vault_root)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "adversary_last_run.txt").write_text(str(_time.time() - 2 * 86400))

    result = await agent.run()
    assert result.claims_checked == 1
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_idle_guard_empty_vault_does_not_record_timestamp(tmp_path: Path, _clean_state):
    """Running on an empty vault (no entries) does not write the timestamp file."""
    _clean_state.append(_state_dir_for(tmp_path))
    (tmp_path / "wiki").mkdir()
    vault = Vault.scan(tmp_path)
    config = WikiConfig()
    stub = _StubLLM('{"verdict": "validated", "confidence": 0.9, "explanation": "x"}')
    agent = AdversaryAgent(vault, tmp_path, stub, IssueQueue(tmp_path / "wiki"), config)

    await agent.run()

    ts_file = _state_dir_for(tmp_path) / "adversary_last_run.txt"
    assert not ts_file.exists(), "timestamp must not be written when vault is empty"


@pytest.mark.asyncio
async def test_idle_guard_no_claims_records_timestamp(tmp_path: Path, _clean_state):
    """Running on a vault with pages but no raw citations writes the timestamp
    so the guard fires on subsequent runs (preventing repeated scans)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "page.md").write_text(
        "---\ntitle: Page\n---\n\n%% section: overview %%\n## Overview\n\n"
        "No citations here, just prose.\n"
    )
    _clean_state.append(_state_dir_for(tmp_path))
    config = WikiConfig(maintenance=MaintenanceConfig(adversary_claims_per_run=5))
    stub = _StubLLM('{"verdict": "validated", "confidence": 0.9, "explanation": "x"}')
    vault = Vault.scan(tmp_path)
    agent = AdversaryAgent(vault, tmp_path, stub, IssueQueue(wiki_dir), config)

    result = await agent.run()
    assert result.claims_checked == 0
    assert stub.calls == []  # no LLM calls (no claims)

    ts_file = _state_dir_for(tmp_path) / "adversary_last_run.txt"
    assert ts_file.exists(), "timestamp must be written even when no claims found"
    assert agent._load_last_run_ts() is not None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_adversary/test_agent.py::test_idle_guard_skips_llm_on_stable_vault tests/test_adversary/test_agent.py::test_idle_guard_runs_after_wiki_change tests/test_adversary/test_agent.py::test_idle_guard_force_recheck_bypasses_stable_vault tests/test_adversary/test_agent.py::test_idle_guard_empty_vault_does_not_record_timestamp tests/test_adversary/test_agent.py::test_idle_guard_no_claims_records_timestamp -v
```

Expected: FAIL — second run still calls the LLM (guard not wired in yet).

- [ ] **Step 3: Wire the guard and timestamp recording into `run()`**

Update `AdversaryAgent.run()` to call the guard early and record the timestamp after real work. The full updated `run()` method:

```python
    async def run(self) -> AdversaryResult:
        result = AdversaryResult()
        entries = self._vault.manifest_entries()
        if not entries:
            return result

        if self._vault_unchanged_since_last_run():
            logger.info("Adversary: vault unchanged since last run, skipping")
            return result

        raw_prefix = self._config.vault.raw_dir.rstrip("/")

        # 1. Extract claims from every non-synthesis page
        all_claims: list[Claim] = []
        for name in entries:
            page = self._vault.read_page(name)
            if page is None:
                continue
            if page.frontmatter.get("type") == "synthesis":
                continue  # resonance agent handles synthesis pages; adversary skips them
            all_claims.extend(extract_claims(page, raw_dir=raw_prefix))

        if not all_claims:
            self._record_last_run_ts()
            return result

        # 2. Sample
        n = self._config.maintenance.adversary_claims_per_run
        now = datetime.datetime.now(datetime.timezone.utc)

        # Build unread sources set for adversary upweighting
        unread_sources: set[str] = set()
        raw_dir = self._vault_root / raw_prefix
        if raw_dir.is_dir():
            from llm_wiki.ingest.source_meta import read_frontmatter
            for md_file in raw_dir.glob("*.md"):
                fm = read_frontmatter(md_file)
                if fm.get("reading_status") == "unread":
                    unread_sources.add(f"{raw_prefix}/{md_file.name}")
                    for ext in (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"):
                        binary = md_file.with_suffix(ext)
                        if binary.exists():
                            unread_sources.add(f"{raw_prefix}/{binary.name}")

        sampled = sample_claims(
            all_claims, entries, n=n, rng=self._rng, now=now,
            unread_sources=unread_sources,
            unread_weight=self._config.maintenance.adversary_unread_weight,
        )

        # 3. Verify each
        for claim in sampled:
            await self._process_claim(claim, result, now)

        self._record_last_run_ts()
        return result
```

- [ ] **Step 4: Run the new integration tests**

```bash
python -m pytest tests/test_adversary/test_agent.py::test_idle_guard_skips_llm_on_stable_vault tests/test_adversary/test_agent.py::test_idle_guard_runs_after_wiki_change tests/test_adversary/test_agent.py::test_idle_guard_force_recheck_bypasses_stable_vault tests/test_adversary/test_agent.py::test_idle_guard_empty_vault_does_not_record_timestamp tests/test_adversary/test_agent.py::test_idle_guard_no_claims_records_timestamp -v
```

Expected: 5 passed.

- [ ] **Step 5: Run the full test suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1059+ passed, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/adversary/agent.py tests/test_adversary/test_agent.py
git commit -m "feat(adversary): wire idle guard into run(); record timestamp after each real run"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Vault mtime guard in `AdversaryAgent.run()` | Task 4 + 5 |
| `_load_last_run_ts` / `_record_last_run_ts` | Task 4 |
| `adversary_force_recheck_days` config (default 30) | Task 1 |
| Force-recheck bypasses guard after N idle days | Task 4 (`_vault_unchanged_since_last_run`) |
| `raw_dir` threading in `claim_extractor.py` | Task 2 |
| `raw_dir` threading in `agent.py` (lines 95, 102, 106) | Task 3 |
| `extract_claims` called with `raw_dir=raw_prefix` | Task 3 |
| Atomic write of timestamp file | Task 4 |
| Skip hidden files in mtime scan | Task 4 |
| Record timestamp even when `all_claims` is empty | Task 5 (`test_idle_guard_no_claims_records_timestamp`) |
| Force-recheck integration (end-to-end) | Task 5 (`test_idle_guard_force_recheck_bypasses_stable_vault`) |
| Empty vault does not write timestamp | Task 5 (`test_idle_guard_empty_vault_does_not_record_timestamp`) |

All spec requirements covered. No gaps.
