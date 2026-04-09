from __future__ import annotations

import datetime
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from llm_wiki.severity import Severity

# Issue ids are filesystem-sensitive: they're concatenated with `.md` and joined
# to the `.issues/` directory without further sanitization. Lock the shape to
# lowercase alnum + hyphen so an attacker-controlled id can't escape the
# directory via `..`, absolute paths, or other surprises. The shape matches
# `Issue.make_id`'s output: `<type>-<page-or-vault>-<6hex>` (all lowercase).
_ISSUE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,127}$")


def _validate_id(issue_id: str) -> None:
    """Raise ValueError unless `issue_id` is a safe, well-formed id."""
    if not isinstance(issue_id, str) or not _ISSUE_ID_RE.match(issue_id):
        raise ValueError(f"Invalid issue id: {issue_id!r}")


@dataclass
class Issue:
    """One issue in the queue, persisted as wiki/.issues/<id>.md.

    Issues are idempotent: re-running a check that finds the same problem
    produces the same id (via make_id) and the existing file is left alone.
    """

    id: str
    type: str
    status: str
    title: str
    page: str | None
    body: str
    created: str
    detected_by: str
    metadata: dict = field(default_factory=dict)
    severity: Severity = "minor"

    @staticmethod
    def make_id(type: str, page: str | None, key: str) -> str:
        """Build a deterministic id from (type, page, key).

        `key` is the discriminator that uniquely identifies this specific
        instance of the issue type — e.g. the broken-link target slug, the
        missing citation path. The hash is content-addressable so the same
        problem always maps to the same file on disk.

        The returned id is validated against `_ISSUE_ID_RE` so callers can
        trust the shape when joining it to a filesystem path. If `type` or
        `page` contains characters outside `[a-z0-9-]`, this raises
        `ValueError` instead of silently producing a malformed id.
        """
        digest = hashlib.sha256(
            f"{type}|{page or ''}|{key}".encode("utf-8")
        ).hexdigest()[:6]
        page_part = page or "vault"
        issue_id = f"{type}-{page_part}-{digest}"
        _validate_id(issue_id)
        return issue_id

    @staticmethod
    def now_iso() -> str:
        """Current time as ISO 8601 UTC. Centralized so tests can monkeypatch."""
        return datetime.datetime.now(datetime.timezone.utc).isoformat()


class IssueQueue:
    """Filesystem-backed issue queue at <wiki_dir>/.issues/.

    Issues are stored one-per-file as YAML frontmatter + markdown body.
    The id is the filename (without .md extension). Add operations are
    idempotent: if an issue with the same id already exists on disk, the
    existing file is preserved unchanged and add() returns was_new=False.

    The .issues directory is excluded from Vault.scan() because Vault
    already filters out hidden directories (those starting with '.').
    """

    def __init__(self, wiki_dir: Path) -> None:
        self._wiki_dir = wiki_dir

    @property
    def issues_dir(self) -> Path:
        return self._wiki_dir / ".issues"

    def exists(self, issue_id: str) -> bool:
        _validate_id(issue_id)
        return (self.issues_dir / f"{issue_id}.md").exists()

    def add(self, issue: Issue) -> tuple[Path, bool]:
        """Write the issue to disk if not already present.

        Returns:
            (path, was_new) — was_new is False if the file already existed.
        """
        path = self.issues_dir / f"{issue.id}.md"
        if path.exists():
            return path, False

        self.issues_dir.mkdir(parents=True, exist_ok=True)
        fm = {
            "id": issue.id,
            "type": issue.type,
            "status": issue.status,
            "severity": issue.severity,
            "title": issue.title,
            "page": issue.page,
            "created": issue.created,
            "detected_by": issue.detected_by,
            "metadata": issue.metadata,
        }
        frontmatter = yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
        path.write_text(
            f"---\n{frontmatter}\n---\n\n{issue.body.strip()}\n",
            encoding="utf-8",
        )
        return path, True

    def get(self, issue_id: str) -> Issue | None:
        _validate_id(issue_id)
        path = self.issues_dir / f"{issue_id}.md"
        if not path.exists():
            return None
        return self._parse_file(path)

    def list(
        self,
        status: str | None = None,
        type: str | None = None,
    ) -> list[Issue]:
        """Return all issues, optionally filtered by status and/or type."""
        if not self.issues_dir.exists():
            return []
        results: list[Issue] = []
        for path in sorted(self.issues_dir.glob("*.md")):
            issue = self._parse_file(path)
            if issue is None:
                continue
            if status is not None and issue.status != status:
                continue
            if type is not None and issue.type != type:
                continue
            results.append(issue)
        return results

    _VALID_STATUSES = {"open", "resolved", "wontfix"}

    def update_status(self, issue_id: str, new_status: str) -> bool:
        """Mutate the status field, preserving all other fields and the body."""
        _validate_id(issue_id)
        if new_status not in self._VALID_STATUSES:
            raise ValueError(
                f"Invalid status {new_status!r}; must be one of {sorted(self._VALID_STATUSES)}"
            )
        issue = self.get(issue_id)
        if issue is None:
            return False
        issue.status = new_status
        # Re-write by deleting + re-adding (preserves the file path since id is unchanged)
        path = self.issues_dir / f"{issue_id}.md"
        path.unlink()
        self.add(issue)
        return True

    def _parse_file(self, path: Path) -> Issue | None:
        """Parse a single issue file. Returns None if the frontmatter is malformed."""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return None
        try:
            end = text.index("\n---", 4)
        except ValueError:
            return None
        try:
            fm = yaml.safe_load(text[4:end]) or {}
        except yaml.YAMLError:
            return None
        body = text[end + 4:].strip()
        return Issue(
            id=fm.get("id", path.stem),
            type=fm.get("type", "unknown"),
            status=fm.get("status", "open"),
            title=fm.get("title", ""),
            page=fm.get("page"),
            body=body,
            created=fm.get("created", ""),
            detected_by=fm.get("detected_by", "unknown"),
            metadata=fm.get("metadata") or {},
            severity=fm.get("severity") or "minor",
        )
