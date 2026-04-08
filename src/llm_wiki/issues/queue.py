from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass, field


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

    @staticmethod
    def make_id(type: str, page: str | None, key: str) -> str:
        """Build a deterministic id from (type, page, key).

        `key` is the discriminator that uniquely identifies this specific
        instance of the issue type — e.g. the broken-link target slug, the
        missing citation path. The hash is content-addressable so the same
        problem always maps to the same file on disk.
        """
        digest = hashlib.sha256(
            f"{type}|{page or ''}|{key}".encode("utf-8")
        ).hexdigest()[:6]
        page_part = page or "vault"
        return f"{type}-{page_part}-{digest}"

    @staticmethod
    def now_iso() -> str:
        """Current time as ISO 8601 UTC. Centralized so tests can monkeypatch."""
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
