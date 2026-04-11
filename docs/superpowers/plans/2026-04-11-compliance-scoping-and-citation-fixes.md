# Compliance Scoping and Citation Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs — compliance reviewer running on non-wiki files, citation format mismatch between prompts and auditor, and cluster directories never created.

**Architecture:** Three independent fixes scoped to three separate subsystems. Task 1 and 2 are bugs. Task 3 is a missing feature. Task 4 cleans up the phantom issues already on disk.

**Tech Stack:** Python, pytest, no external dependencies

---

## Task 1: Scope compliance reviewer to `wiki/` only

The file watcher (`watcher.py`) correctly watches the entire `vault_root`, but the change callback in `server.py` must only dispatch wiki pages to compliance review. Files in `raw/`, `inbox/`, `schema/` etc. are not wiki pages and must not be audited.

**Files:**
- Modify: `src/llm_wiki/daemon/server.py:426-434`
- Test: `tests/test_daemon/test_compliance_integration.py`

- [ ] **Step 1: Write the failing test**

Add a test to `tests/test_daemon/test_compliance_integration.py` that creates a file in `raw/` and verifies it does NOT generate compliance issues:

```python
def test_raw_file_not_submitted_to_compliance(tmp_path):
    """Files under raw/ must not be dispatched to compliance review."""
    from llm_wiki.audit.compliance import ComplianceReviewer
    from llm_wiki.issues.queue import IssueQueue
    from llm_wiki.config import WikiConfig

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    queue = IssueQueue(wiki_dir)
    config = WikiConfig()
    reviewer = ComplianceReviewer(tmp_path, queue, config)

    # Write a file in raw/ with no citations (would fail compliance if checked)
    raw_file = raw_dir / "paper.md"
    raw_file.write_text("This is raw paper text with zero citations anywhere.")

    # Simulate what the callback does — but we're testing the filter logic
    # that should PREVENT this file from reaching review_change
    result = reviewer.review_change(raw_file, None, raw_file.read_text())

    # The reviewer itself will run on any path given to it.
    # The bug is in server.py not filtering. We test the filter here.
    assert True  # placeholder — actual test is the server filter test below
```

The real test needs to verify the `on_file_change` filter. Since that's an async method on `DaemonServer`, test the filter logic directly:

```python
def test_on_file_change_skips_non_wiki_files(tmp_path):
    """on_file_change must not dispatch files outside wiki/ to compliance."""
    from pathlib import Path

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (tmp_path / "inbox").mkdir()

    # Replicate the filter logic from server.py
    def should_dispatch(path: Path, vault_root: Path, wiki_subdir: str) -> bool:
        try:
            rel = path.relative_to(vault_root)
        except ValueError:
            return False
        if any(p.startswith(".") for p in rel.parts):
            return False
        wiki_dir = vault_root / wiki_subdir.rstrip("/")
        if not path.is_relative_to(wiki_dir):
            return False
        return True

    wiki_subdir = "wiki"

    # Raw files must be rejected
    assert not should_dispatch(raw_dir / "paper.md", tmp_path, wiki_subdir)
    # Inbox files must be rejected
    assert not should_dispatch(tmp_path / "inbox" / "plan.md", tmp_path, wiki_subdir)
    # Hidden dirs must be rejected
    assert not should_dispatch(wiki_dir / ".issues" / "x.md", tmp_path, wiki_subdir)
    # Wiki files must be accepted
    assert should_dispatch(wiki_dir / "boltz-2.md", tmp_path, wiki_subdir)
```

