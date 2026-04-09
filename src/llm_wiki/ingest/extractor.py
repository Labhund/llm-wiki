from __future__ import annotations

import asyncio
import base64
import json
import logging
import tempfile
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

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
        # Unknown value — fall back to pdftotext and warn so misconfiguration is visible
        _log.warning("Unknown pdf_extractor %r — falling back to pdftotext", extractor)
        result = await _extract_pdf_pdftotext(path)

    if result.success:
        warning = _detect_quality_issues(result.content)
        if warning:
            return replace(result, quality_warning=warning)
    return result


async def _extract_pdf_pdftotext(path: Path) -> ExtractionResult:
    """Extract PDF text via pdftotext system binary (poppler)."""
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
    loop = asyncio.get_running_loop()
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
