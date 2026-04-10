# PDF Extraction Pipeline — Configurable `pdf_extractor`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PDF extraction configurable via `config.yaml`, replacing the current liteparse-for-PDFs path with a dispatch to `pdftotext` (default), `local-ocr` (vision model), `marker`, or `nougat`, and surface a quality warning when extracted text appears mangled.

**Architecture:** A new `IngestConfig` dataclass holds the extractor choice and local-OCR endpoint. `extractor.py` gains a config-driven `_extract_pdf()` dispatcher; non-PDF paths (DOCX, images, markdown) continue using liteparse unchanged. A `_detect_quality_issues()` heuristic adds `quality_warning` to `ExtractionResult`. The warning is threaded through `IngestResult` to the daemon's ingest response so the attending skill can flag bad extraction before any pages are written.

**Tech Stack:** Python stdlib (`asyncio`, `subprocess`, `tempfile`, `urllib.request`, `base64`), `unittest.mock` for subprocess/network mocking in tests. No new runtime dependencies — liteparse stays for DOCX/images; `pdftotext` and `pdftoppm` are system binaries assumed present on the user's machine.

---

## File Structure

| File | Change |
|---|---|
| `src/llm_wiki/config.py` | Add `IngestConfig` dataclass; add `ingest: IngestConfig` to `WikiConfig` |
| `src/llm_wiki/ingest/extractor.py` | Config-driven PDF dispatch, quality signal, four extractor functions |
| `src/llm_wiki/ingest/agent.py` | Thread config to `extract_text`; set `result.extraction_warning` |
| `src/llm_wiki/daemon/server.py` | Include extraction warning in ingest response warnings list |
| `tests/test_config.py` | `IngestConfig` defaults + YAML round-trip |
| `tests/test_ingest/test_extractor.py` | Tests for each extractor path + quality signal |

---

### Task 1: `IngestConfig` in `config.py`

**Files:**
- Modify: `src/llm_wiki/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_config.py`:

```python
def test_ingest_config_defaults():
    from llm_wiki.config import WikiConfig
    c = WikiConfig()
    assert c.ingest.pdf_extractor == "pdftotext"
    assert c.ingest.local_ocr_endpoint == "http://localhost:8006/v1"
    assert c.ingest.local_ocr_model == "qianfan-ocr"


def test_ingest_config_loads_from_yaml(tmp_path):
    from llm_wiki.config import WikiConfig
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "ingest:\n"
        "  pdf_extractor: local-ocr\n"
        "  local_ocr_endpoint: http://gpu-box:8006/v1\n"
        "  local_ocr_model: my-ocr-model\n"
    )
    c = WikiConfig.load(cfg_file)
    assert c.ingest.pdf_extractor == "local-ocr"
    assert c.ingest.local_ocr_endpoint == "http://gpu-box:8006/v1"
    assert c.ingest.local_ocr_model == "my-ocr-model"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd /home/labhund/repos/llm-wiki/.worktrees/pdf-extractor
pytest tests/test_config.py -k "ingest_config" -v 2>&1 | head -15
```

Expected: `AttributeError: 'WikiConfig' object has no attribute 'ingest'`

- [ ] **Step 3: Add `IngestConfig` and wire into `WikiConfig`**

In `src/llm_wiki/config.py`, add after `VaultConfig` (before `HonchoConfig`):

```python
@dataclass
class IngestConfig:
    pdf_extractor: str = "pdftotext"              # pdftotext | local-ocr | marker | nougat
    local_ocr_endpoint: str = "http://localhost:8006/v1"
    local_ocr_model: str = "qianfan-ocr"
```

In `WikiConfig`, add after `vault: VaultConfig`:

```python
    ingest: IngestConfig = field(default_factory=IngestConfig)
```

- [ ] **Step 4: Run to confirm they pass**

```bash
pytest tests/test_config.py -k "ingest_config" -v
```

Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/config.py tests/test_config.py
git commit -m "feat: IngestConfig — pdf_extractor, local_ocr_endpoint, local_ocr_model

