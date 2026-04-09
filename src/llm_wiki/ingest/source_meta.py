from __future__ import annotations

import datetime
from pathlib import Path

import yaml

_SUPPORTED_BINARY = frozenset({
    ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"
})


def read_frontmatter(path: Path) -> dict:
    """Read YAML frontmatter from a file, stopping at the closing ---.

    Never reads the body. Returns {} if no frontmatter block is found,
    the file is missing, or YAML parsing fails.
    """
    try:
        with path.open(encoding="utf-8") as f:
            if f.readline().strip() != "---":
                return {}
            lines: list[str] = []
            for _ in range(100):  # safety cap — standard frontmatter is < 20 lines
                line = f.readline()
                if not line or line.strip() == "---":
                    break
                lines.append(line)
        return yaml.safe_load("".join(lines)) or {}
    except (OSError, yaml.YAMLError):
        return {}


def write_frontmatter(path: Path, updates: dict) -> None:
    """Merge updates into the frontmatter of path. Body preserved byte-for-byte."""
    content = path.read_text(encoding="utf-8")
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            fm_text = content[3:end].strip()
            body = content[end + 4:]  # everything after the closing \n---
        else:
            fm_text = ""
            body = content
    else:
        fm_text = ""
        body = content
    fm: dict = yaml.safe_load(fm_text) if fm_text else {}
    fm = fm or {}
    fm.update(updates)
    new_fm = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    if body and not body.startswith("\n"):
        body = "\n" + body
    path.write_text(f"---\n{new_fm}\n---{body}", encoding="utf-8")


def init_companion(
    source_path: Path,
    vault_root: Path,
    source_type: str = "paper",
) -> Path | None:
    """Create a frontmatter-only companion .md for a binary source in raw/.

    Returns the new companion Path only when freshly created. Returns None
    on ALL no-op paths: source is .md/.markdown, not under vault_root/raw/,
    or companion already exists. Callers must guard body-write with
    ``if companion:``.
    """
    if source_path.suffix.lower() in (".md", ".markdown"):
        return None
    raw_dir = vault_root / "raw"
    try:
        source_path.relative_to(raw_dir)
    except ValueError:
        return None
    companion = source_path.with_suffix(".md")
    if companion.exists():
        return None
    today = datetime.date.today().isoformat()
    companion.write_text(
        f"---\nreading_status: unread\ningested: {today}\nsource_type: {source_type}\n---\n",
        encoding="utf-8",
    )
    return companion


def write_companion_body(path: Path, text: str) -> None:
    """Append extracted text as body to a frontmatter-only companion file.

    Called once immediately after init_companion. Assumes the file
    currently ends at the closing ``---``. The body is separated from
    the frontmatter by a blank line.
    """
    current = path.read_text(encoding="utf-8")
    path.write_text(current + "\n" + text, encoding="utf-8")
