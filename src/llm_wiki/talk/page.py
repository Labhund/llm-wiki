from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Matches an entry header line: **<iso-timestamp> — @<author>**
# with optional HTML-comment metadata: <!-- severity:critical, resolves:[1,2] -->
_ENTRY_HEADER_RE = re.compile(
    r"^\*\*(?P<ts>\S+)\s*[—-]\s*(?P<author>@\S+)\*\*"
    r"(?:\s*<!--\s*(?P<meta>[^>]*?)\s*-->)?\s*$",
    re.MULTILINE,
)


def _parse_meta(meta_str: str | None) -> tuple[str, list[int]]:
    """Parse a `severity:foo, resolves:[1,2]` metadata blob.

    Returns (severity, resolves). Missing keys default to ("suggestion", []).
    Whitespace and key order are tolerant. Invalid blobs return defaults.
    """
    if not meta_str:
        return "suggestion", []

    severity = "suggestion"
    resolves: list[int] = []

    # Split top-level by comma — but not inside [...] which holds the resolves list.
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in meta_str:
        if ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())

    for part in parts:
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "severity":
            severity = value
        elif key == "resolves":
            inner = value.strip("[]")
            if inner:
                try:
                    resolves = [int(x.strip()) for x in inner.split(",") if x.strip()]
                except ValueError:
                    resolves = []
    return severity, resolves


def _format_meta(severity: str, resolves: list[int]) -> str:
    """Build the optional `<!-- ... -->` suffix for an entry header line.

    Returns an empty string for the default case (severity='suggestion',
    no resolves) so the writer emits the same shape as pre-Phase-6a files.
    """
    parts: list[str] = []
    if severity != "suggestion":
        parts.append(f"severity:{severity}")
    if resolves:
        joined = ",".join(str(i) for i in resolves)
        parts.append(f"resolves:[{joined}]")
    if not parts:
        return ""
    return f" <!-- {', '.join(parts)} -->"


@dataclass
class TalkEntry:
    """One chronological entry in a talk page.

    `index` is 1-based and positional — assigned by `TalkPage.load()` from the
    entry's chronological position in the file. It is not stored in the file
    and may be left as 0 by callers constructing entries to pass to `append()`.
    """
    index: int
    timestamp: str
    author: str
    body: str
    severity: str = "suggestion"
    resolves: list[int] = field(default_factory=list)


class TalkPage:
    """Append-only sidecar discussion file at <wiki_dir>/<page>.talk.md.

    Format:
        ---
        page: <slug>
        ---

        **<timestamp> — @<author>**
        body...

        **<timestamp> — @<author>**
        body...

    Talk pages are excluded from Vault.scan() page indexing — see Task 7.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def for_page(cls, page_path: Path) -> "TalkPage":
        """Derive the sidecar talk path for a wiki page path."""
        return cls(page_path.parent / f"{page_path.stem}.talk.md")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def exists(self) -> bool:
        return self._path.exists()

    @property
    def parent_page_slug(self) -> str:
        """Strip the .talk suffix from the file stem to get the parent slug."""
        stem = self._path.stem  # foo.talk
        if stem.endswith(".talk"):
            return stem[: -len(".talk")]
        return stem

    def load(self) -> list[TalkEntry]:
        if not self._path.exists():
            return []
        text = self._path.read_text(encoding="utf-8")
        body = self._strip_frontmatter(text)

        headers = list(_ENTRY_HEADER_RE.finditer(body))
        entries: list[TalkEntry] = []
        for i, match in enumerate(headers):
            ts = match.group("ts")
            author = match.group("author")
            meta = match.group("meta")
            severity, resolves = _parse_meta(meta)
            content_start = match.end()
            content_end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
            entry_body = body[content_start:content_end].strip()
            entries.append(TalkEntry(
                index=i + 1,                  # 1-based, positional
                timestamp=ts,
                author=author,
                body=entry_body,
                severity=severity,
                resolves=resolves,
            ))
        return entries

    def append(self, entry: TalkEntry) -> None:
        """Append a new entry, creating the file with frontmatter if missing.

        The caller's `entry.index` is ignored — indices are positional and
        get assigned by `load()`. The optional severity/resolves fields ride
        in an HTML comment on the header line; the default case writes the
        same shape as pre-Phase-6a files.
        """
        meta_suffix = _format_meta(entry.severity, entry.resolves)
        block = (
            f"\n**{entry.timestamp} — {entry.author}**{meta_suffix}\n"
            f"{entry.body.strip()}\n"
        )
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            frontmatter = yaml.dump(
                {"page": self.parent_page_slug},
                default_flow_style=False,
            ).strip()
            self._path.write_text(
                f"---\n{frontmatter}\n---\n{block}", encoding="utf-8"
            )
        else:
            existing = self._path.read_text(encoding="utf-8").rstrip()
            self._path.write_text(existing + "\n" + block, encoding="utf-8")

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if not text.startswith("---\n"):
            return text
        try:
            end = text.index("\n---", 4)
        except ValueError:
            return text
        return text[end + 4:].lstrip()