Agent: subagent-task1"
```

---

### Task 2: `pdftotext` extractor + quality signal

**Files:**
- Modify: `src/llm_wiki/ingest/extractor.py`
- Modify: `tests/test_ingest/test_extractor.py`

This task covers the default path (`pdftotext`) and the quality-signal heuristic. Non-PDF liteparse paths are not touched.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ingest/test_extractor.py`:

```python
from unittest.mock import AsyncMock, patch, MagicMock
from llm_wiki.config import IngestConfig


# ---------------------------------------------------------------------------
# pdftotext extractor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_pdf_pdftotext_success(tmp_path: Path):
    """pdftotext extractor returns subprocess stdout as content."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"Extracted text.\n", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        config = IngestConfig(pdf_extractor="pdftotext")
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    assert result.content == "Extracted text.\n"
    assert result.extraction_method == "pdf"
    assert result.token_count > 0
    # Verify pdftotext was called with the right args
    args = mock_exec.call_args[0]
    assert args[0] == "pdftotext"
    assert str(pdf) in args


@pytest.mark.asyncio
async def test_extract_pdf_pdftotext_failure(tmp_path: Path):
    """pdftotext non-zero exit code becomes a failed ExtractionResult."""
    pdf = tmp_path / "corrupt.pdf"
    pdf.write_bytes(b"not a pdf")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"pdftotext: corrupt.pdf: Couldn't open file"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        config = IngestConfig(pdf_extractor="pdftotext")
        result = await extract_text(pdf, ingest_config=config)

    assert not result.success
    assert "pdftotext" in result.error


@pytest.mark.asyncio
async def test_extract_pdf_no_config_uses_pdftotext(tmp_path: Path):
    """extract_text with no ingest_config defaults to pdftotext."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"text", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await extract_text(pdf)

    args = mock_exec.call_args[0]
    assert args[0] == "pdftotext"
    assert result.success


# ---------------------------------------------------------------------------
# quality signal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quality_signal_on_low_word_line_ratio(tmp_path: Path):
    """Mangled text (many short lines, low word/line ratio) triggers quality_warning."""
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"fake pdf")
    # Simulate extracted text that looks like a broken table: many short lines
    mangled = "\n".join(["x"] * 50)  # 50 single-char lines

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(mangled.encode(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        config = IngestConfig(pdf_extractor="pdftotext")
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    assert result.quality_warning is not None
    assert "word/line" in result.quality_warning or "short" in result.quality_warning.lower()


@pytest.mark.asyncio
async def test_quality_signal_absent_for_good_text(tmp_path: Path):
    """Normal prose extraction does not trigger a quality warning."""
    pdf = tmp_path / "good.pdf"
    pdf.write_bytes(b"fake pdf")
    good_text = (
        "This paper presents a novel approach to attention mechanisms in transformers. "
        "We demonstrate that our method achieves state-of-the-art performance on "
        "several benchmark datasets.\n\n"
        "Section 2 describes the related work. Section 3 details the architecture. "
        "Our experiments in Section 4 show consistent improvements.\n\n"
        "The key insight is that sparse attention patterns reduce quadratic complexity "
        "while preserving the most relevant token interactions.\n"
    )

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(good_text.encode(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        config = IngestConfig(pdf_extractor="pdftotext")
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    assert result.quality_warning is None


@pytest.mark.asyncio
async def test_quality_signal_skipped_on_failed_extraction(tmp_path: Path):
    """Failed extraction does not attempt quality check."""
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"fake pdf")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        config = IngestConfig(pdf_extractor="pdftotext")
        result = await extract_text(pdf, ingest_config=config)

    assert not result.success
    assert result.quality_warning is None
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_ingest/test_extractor.py -k "pdftotext or quality_signal" -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'IngestConfig'` or `AttributeError: 'ExtractionResult' object has no attribute 'quality_warning'`

