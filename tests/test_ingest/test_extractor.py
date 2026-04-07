from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.ingest.extractor import ExtractionResult, extract_text


# ---------------------------------------------------------------------------
# Minimal stand-in for liteparse.types.ParseResult so tests don't depend on
# liteparse's Node.js CLI being available.
# ---------------------------------------------------------------------------
class _FakeParseResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pages = []


class _FakeParser:
    """Fake liteparse.LiteParse that returns scripted text."""
    def __init__(self, text: str = "Extracted content.") -> None:
        self._text = text

    async def parse_async(self, path, **kwargs) -> _FakeParseResult:
        return _FakeParseResult(self._text)


class _ErrorParser:
    """Fake liteparse.LiteParse that raises ParseError."""
    async def parse_async(self, path, **kwargs):
        from liteparse.types import ParseError
        raise ParseError("corrupt file")


@pytest.mark.asyncio
async def test_extract_pdf(tmp_path: Path):
    """PDF extraction returns liteparse text."""
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"fake pdf content")

    result = await extract_text(pdf_file, _parser=_FakeParser("PDF text here."))

    assert result.success
    assert result.content == "PDF text here."
    assert result.extraction_method == "pdf"
    assert result.token_count > 0
    assert result.error is None


@pytest.mark.asyncio
async def test_extract_docx(tmp_path: Path):
    """DOCX extraction returns liteparse text."""
    docx_file = tmp_path / "test.docx"
    docx_file.write_bytes(b"fake docx content")

    result = await extract_text(docx_file, _parser=_FakeParser("DOCX text here."))

    assert result.success
    assert result.content == "DOCX text here."
    assert result.extraction_method == "docx"


@pytest.mark.asyncio
async def test_extract_image_ocr(tmp_path: Path):
    """Image extraction uses OCR via liteparse."""
    img_file = tmp_path / "scan.png"
    img_file.write_bytes(b"fake png")

    result = await extract_text(img_file, _parser=_FakeParser("OCR text."))

    assert result.success
    assert result.content == "OCR text."
    assert result.extraction_method == "image_ocr"


@pytest.mark.asyncio
async def test_extract_markdown_passthrough(tmp_path: Path):
    """Markdown files are read directly — liteparse is NOT called."""
    md_content = "# Test\n\nContent here."
    md_file = tmp_path / "test.md"
    md_file.write_text(md_content)

    # Pass an error parser — if it's called, the test will fail
    result = await extract_text(md_file, _parser=_ErrorParser())

    assert result.success
    assert result.content == md_content
    assert result.extraction_method == "markdown"
    assert result.token_count > 0


@pytest.mark.asyncio
async def test_extract_liteparse_error(tmp_path: Path):
    """ParseError from liteparse becomes a failed ExtractionResult."""
    pdf_file = tmp_path / "corrupt.pdf"
    pdf_file.write_bytes(b"not a real pdf")

    result = await extract_text(pdf_file, _parser=_ErrorParser())

    assert not result.success
    assert "corrupt file" in result.error
    assert result.extraction_method == "pdf"


@pytest.mark.asyncio
async def test_extract_unsupported_format(tmp_path: Path):
    """Unsupported file extension returns error without calling liteparse."""
    bad_file = tmp_path / "data.xyz"
    bad_file.write_text("content")

    result = await extract_text(bad_file)

    assert not result.success
    assert "Unsupported" in result.error


@pytest.mark.asyncio
async def test_extract_nonexistent_file():
    """Missing files return error."""
    result = await extract_text(Path("/nonexistent/file.pdf"))

    assert not result.success
    assert "No such file" in result.error