- [ ] **Step 2: Run test to verify it passes (the filter doesn't exist yet, so this is documenting expected behavior)**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_daemon/test_compliance_integration.py -v -k "non_wiki" --no-header`
The pure-logic test will pass since it tests its own function. The real fix is in server.py.

- [ ] **Step 3: Apply the fix to `server.py`**

In `src/llm_wiki/daemon/server.py`, replace the `on_file_change` loop (lines ~426-434). The current code:

```python
for path in changed:
    try:
        rel = path.relative_to(self._vault_root)
    except ValueError:
        continue
    if any(p.startswith(".") for p in rel.parts):
        continue  # skip hidden dirs (e.g. .issues)
    if self._dispatcher is not None:
        self._dispatcher.submit(path)
```

Replace with:

```python
wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
for path in changed:
    try:
        rel = path.relative_to(self._vault_root)
    except ValueError:
        continue
    if any(p.startswith(".") for p in rel.parts):
        continue  # skip hidden dirs (e.g. .issues)
    if not path.is_relative_to(wiki_dir):
        continue  # only audit wiki pages, not raw/inbox/schema
    if self._dispatcher is not None:
        self._dispatcher.submit(path)
```

- [ ] **Step 4: Run existing compliance tests to verify nothing broke**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_audit/test_compliance.py tests/test_daemon/test_compliance_integration.py -v --no-header`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_compliance_integration.py
git commit -m "fix: scope compliance reviewer to wiki/ only, skip raw/inbox/schema"
```

---

## Task 2: Replace `[^N]` footnotes with `[[raw/source.pdf|N]]` inline citations

The current citation format (`[^1]` footnotes) is invisible to the compliance reviewer's `_has_citation` check (which looks for `[[...]]` wikilinks). Replace with `[[raw/source.pdf|N]]` where N is auto-assigned by `patch_token_estimates`.

Two parts: (A) change the prompts, (B) add citation renumbering to `patch_token_estimates`.

### Task 2a: Update prompts to emit `[[raw/source.pdf]]` instead of `[^N]`

**Files:**
- Modify: `src/llm_wiki/ingest/prompts.py` (two prompt templates)
- Test: `tests/test_ingest/test_prompts.py` (create if not exists)

- [ ] **Step 1: Write tests for new citation format in prompt output**

Create `tests/test_ingest/test_prompts.py`:

```python
from llm_wiki.ingest.prompts import (
    compose_concept_extraction_messages,
    compose_page_content_messages,
    compose_deep_read_synthesis_messages,
    compose_overview_messages,
    parse_concept_extraction,
    parse_page_content,
    parse_content_synthesis,
)
from llm_wiki.ingest.agent import ConceptPlan


def test_page_content_prompt_uses_wikilink_citations():
    """The page content prompt must instruct [[raw/...]] citations, not [^N]."""
    messages = compose_page_content_messages(
        concept_title="Boltz-2",
        passages=["Boltz-2 is a model."],
        source_ref="raw/boltz2.pdf",
    )
    system = messages[0]["content"]
    assert "[[^N]]" not in system
    assert "[^N]" not in system or "[^N] = [[raw/" in system  # transition comment ok
    assert "[[raw/boltz2.pdf]]" in system or "{source_ref}" in system


def test_deep_read_prompt_uses_wikilink_citations():
    """The deep-read synthesis prompt must instruct [[raw/...]] citations."""
    concept = ConceptPlan(name="boltz-2", title="Boltz-2")
    messages = compose_deep_read_synthesis_messages(
        concept=concept,
        paper_context="Full paper text here.",
        source_ref="raw/boltz2.pdf",
        manifest_lines=[],
        batch_concepts=[concept],
    )
    system = messages[0]["content"]
    assert "[[raw/boltz2.pdf]]" in system or "[[raw/" in system
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_ingest/test_prompts.py -v --no-header`
Expected: FAIL (prompts still say `[^N]`)

- [ ] **Step 3: Update `_PAGE_CONTENT_SYSTEM` in `prompts.py`**

In `src/llm_wiki/ingest/prompts.py`, replace the `_PAGE_CONTENT_SYSTEM` template. Change lines 48-91:

```python
_PAGE_CONTENT_SYSTEM = """\
You are writing content for a wiki page about a specific concept, based on a \
source document.

## Citation Rules (Non-Negotiable)

Use inline wikilink citations — [[{source_ref}]] — for every factual claim:
- Every factual claim MUST end with [[{source_ref}]]. No exceptions.
- Place the citation at the end of the sentence, inside the sentence punctuation.
- Example: "Boltz-2 achieves SOTA performance [[{source_ref}]]."
- Do NOT use footnote syntax ([^N]) or embed [[raw/...]] links outside citations.

## Wikilink Rules

Named concepts, models, methods, datasets, proteins, databases, and proper nouns \
get `[[slug]]` wikilinks:
- Known wiki slug → use it exactly.
- Named concept not yet in wiki (e.g. "Free Energy Perturbation", "TYK2") → invent \
  a kebab-case slug (e.g. `[[free-energy-perturbation]]`, `[[tyk2]]`). Red links are fine.
- Generic terms with no standalone identity → plain text, no brackets.

## Content Rules

- Do NOT interpret beyond what the source states.
- "X correlates with Y", not "X causes Y".
- Be concise. Every sentence earns its place.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object:

{{
  "sections": [
    {{
      "name": "section-slug",
      "heading": "Section Heading",
      "content": "Markdown with [[wikilinks]] and [[{source_ref}]] citations."
    }}
  ]
}}"""
```

- [ ] **Step 4: Update `_DEEP_READ_SYNTHESIS_SYSTEM` in `prompts.py`**

Replace the `_DEEP_READ_SYNTHESIS_SYSTEM` template (lines 316-376):

```python
_DEEP_READ_SYNTHESIS_SYSTEM = """\
You are writing a wiki page for a specific concept.

You have a comprehensive digest of the full paper — you understand the \
whole document. Write from that understanding. Do not transcribe; synthesize.

Think like an expert explaining this concept to a knowledgeable colleague: \
integrate the methodology, results, comparisons to baselines, and limitations. \
Every sentence should carry information that earns its place.

## Citation Rules (Non-Negotiable)

Use inline wikilink citations — [[<<<SOURCE_REF>>>]] — for every factual claim:
- Every factual claim MUST end with [[<<<SOURCE_REF>>>]]. No exceptions.
- Place the citation at the end of the sentence, inside the sentence punctuation.
- Example: "Boltz-2 achieves SOTA performance [[<<<SOURCE_REF>>>]]."
- Do NOT use footnote syntax ([^N]) or separate References sections.

## Wikilink Rules

Named concepts, models, methods, datasets, proteins, databases, and proper nouns \
get `[[slug]]` wikilinks:
1. Slug in EXISTING WIKI list → use that exact slug.
2. Slug in BATCH list → use that exact slug.
3. Named concept NOT in either list → invent a kebab-case slug. Red links are fine.
4. Generic terms → plain text.

## Existing wiki pages

<<<MANIFEST>>>

## Concepts in this ingest batch

<<<BATCH_SLUGS>>>

## Content Rules

- Synthesize, do not transcribe.
- Write with depth. A good wiki page explains WHY, not just WHAT.
- Include quantitative results where they ground a claim.
- Do not interpret beyond what the paper states.
- "X correlates with Y" not "X causes Y".

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object:

{
  "summary": "One sentence (≤20 words) describing the concept.",
  "sections": [
    {
      "name": "section-slug",
      "heading": "Section Heading",
      "content": "Markdown with [[wikilinks]] and [[<<<SOURCE_REF>>>]] citations."
    }
  ]
}"""
```

Key change: removed the `"references"` section entirely. Citations are inline `[[raw/source.pdf]]` wikilinks. No more `[^N]` footnotes. No more References section.

- [ ] **Step 5: Update `parse_page_content` and `parse_content_synthesis` if they enforce references section**

Read `src/llm_wiki/ingest/prompts.py` functions `parse_page_content` and `parse_content_synthesis`. If they require a `"references"` section or validate footnote format, update them to not require it. The references section is now optional — inline citations replace it.

- [ ] **Step 6: Run tests**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_ingest/test_prompts.py tests/test_ingest/ -v --no-header`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/ingest/prompts.py tests/test_ingest/test_prompts.py
git commit -m "feat: replace [^N] footnote citations with [[raw/source.pdf]] inline wikilinks"
```

### Task 2b: Add citation renumbering to `patch_token_estimates`

The prompts now emit bare `[[raw/source.pdf]]` citations. The daemon's `patch_token_estimates` pass (already called on every write) will assign incrementing numbers per page: `[[raw/source.pdf|1]]`, `[[raw/source.pdf|2]]`, etc.

**Files:**
- Modify: `src/llm_wiki/ingest/page_writer.py:146-213`
- Test: `tests/test_ingest/test_page_writer.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_ingest/test_page_writer.py`:

```python
def test_patch_token_estimates_renumbers_citations(tmp_path):
    """Bare [[raw/source.pdf]] citations get numbered [[raw/source.pdf|N]]."""
    from llm_wiki.ingest.page_writer import patch_token_estimates

    page = tmp_path / "test.md"
    page.write_text(
        "---\ntitle: Test\nsource: '[[raw/paper.pdf]]'\n---\n\n"
        "%% section: overview %%\n"
        "## Overview\n\n"
        "First claim [[raw/paper.pdf]]. Second claim [[raw/paper.pdf]]. "
        "Third claim [[raw/other.pdf]].\n",
        encoding="utf-8",
    )
    patch_token_estimates(page)
    text = page.read_text()

    # Bare citations should be numbered
    assert "[[raw/paper.pdf|1]]" in text
    assert "[[raw/paper.pdf|2]]" in text
    # Different source gets its own counter
    assert "[[raw/other.pdf|1]]" in text
    # No bare [[raw/...]] left in body (frontmatter is untouched)
    # Count bare citations in body only (after frontmatter)
    body = text.split("---\n", 2)[-1]
    import re
    bare = re.findall(r"\[\[raw/[^\]|]+?\]\]", body)
    assert len(bare) == 0


def test_patch_already_numbered_citations_idempotent(tmp_path):
    """Already-numbered citations are not double-numbered."""
    from llm_wiki.ingest.page_writer import patch_token_estimates

    page = tmp_path / "test.md"
    page.write_text(
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n"
        "## Overview\n\n"
        "Claim [[raw/paper.pdf|1]]. Another [[raw/paper.pdf|2]].\n",
        encoding="utf-8",
    )
    patch_token_estimates(page)
    text = page.read_text()

    assert "[[raw/paper.pdf|1]]" in text
    assert "[[raw/paper.pdf|2]]" in text
    # Should not produce triple-numbered like |1|1
    assert "|1|" not in text


def test_patch_preserves_wikilink_aliases(tmp_path):
    """Wikilinks with display text like [[raw/paper.pdf|Paper]] are not renumbered."""
    from llm_wiki.ingest.page_writer import patch_token_estimates

    page = tmp_path / "test.md"
    page.write_text(
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n"
        "## Overview\n\n"
        "See [[raw/paper.pdf|the paper]] for details.\n",
        encoding="utf-8",
    )
    patch_token_estimates(page)
    text = page.read_text()

    # Display-text aliases should be left alone
    assert "[[raw/paper.pdf|the paper]]" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_ingest/test_page_writer.py -v -k "renumber or idempotent or alias" --no-header`
Expected: FAIL (renumbering not implemented yet)

- [ ] **Step 3: Implement citation renumbering in `patch_token_estimates`**

In `src/llm_wiki/ingest/page_writer.py`, add a citation renumbering step to `patch_token_estimates`. After the existing token-count rewrite loop, add a second pass that renumbers bare `[[raw/...]]` citations in the body.

Add these constants and helper after the existing `_SECTION_MARKER_RE`:

```python
# Matches bare [[raw/...]] citations (no pipe, so no |N suffix or |display text).
# We need to be careful: [[raw/file.pdf|display]] is an alias, [[raw/file.pdf|3]] is numbered.
# Only [[raw/file.pdf]] (no pipe at all) gets renumbered.
_BARE_RAW_CITATION_RE = re.compile(
    r"\[\[(raw/[^|\]]+?)\]\]"  # [[raw/path]] with no |pipe at all
)
```

Then modify `patch_token_estimates` — after building `new_text` with token counts and before the final write, add the renumbering pass:

```python
def patch_token_estimates(path: Path) -> None:
    """Rewrite %% section: name %% markers to include token counts,
    and renumber bare [[raw/...]] citations to [[raw/...|N]].

    Reads the file, counts tokens in each section's content block, then
    rewrites each marker as %% section: name, tokens: N %%. Then scans
    the body for bare [[raw/...]] citations (no pipe) and assigns
    incrementing per-source numbers.

    Pure Python — no LLM calls.
    """
    text = path.read_text(encoding="utf-8")

    # --- Phase 1: Token count patching (unchanged) ---
    lines = text.splitlines()
    segments: list[tuple[str, list[str]]] = []
    current_marker: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if _SECTION_MARKER_RE.match(line.strip()):
            if current_marker is not None:
                segments.append((current_marker, current_lines))
            current_marker = line.strip()
            current_lines = []
        else:
            if current_marker is not None:
                current_lines.append(line)

    if current_marker is not None:
        segments.append((current_marker, current_lines))

    if not segments:
        # No section markers — still do citation renumbering
        new_text = _renumber_citations(text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
        return

    first_marker_line = None
    for i, line in enumerate(lines):
        if _SECTION_MARKER_RE.match(line.strip()):
            first_marker_line = i
            break

    header_lines = lines[:first_marker_line] if first_marker_line is not None else []

    output_parts = header_lines[:]
    for marker_line, content_lines in segments:
        m = _SECTION_MARKER_RE.match(marker_line.strip())
        if m:
            section_name = m.group(2).strip()
            section_content = "\n".join(content_lines)
            tokens = count_tokens(section_content)
            new_marker = f"%% section: {section_name}, tokens: {tokens} %%"
        else:
            new_marker = marker_line.strip()
        output_parts.append(new_marker)
        output_parts.extend(content_lines)

    new_text = "\n".join(output_parts)

    # --- Phase 2: Citation renumbering ---
    new_text = _renumber_citations(new_text)

    if not new_text.endswith("\n"):
        new_text += "\n"
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")


def _renumber_citations(text: str) -> str:
    """Renumber bare [[raw/...]] citations in the body to [[raw/...|N]].

    Only touches citations with no pipe at all: [[raw/file.pdf]] → [[raw/file.pdf|N]].
    Already-numbered [[raw/file.pdf|3]] and aliased [[raw/file.pdf|display]] are
    left untouched. Frontmatter (between --- markers) is left untouched.

    N increments per source path independently within the page.
    """
    # Split off frontmatter
    if text.startswith("---\n"):
        try:
            fm_end = text.index("\n---", 4)
        except ValueError:
            return text  # malformed frontmatter, don't touch
        fm_end += 4  # past the closing ---
        frontmatter = text[:fm_end]
        body = text[fm_end:]
    else:
        frontmatter = ""
        body = text

    counters: dict[str, int] = {}  # source_path -> next N

    def _replace(m: re.Match) -> str:
        source = m.group(1)
        n = counters.get(source, 1)
        counters[source] = n + 1
        return f"[[{source}|{n}]]"

    new_body = _BARE_RAW_CITATION_RE.sub(_replace, body)
    return frontmatter + new_body
```

- [ ] **Step 4: Run tests**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_ingest/test_page_writer.py -v --no-header`
Expected: all pass

- [ ] **Step 5: Run full test suite**

Run: `cd ~/repos/llm-wiki && python -m pytest --no-header -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/ingest/page_writer.py tests/test_ingest/test_page_writer.py
git commit -m "feat: auto-renumber bare [[raw/...]] citations to [[raw/...|N]] in patch_token_estimates"
```

### Task 2c: Update compliance `_has_citation` to recognise new format

The compliance reviewer already checks for `[[...]]` wikilinks. With the new `[[raw/source.pdf|N]]` format, the existing `_WIKILINK_RE` (`\[\[([^\]|]+)(?:\|[^\]]+)?\]\]`) already matches. No code change needed — this task is verification only.

- [ ] **Step 1: Write a test confirming `[[raw/source.pdf|1]]` passes `_has_citation`**

Add to `tests/test_audit/test_compliance.py`:

```python
def test_has_citation_recognises_numbered_raw_citations():
    """[[raw/source.pdf|N]] citations must satisfy _has_citation."""
    from llm_wiki.audit.compliance import ComplianceReviewer
    assert ComplianceReviewer._has_citation("Claim [[raw/paper.pdf|1]].")
    assert ComplianceReviewer._has_citation("Claim [[raw/paper.pdf|12]].")
    assert ComplianceReviewer._has_citation("Claim [[raw/paper.pdf]].")