- [ ] **Step 3: Add `quality_warning` to `ExtractionResult`, `ingest_config` param to `extract_text`, pdftotext extractor, quality signal, and dispatch**

Replace `src/llm_wiki/ingest/extractor.py` entirely with:

```python
from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm_wiki.tokens import count_tokens

if TYPE_CHECKING:
    from llm_wiki.config import IngestConfig


@dataclass
class ExtractionResult:
    """Result of document text extraction."""
    success: bool
    content: str
    extraction_method: str   # "pdf", "docx", "image_ocr", "markdown"
    token_count: int = 0
    error: str | None = None
    quality_warning: str | None = None


_SUPPORTED_BINARY = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".png": "image_ocr",
    ".jpg": "image_ocr",
    ".jpeg": "image_ocr",
    ".gif": "image_ocr",
    ".bmp": "image_ocr",
    ".tiff": "image_ocr",
}


async def extract_text(
    source_path: Path,
    _parser: Any = None,
    ingest_config: "IngestConfig | None" = None,
) -> ExtractionResult:
    """Extract text from a document.

    For PDFs, dispatches based on ingest_config.pdf_extractor (default: pdftotext).
    For DOCX and images, uses liteparse (unchanged).
    For markdown, reads directly.

    _parser: liteparse.LiteParse-compatible object — injection for tests (DOCX/image paths only).
    ingest_config: IngestConfig controlling PDF extractor choice.
    """
    if not source_path.exists():
        return ExtractionResult(
            success=False,
            content="",
            extraction_method="none",
            error=f"No such file: {source_path}",
        )

    suffix = source_path.suffix.lower()

    if suffix in (".md", ".markdown"):
        return _extract_markdown(source_path)

    method = _SUPPORTED_BINARY.get(suffix)
    if method is None:
        return ExtractionResult(
            success=False,
            content="",
            extraction_method="none",
            error=f"Unsupported format: {suffix}",
        )

    if suffix == ".pdf":
        return await _extract_pdf(source_path, ingest_config)

    return await _extract_via_liteparse(source_path, method, _parser)


def _extract_markdown(path: Path) -> ExtractionResult:
    """Read a markdown file directly — no liteparse needed."""
    try:
        content = path.read_text(encoding="utf-8")
        return ExtractionResult(
            success=True,
            content=content,
            extraction_method="markdown",
            token_count=count_tokens(content),
        )
    except (UnicodeDecodeError, OSError) as exc:
        return ExtractionResult(
            success=False,
            content="",
            extraction_method="markdown",
            error=str(exc),
        )


async def _extract_via_liteparse(
    path: Path,
    method: str,
    parser: Any,
) -> ExtractionResult:
    """Call liteparse (or test fake) to extract text from binary docs (DOCX, images)."""
    if parser is None:
        from liteparse import LiteParse
        parser = LiteParse()

    try:
        result = await parser.parse_async(path)
        content = result.text or ""
        return ExtractionResult(
            success=True,
            content=content,
            extraction_method=method,
            token_count=count_tokens(content),
        )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            content="",
            extraction_method=method,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# PDF dispatch
# ---------------------------------------------------------------------------

async def _extract_pdf(
    path: Path,
    ingest_config: "IngestConfig | None",
) -> ExtractionResult:
    """Dispatch PDF extraction to the configured extractor."""
    extractor = (ingest_config.pdf_extractor if ingest_config else "pdftotext")

    if extractor == "pdftotext":
        result = await _extract_pdf_pdftotext(path)
    elif extractor == "local-ocr":
        endpoint = ingest_config.local_ocr_endpoint if ingest_config else "http://localhost:8006/v1"
        model = ingest_config.local_ocr_model if ingest_config else "qianfan-ocr"
        result = await _extract_pdf_local_ocr(path, endpoint, model)
    elif extractor == "marker":
        result = await _extract_pdf_marker(path)
    elif extractor == "nougat":
        result = await _extract_pdf_nougat(path)
    else:
        # Unknown value — fall back to pdftotext
        result = await _extract_pdf_pdftotext(path)

    if result.success:
        warning = _detect_quality_issues(result.content)
        if warning:
            return ExtractionResult(
                success=result.success,
                content=result.content,
                extraction_method=result.extraction_method,
                token_count=result.token_count,
                error=result.error,
                quality_warning=warning,
            )
    return result


async def _extract_pdf_pdftotext(path: Path) -> ExtractionResult:
    """Extract PDF text via pdftotext system binary (poppler)."""
    # TODO(async): asyncio subprocess — correct for this new code; migrate
    # existing blocking subprocess calls in server.py in a follow-up.
    proc = await asyncio.create_subprocess_exec(
        "pdftotext", "-layout", str(path), "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return ExtractionResult(
            success=False,
            content="",
            extraction_method="pdf",
            error=f"pdftotext failed (exit {proc.returncode}): {stderr.decode('utf-8', errors='replace')[:200]}",
        )
    content = stdout.decode("utf-8", errors="replace")
    return ExtractionResult(
        success=True,
        content=content,
        extraction_method="pdf",
        token_count=count_tokens(content),
    )


async def _extract_pdf_local_ocr(
    path: Path,
    endpoint: str,
    model: str,
) -> ExtractionResult:
    """Extract PDF via a local OpenAI-compatible vision endpoint (e.g. Qianfan-OCR).

    Renders each PDF page to a PNG via pdftoppm, then sends all page images
    in a single vision API call.
    """
    loop = asyncio.get_event_loop()
    try:
        page_images = await loop.run_in_executor(None, _render_pdf_pages_to_base64, path)
    except Exception as exc:
        return ExtractionResult(
            success=False,
            content="",
            extraction_method="pdf",
            error=f"pdftoppm page rendering failed: {exc}",
        )

    try:
        content = await loop.run_in_executor(
            None, _call_vision_api, endpoint, model, page_images
        )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            content="",
            extraction_method="pdf",
            error=f"Vision API call failed: {exc}",
        )

    return ExtractionResult(
        success=True,
        content=content,
        extraction_method="pdf",
        token_count=count_tokens(content),
    )


def _render_pdf_pages_to_base64(path: Path) -> list[str]:
    """Render PDF pages to PNG images and return as base64-encoded strings.

    Uses pdftoppm (poppler). Raises on failure.
    """
    import glob
    import subprocess

    with tempfile.TemporaryDirectory() as tmpdir:
        prefix = str(Path(tmpdir) / "page")
        result = subprocess.run(
            ["pdftoppm", "-png", "-r", "150", str(path), prefix],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pdftoppm failed (exit {result.returncode}): "
                f"{result.stderr.decode('utf-8', errors='replace')[:200]}"
            )
        pages = sorted(glob.glob(f"{prefix}-*.png"))
        if not pages:
            raise RuntimeError("pdftoppm produced no page images")
        return [
            base64.b64encode(Path(p).read_bytes()).decode("ascii")
            for p in pages
        ]


def _call_vision_api(endpoint: str, model: str, page_images: list[str]) -> str:
    """POST page images to an OpenAI-compatible vision endpoint.

    Sends all pages in a single request. Returns the assistant content string.
    """
    content = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        }
        for b64 in page_images
    ]
    content.append({
        "type": "text",
        "text": (
            "Convert these document pages to markdown. "
            "Preserve tables, equations, figures, and document structure. "
            "Output only the markdown, no commentary."
        ),
    })
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }).encode()
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


async def _extract_pdf_marker(path: Path) -> ExtractionResult:
    """Extract PDF via the marker-pdf CLI (marker_single)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        proc = await asyncio.create_subprocess_exec(
            "marker_single", str(path), "--output-dir", tmpdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            return ExtractionResult(
                success=False,
                content="",
                extraction_method="pdf",
                error=f"marker failed (exit {proc.returncode}): {stderr.decode('utf-8', errors='replace')[:200]}",
            )
        # marker_single writes <stem>/<stem>.md inside output-dir
        stem = path.stem
        output_file = Path(tmpdir) / stem / f"{stem}.md"
        if not output_file.exists():
            # Fallback: find any .md in the output dir
            candidates = list(Path(tmpdir).rglob("*.md"))
            if not candidates:
                return ExtractionResult(
                    success=False,
                    content="",
                    extraction_method="pdf",
                    error="marker produced no output file",
                )
            output_file = candidates[0]
        content = output_file.read_text(encoding="utf-8")
    return ExtractionResult(
        success=True,
        content=content,
        extraction_method="pdf",
        token_count=count_tokens(content),
    )


async def _extract_pdf_nougat(path: Path) -> ExtractionResult:
    """Extract PDF via the nougat-ocr CLI."""
    with tempfile.TemporaryDirectory() as tmpdir:
        proc = await asyncio.create_subprocess_exec(
            "nougat", str(path), "-o", tmpdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            return ExtractionResult(
                success=False,
                content="",
                extraction_method="pdf",
                error=f"nougat failed (exit {proc.returncode}): {stderr.decode('utf-8', errors='replace')[:200]}",
            )
        # nougat writes <stem>.mmd in the output dir
        output_file = Path(tmpdir) / f"{path.stem}.mmd"
        if not output_file.exists():
            candidates = list(Path(tmpdir).glob("*.mmd"))
            if not candidates:
                return ExtractionResult(
                    success=False,
                    content="",
                    extraction_method="pdf",
                    error="nougat produced no output file",
                )
            output_file = candidates[0]
        content = output_file.read_text(encoding="utf-8")
    return ExtractionResult(
        success=True,
        content=content,
        extraction_method="pdf",
        token_count=count_tokens(content),
    )


# ---------------------------------------------------------------------------
# Quality signal
# ---------------------------------------------------------------------------

def _detect_quality_issues(content: str) -> str | None:
    """Heuristically detect mangled extraction output.

    Returns a warning string if the text looks broken, None if it looks fine.
    Short content (< 10 non-empty lines) is not evaluated.
    """
    lines = content.splitlines()
    non_empty = [l for l in lines if l.strip()]
    if len(non_empty) < 10:
        return None

    words = content.split()
    word_line_ratio = len(words) / len(non_empty)

    # Heuristic 1: very low word/line ratio → lines are fragments (headers-only,
    # broken tables, one-word-per-line from bad PDF column parsing)
    if word_line_ratio < 3.0:
        return (
            f"low word/line ratio ({word_line_ratio:.1f}) — extraction may be mangled "
            "(broken tables, equations, or column mis-parsing). "
            "Consider local-ocr or marker for better layout handling."
        )

    # Heuristic 2: high proportion of very short non-empty lines
    short = sum(1 for l in non_empty if len(l.strip()) < 15)
    short_ratio = short / len(non_empty)
    if short_ratio > 0.45:
        return (
            f"high short-line ratio ({short_ratio:.0%}) — extraction may have broken up "
            "paragraphs or produced watermark/header repetition. "
            "Consider local-ocr or marker for better layout handling."
        )

    return None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_ingest/test_extractor.py -k "pdftotext or quality_signal" -v
```

