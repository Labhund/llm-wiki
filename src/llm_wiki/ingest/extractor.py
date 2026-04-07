from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_wiki.tokens import count_tokens


@dataclass
class ExtractionResult:
    """Result of document text extraction."""
    success: bool
    content: str
    extraction_method: str   # "pdf", "docx", "image_ocr", "markdown"
    token_count: int = 0
    error: str | None = None


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
    _parser: Any = None,     # injection point for tests; defaults to LiteParse()
) -> ExtractionResult:
    """Extract text from a document.

    Supports: PDF, DOCX, images (OCR via liteparse), markdown (passthrough).
    _parser is a liteparse.LiteParse-compatible object; pass a fake in tests to
    avoid the Node.js CLI cold-start.
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
    """Call liteparse (or test fake) to extract text from binary docs."""
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