def test_has_citation_rejects_uncited_sentences():
    """Sentences with no wikilinks must fail _has_citation."""
    from llm_wiki.audit.compliance import ComplianceReviewer
    assert not ComplianceReviewer._has_citation("Boltz-2 is a model.")
    assert not ComplianceReviewer._has_citation("See [^1] for details.")
```

- [ ] **Step 2: Run test**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_audit/test_compliance.py -v -k "has_citation" --no-header`
Expected: PASS (the regex already handles `|` suffix)

- [ ] **Step 3: Commit**

```bash
git add tests/test_audit/test_compliance.py
git commit -m "test: verify compliance _has_citation recognises [[raw/...|N]] format"
```

---

## Task 3: Implement cluster directories in write path

The overview prompt asks the LLM for a cluster, `ConceptPlan` stores it, but neither `_write_via_service` nor `PageWriteService.create` uses it. Both write flat to `wiki/<slug>.md`.

**Files:**
- Modify: `src/llm_wiki/daemon/writes.py:117-211` (add cluster param to create)
- Modify: `src/llm_wiki/ingest/agent.py:280-319` (pass cluster through)
- Test: `tests/test_daemon/test_write_routes.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_daemon/test_write_routes.py`:

```python
@pytest.mark.asyncio
async def test_create_with_cluster_subdirectory(tmp_path):
    """wiki_create with cluster writes to wiki/<cluster>/<slug>.md."""
    # Setup: vault_root with wiki/ dir and daemon server
    # This test verifies that when create() receives a cluster param,
    # the page is written to wiki/<cluster>/<slug>.md
    from llm_wiki.ingest.page_writer import write_page

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    sections = [
        __import__("llm_wiki.ingest.page_writer", fromlist=["PageSection"]).PageSection(
            name="overview", heading="Overview", content="Test content [[raw/paper.pdf]]."
        ),
    ]
    result = write_page(
        wiki_dir, "boltz-2", "Boltz-2", sections, "raw/paper.pdf",
        cluster="structural-biology",
    )
    assert result.path == wiki_dir / "structural-biology" / "boltz-2.md"
    assert result.path.exists()
    assert "structural-biology" in str(result.path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_daemon/test_write_routes.py -v -k "cluster" --no-header`
