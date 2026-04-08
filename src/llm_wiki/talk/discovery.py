from __future__ import annotations

from pathlib import Path


def ensure_talk_marker(page_path: Path) -> bool:
    """Append a %% talk: [[<slug>.talk]] %% marker to a wiki page if missing.

    The marker is invisible in Obsidian's preview mode but visible in source
    mode and parseable by the daemon. Idempotent: returns False if the
    marker is already present.
    """
    slug = page_path.stem
    marker = f"%% talk: [[{slug}.talk]] %%"
    text = page_path.read_text(encoding="utf-8")
    if marker in text:
        return False
    page_path.write_text(text.rstrip() + f"\n\n{marker}\n", encoding="utf-8")
    return True
