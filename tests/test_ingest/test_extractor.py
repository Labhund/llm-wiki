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
    """PDF extraction uses pdftotext (not liteparse); _parser is ignored for PDFs."""
    from unittest.mock import AsyncMock, patch

    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"fake pdf content")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"PDF text here.", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        # _parser is passed but should be ignored — PDF routes to pdftotext now
        result = await extract_text(pdf_file, _parser=_FakeParser("should be ignored"))

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
    """ParseError from liteparse on a DOCX becomes a failed ExtractionResult.
    (PDFs no longer go through liteparse — they use pdftotext.)
    """
    docx_file = tmp_path / "corrupt.docx"
    docx_file.write_bytes(b"not a real docx")

    result = await extract_text(docx_file, _parser=_ErrorParser())

    assert not result.success
    assert "corrupt file" in result.error
    assert result.extraction_method == "docx"


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
    """Word/line ratio < 3.0 triggers quality_warning (heuristic 1)."""
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"fake pdf")
    # 50 single-word lines → ratio = 1.0, well below the 3.0 threshold
    mangled = "\n".join(["word"] * 50)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(mangled.encode(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        config = IngestConfig(pdf_extractor="pdftotext")
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    assert result.quality_warning is not None
    assert "word/line" in result.quality_warning


@pytest.mark.asyncio
async def test_quality_signal_on_high_short_line_ratio(tmp_path: Path):
    """Short-line ratio > 0.45 triggers quality_warning (heuristic 2).

    30 short lines (12 chars, 3 words each) + 10 long lines (8 words each):
    - word/line ratio = (30*3 + 10*8) / 40 = 170/40 = 4.25 → above 3.0 (heuristic 1 silent)
    - short-line ratio = 30/40 = 0.75 → above 0.45 (heuristic 2 fires)
    """
    pdf = tmp_path / "bad2.pdf"
    pdf.write_bytes(b"fake pdf")
    lines = ["abc defg hij"] * 30 + ["word one two three four five six seven"] * 10
    text = "\n".join(lines)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(text.encode(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        config = IngestConfig(pdf_extractor="pdftotext")
        result = await extract_text(pdf, ingest_config=config)

    assert result.success
    assert result.quality_warning is not None
    assert "short-line" in result.quality_warning


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
        # asyncio.create_subprocess_exec receives args as positional params
        full_cmd = (cmd,) + args
        output_dir = full_cmd[full_cmd.index("--output-dir") + 1]
        stem = Path(full_cmd[1]).stem
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
        # asyncio.create_subprocess_exec receives args as positional params
        full_cmd = (cmd,) + args
        output_dir = full_cmd[full_cmd.index("-o") + 1]
        stem = Path(full_cmd[1]).stem
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