Expected: all PASS

- [ ] **Step 5: Confirm existing liteparse-path tests still pass**

```bash
pytest tests/test_ingest/test_extractor.py -v
```

Expected: all PASS (the pre-existing DOCX, image, markdown, and error tests must still pass — they use `_parser` injection and don't touch the PDF path)

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/ingest/extractor.py tests/test_ingest/test_extractor.py
git commit -m "feat: pdftotext extractor + quality signal (default PDF extraction path)

Agent: subagent-task2"
```

---

### Task 3: `local-ocr`, `marker`, `nougat` extractor tests

**Files:**
- Modify: `tests/test_ingest/test_extractor.py`

The implementations are already in `extractor.py` from Task 2. This task adds tests for the three non-default paths so they're exercised.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ingest/test_extractor.py`:

```python
# ---------------------------------------------------------------------------
# local-ocr extractor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_pdf_local_ocr_success(tmp_path: Path):
    """local-ocr path calls _render_pdf_pages_to_base64 and _call_vision_api."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    with patch("llm_wiki.ingest.extractor._render_pdf_pages_to_base64", return_value=["base64img"]) as mock_render, \
         patch("llm_wiki.ingest.extractor._call_vision_api", return_value="# Extracted\n\nContent.") as mock_api:
        config = IngestConfig(
            pdf_extractor="local-ocr",
            local_ocr_endpoint="http://localhost:8006/v1",
            local_ocr_model="qianfan-ocr",
        )
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    assert result.content == "# Extracted\n\nContent."
    assert result.extraction_method == "pdf"
    mock_render.assert_called_once_with(pdf)
    mock_api.assert_called_once_with("http://localhost:8006/v1", "qianfan-ocr", ["base64img"])


@pytest.mark.asyncio
async def test_extract_pdf_local_ocr_render_failure(tmp_path: Path):
    """pdftoppm failure returns a failed ExtractionResult."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    with patch("llm_wiki.ingest.extractor._render_pdf_pages_to_base64", side_effect=RuntimeError("pdftoppm not found")):
        config = IngestConfig(pdf_extractor="local-ocr")
        result = await extract_text(pdf, ingest_config=config)

    assert not result.success
    assert "pdftoppm" in result.error


@pytest.mark.asyncio
async def test_extract_pdf_local_ocr_api_failure(tmp_path: Path):
    """Vision API failure returns a failed ExtractionResult."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    with patch("llm_wiki.ingest.extractor._render_pdf_pages_to_base64", return_value=["img"]), \
         patch("llm_wiki.ingest.extractor._call_vision_api", side_effect=Exception("connection refused")):
        config = IngestConfig(pdf_extractor="local-ocr")
        result = await extract_text(pdf, ingest_config=config)

    assert not result.success
    assert "Vision API" in result.error


# ---------------------------------------------------------------------------
# marker extractor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_pdf_marker_success(tmp_path: Path):
    """marker_single subprocess produces output and extraction succeeds."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    async def _fake_marker(cmd, *args, **kwargs):
        # Simulate marker writing output file to the tmpdir arg
        output_dir = cmd[cmd.index("--output-dir") + 1]
        stem = Path(cmd[1]).stem
        out_dir = Path(output_dir) / stem
        out_dir.mkdir(parents=True)
        (out_dir / f"{stem}.md").write_text("# Paper\n\nContent from marker.\n")
        mock = AsyncMock()
        mock.returncode = 0
        mock.communicate = AsyncMock(return_value=(b"", b""))
        return mock

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_marker):
        config = IngestConfig(pdf_extractor="marker")
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    assert "Content from marker." in result.content


@pytest.mark.asyncio
async def test_extract_pdf_marker_failure(tmp_path: Path):
    """marker_single non-zero exit returns a failed ExtractionResult."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"marker: error processing file"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        config = IngestConfig(pdf_extractor="marker")
        result = await extract_text(pdf, ingest_config=config)

    assert not result.success
    assert "marker" in result.error


# ---------------------------------------------------------------------------
# nougat extractor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_pdf_nougat_success(tmp_path: Path):
    """nougat subprocess produces .mmd output and extraction succeeds."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    async def _fake_nougat(cmd, *args, **kwargs):
        # cmd: ["nougat", <pdf>, "-o", <tmpdir>]
        output_dir = cmd[cmd.index("-o") + 1]
        stem = Path(cmd[1]).stem
        (Path(output_dir) / f"{stem}.mmd").write_text("# Nougat output\n\nEquations here.\n")
        mock = AsyncMock()
        mock.returncode = 0
        mock.communicate = AsyncMock(return_value=(b"", b""))
        return mock

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_nougat):
        config = IngestConfig(pdf_extractor="nougat")
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    assert "Nougat output" in result.content


@pytest.mark.asyncio
async def test_extract_pdf_nougat_failure(tmp_path: Path):
    """nougat non-zero exit returns a failed ExtractionResult."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"nougat: CUDA not available"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        config = IngestConfig(pdf_extractor="nougat")
        result = await extract_text(pdf, ingest_config=config)

    assert not result.success
    assert "nougat" in result.error


# ---------------------------------------------------------------------------
# unknown extractor falls back to pdftotext
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_pdf_unknown_extractor_falls_back_to_pdftotext(tmp_path: Path):
    """An unrecognised pdf_extractor value falls back to pdftotext."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake pdf")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"fallback text", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        config = IngestConfig(pdf_extractor="unknown-tool")
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    args = mock_exec.call_args[0]
    assert args[0] == "pdftotext"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_ingest/test_extractor.py -k "local_ocr or marker or nougat or unknown_extractor" -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` or `AttributeError` — the implementations are there but `IngestConfig` import in the test file needs to be verified.

- [ ] **Step 3: Run all extractor tests**

```bash
pytest tests/test_ingest/test_extractor.py -v
```

Expected: all PASS. If the marker/nougat fake subprocess functions don't work correctly, the issue is likely in how `asyncio.create_subprocess_exec` is patched when using `side_effect` with a coroutine — the fake function must return an awaitable mock. Debug by checking that `mock.communicate` is an `AsyncMock`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_ingest/test_extractor.py
git commit -m "test: local-ocr, marker, nougat extractor paths + unknown fallback

Agent: subagent-task3"
```

---

### Task 4: Thread config through agent + surface extraction warning

**Files:**
- Modify: `src/llm_wiki/ingest/agent.py`
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `tests/test_ingest/test_agent.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ingest/test_agent.py`:

```python
# ---------------------------------------------------------------------------
# extraction_warning threading
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_result_includes_extraction_warning(tmp_path):
    """When extraction returns a quality_warning, IngestResult carries it through."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from llm_wiki.config import WikiConfig
    from llm_wiki.ingest.agent import IngestAgent
    from llm_wiki.ingest.extractor import ExtractionResult

    # Source file
    source = tmp_path / "raw" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake pdf")
    (tmp_path / "wiki").mkdir()

    config = WikiConfig()

    # Patched extract_text that returns a result with a quality_warning
    warned_result = ExtractionResult(
        success=True,
        content="x\n" * 50,
        extraction_method="pdf",
        token_count=50,
        quality_warning="low word/line ratio (1.0) — extraction may be mangled",
    )

    fake_llm = AsyncMock()
    fake_llm.complete = AsyncMock(return_value=MagicMock(content='{"concepts": []}'))

    with patch("llm_wiki.ingest.agent.extract_text", return_value=warned_result):
        agent = IngestAgent(config=config, llm=fake_llm)
        result = await agent.ingest(
            source_path=source,
            vault_root=tmp_path,
            dry_run=True,
        )

    assert result.extraction_warning == "low word/line ratio (1.0) — extraction may be mangled"


@pytest.mark.asyncio
async def test_ingest_result_extraction_warning_absent_on_clean_extraction(tmp_path):
    """Clean extraction produces IngestResult with extraction_warning = None."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from llm_wiki.config import WikiConfig
    from llm_wiki.ingest.agent import IngestAgent
    from llm_wiki.ingest.extractor import ExtractionResult

    source = tmp_path / "raw" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake pdf")
    (tmp_path / "wiki").mkdir()

    config = WikiConfig()

    clean_result = ExtractionResult(
        success=True,
        content="Normal text content with good word line ratio.\n" * 5,
        extraction_method="pdf",
        token_count=100,
        quality_warning=None,
    )

    fake_llm = AsyncMock()
    fake_llm.complete = AsyncMock(return_value=MagicMock(content='{"concepts": []}'))

    with patch("llm_wiki.ingest.agent.extract_text", return_value=clean_result):
        agent = IngestAgent(config=config, llm=fake_llm)
        result = await agent.ingest(
            source_path=source,
            vault_root=tmp_path,
            dry_run=True,
        )

    assert result.extraction_warning is None
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_ingest/test_agent.py -k "extraction_warning" -v 2>&1 | head -15
```

Expected: `AttributeError: 'IngestResult' object has no attribute 'extraction_warning'`

- [ ] **Step 3: Add `extraction_warning` to `IngestResult`**

In `src/llm_wiki/ingest/agent.py`, update `IngestResult`:

```python
@dataclass
class IngestResult:
    """Result of ingesting one source document."""
    source_path: Path
    pages_created: list[str] = field(default_factory=list)
    pages_updated: list[str] = field(default_factory=list)
    dry_run: bool = False
    concepts_planned: list[ConceptPreview] = field(default_factory=list)
    source_chars: int = 0
    extraction_warning: str | None = None    # ← new
```

- [ ] **Step 4: Thread config and set warning in `IngestAgent.ingest()`**

In `src/llm_wiki/ingest/agent.py`, update the `extract_text` call (around line 122):

Before:
```python
        extraction = await extract_text(source_path)
```

After:
```python
        extraction = await extract_text(
            source_path,
            ingest_config=self._config.ingest,
        )
```

Immediately after `result.source_chars = len(extraction.content)` (around line 129), add:

```python
        if extraction.quality_warning:
            result.extraction_warning = extraction.quality_warning
```

- [ ] **Step 5: Run agent tests**

```bash
pytest tests/test_ingest/test_agent.py -k "extraction_warning" -v
```

Expected: both PASS

- [ ] **Step 6: Surface extraction warning in daemon ingest response**

In `src/llm_wiki/daemon/server.py`, in `_handle_ingest()`, after the truncation warning is appended (the existing `warnings.append({"code": "response-truncated", ...})` block), add:

```python
        if result.extraction_warning:
            warnings.append({
                "code": "extraction-quality",
                "message": result.extraction_warning,
            })
```

Then ensure the `warnings` list is included in the response even when not truncated. Replace the existing `if truncated:` block with:

```python
        if truncated:
            response["truncated"] = True
            response["shown"] = cap
        if warnings:
            response["warnings"] = warnings
```

(Move `response["warnings"] = warnings` outside the `if truncated:` block so extraction warnings also surface when truncation hasn't occurred.)

- [ ] **Step 7: Run full test suite — no regressions**

```bash
pytest tests/ -q 2>&1 | tail -10
```

Expected: all PASS (1 pre-existing failure in `test_full_workflow` is unrelated).

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/ingest/agent.py src/llm_wiki/daemon/server.py tests/test_ingest/test_agent.py
git commit -m "feat: thread extraction_warning through IngestResult to daemon response

Agent: subagent-task4"
```

---

## Self-Review

**Spec coverage:**

| Requirement (TODO §6) | Task |
|---|---|
| Config key `pdf_extractor` in `ingest:` block | Task 1 |
| `local_ocr_endpoint` + `local_ocr_model` config | Task 1 |
| `pdftotext` path — subprocess call | Task 2 |
| `local-ocr` path — page images + vision endpoint | Task 2 |
| `marker` path — subprocess | Task 2 |
| `nougat` path — subprocess | Task 2 |
| Quality signal heuristic (low word/line ratio, short lines) | Task 2 |
| Warning surfaced in ingest response | Task 4 |

**Placeholder scan:** None found.

**Type consistency:**
- `IngestConfig` defined in Task 1, imported via `TYPE_CHECKING` in `extractor.py`, directly in `agent.py` via `self._config.ingest` — consistent
- `ExtractionResult.quality_warning: str | None` — set in `_extract_pdf()`, read in `agent.py` as `extraction.quality_warning` — consistent
- `IngestResult.extraction_warning: str | None` — set in `agent.py`, read in `server.py` as `result.extraction_warning` — consistent
- `extract_text(source_path, ingest_config=self._config.ingest)` — `ingest_config` param added in Task 2, used in Task 4 — consistent
