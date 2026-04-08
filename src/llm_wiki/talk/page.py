from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


# Matches an entry header line: **<iso-timestamp> — @<author>**
_ENTRY_HEADER_RE = re.compile(
    r"^\*\*(?P<ts>\S+)\s*[—-]\s*(?P<author>@\S+)\*\*\s*$",
    re.MULTILINE,
)


@dataclass
class TalkEntry:
    """One chronological entry in a talk page."""
    timestamp: str
    author: str
    body: str


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
            content_start = match.end()
            content_end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
            entry_body = body[content_start:content_end].strip()
            entries.append(TalkEntry(timestamp=ts, author=author, body=entry_body))
        return entries

    def append(self, entry: TalkEntry) -> None:
        """Append a new entry, creating the file with frontmatter if missing."""
        block = (
            f"\n**{entry.timestamp} — {entry.author}**\n"
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
