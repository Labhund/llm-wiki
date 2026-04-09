from __future__ import annotations

import datetime
import re
from pathlib import Path

import yaml


def render_plan_file(
    source: str,
    title: str,
    claims: list[str],
    started: str,
) -> str:
    """Return the full text of a new inbox plan file (frontmatter + body)."""
    claims_md = "\n".join(f"- [ ] {c}" for c in claims)
    return (
        f"---\n"
        f"source: {source}\n"
        f"started: {started}\n"
        f"status: in-progress\n"
        f"sessions: 1\n"
        f"---\n\n"
        f"# {title} — Research Plan\n\n"
        f"## Claims / Ideas\n"
        f"{claims_md}\n\n"
        f"## Decisions\n\n"
        f"## Session Notes\n\n"
        f"### {started} (Session 1)\n"
    )


def plan_filename(source_path: str, started: str) -> str:
    """Derive the inbox plan filename from the source path and start date.

    Strips any leading YYYY-MM-DD- date prefix from the source stem so
    that raw/2026-04-09-vaswani.pdf with started='2026-04-10'
    produces 2026-04-10-vaswani-plan.md (not double-dated).
    """
    stem = Path(source_path).stem
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    return f"{started}-{stem}-plan.md"


def create_plan_file(
    vault_root: Path,
    source: str,
    title: str,
    claims: list[str],
) -> Path:
    """Create a scaffolded plan file in inbox/.

    Creates inbox/ if it does not exist. Does NOT git-commit — the
    daemon handler is responsible for the commit.

    Raises FileExistsError if the plan file already exists (same
    source ingested on the same day).
    """
    inbox_dir = vault_root / "inbox"
    inbox_dir.mkdir(exist_ok=True)

    started = datetime.date.today().isoformat()
    filename = plan_filename(source, started)
    plan_path = inbox_dir / filename

    if plan_path.exists():
        raise FileExistsError(
            f"Plan file already exists: {plan_path.relative_to(vault_root)}"
        )

    content = render_plan_file(source, title, claims, started)
    plan_path.write_text(content, encoding="utf-8")
    return plan_path


def read_plan_frontmatter(path: Path) -> dict:
    """Read YAML frontmatter from a plan file.

    Returns {} on any error (missing file, no frontmatter block,
    YAML parse failure).
    """
    try:
        with path.open(encoding="utf-8") as f:
            if f.readline().strip() != "---":
                return {}
            lines: list[str] = []
            for _ in range(20):
                line = f.readline()
                if not line or line.strip() == "---":
                    break
                lines.append(line)
        return yaml.safe_load("".join(lines)) or {}
    except (OSError, yaml.YAMLError):
        return {}


def count_unchecked_claims(content: str) -> int:
    """Count unchecked - [ ] items in a plan file's body."""
    return content.count("- [ ]")
