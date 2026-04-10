from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_wiki.page import Page


# A sentence is "completed" by sentence-final punctuation OR end of section.
# We split on sentence-final punctuation followed by whitespace/end.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Match a fenced code block opener/closer (``` or ~~~), mirroring the
# compliance reviewer's pattern.
_CODE_FENCE_RE = re.compile(r"^(?:```|~~~)")
_MARKER_LINE_RE = re.compile(r"^%%\s*section:")


@dataclass
class Claim:
    """One verifiable assertion: a sentence with a [[raw/...]] suffix citation."""
    page: str
    section: str
    text: str
    citation: str

    @property
    def id(self) -> str:
        """12-char hex hash deterministic in (page, section, text)."""
        digest = hashlib.sha256(
            f"{self.page}|{self.section}|{self.text}".encode("utf-8")
        ).hexdigest()
        return digest[:12]


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


def _extract_body_sentences(content: str) -> list[str]:
    """Sentences from non-code, non-marker lines of a section body."""
    keep_lines: list[str] = []
    in_code = False
    for line in content.splitlines():
        stripped = line.strip()
        if _CODE_FENCE_RE.match(stripped):
            in_code = not in_code
            continue
        if in_code:
            continue
        if _MARKER_LINE_RE.match(stripped):
            continue
        if stripped.startswith("#"):
            continue
        keep_lines.append(line)
    joined = " ".join(l.strip() for l in keep_lines if l.strip())
    if not joined:
        return []
    sentences = _SENTENCE_SPLIT_RE.split(joined)
    return [s.strip() for s in sentences if s.strip()]