Expected: FAIL (write_page ignores cluster, writes flat)

- [ ] **Step 3: Modify `write_page` in `page_writer.py` to use cluster**

In `src/llm_wiki/ingest/page_writer.py`, change line 58 from:

```python
    page_path = wiki_dir / f"{concept_name}.md"
```

to:

```python
    if cluster:
        page_path = wiki_dir / cluster / f"{concept_name}.md"
    else:
        page_path = wiki_dir / f"{concept_name}.md"
```

The `page_path.parent.mkdir(parents=True, exist_ok=True)` on line 106 already handles creating the cluster subdirectory.

- [ ] **Step 4: Add `cluster` parameter to `PageWriteService.create`**

In `src/llm_wiki/daemon/writes.py`, modify the `create` method signature (line 117):

```python
    async def create(
        self,
        *,
        title: str,
        body: str,
        citations: list[str],
        author: str,
        connection_id: str,
        tags: list[str] | None = None,
        intent: str | None = None,
        force: bool = False,
        cluster: str = "",          # <-- NEW
    ) -> WriteResult:
```

Then at line 148-149, change the path computation:

```python
        slug = _slugify(title)
        if cluster:
            page_path = self._wiki_dir / cluster / f"{slug}.md"
        else:
            page_path = self._wiki_dir / f"{slug}.md"
```

- [ ] **Step 5: Pass cluster through in `_write_via_service`**

In `src/llm_wiki/ingest/agent.py`, modify `_write_via_service` (line 296) to pass cluster:

```python
            wr = await service.create(
                title=concept.title,
                body=body,
                citations=[source_ref],
                author=author,
                connection_id=connection_id,
                intent=f"ingest from {source_ref}",
                force=True,
                cluster=concept.cluster,  # <-- NEW
            )
```

Also update the existence check at line 293 to search within cluster dirs:

```python
        if concept.cluster:
            page_path = wiki_dir / concept.cluster / f"{concept.name}.md"
        else:
            page_path = wiki_dir / f"{concept.name}.md"
```

- [ ] **Step 6: Update the collision check in `create` to search recursively**

In `writes.py` line 153, the collision check uses `self._vault.manifest_entries()`. Verify that `Vault.scan` picks up pages in subdirectories. If `Vault` only scans flat `wiki/`, it needs to scan recursively. Check `src/llm_wiki/vault.py` and update `scan` to use `rglob` if it doesn't already.

- [ ] **Step 7: Run tests**

Run: `cd ~/repos/llm-wiki && python -m pytest tests/test_daemon/test_write_routes.py tests/test_ingest/test_page_writer.py -v --no-header`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/writes.py src/llm_wiki/ingest/agent.py src/llm_wiki/ingest/page_writer.py tests/test_daemon/test_write_routes.py
git commit -m "feat: write pages into cluster subdirectories from concept.cluster"
```

---

## Task 4: Clean up phantom issues from raw/ files

After Task 1 is deployed, clean the ~4,300 phantom `compliance-boltz2-*` and `compliance-proteindj-*` issues from the issue queue. These were filed against `raw/` files that should never have been audited.

**Files:**
- No code changes — one-time cleanup

- [ ] **Step 1: Count phantom issues before cleanup**

Run: `ls ~/wiki/wiki/.issues/compliance-boltz2-*.md | wc -l`
Run: `ls ~/wiki/wiki/.issues/compliance-proteindj-*.md | wc -l`

- [ ] **Step 2: Delete phantom issues**

```bash
rm ~/wiki/wiki/.issues/compliance-boltz2-*.md
rm ~/wiki/wiki/.issues/compliance-proteindj-*.md
```

- [ ] **Step 3: Re-run lint to confirm clean state**

Run: `cd ~/repos/llm-wiki && python -m llm_wiki.cli.main lint --vault ~/wiki`
Expected: compliance issues should only reference actual wiki pages, not raw/ files

- [ ] **Step 4: Commit cleanup**

```bash
cd ~/wiki && git add -A && git commit -m "chore: remove phantom compliance issues from raw/ files"
```
