from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from llm_wiki.ingest.page_writer import PageSection

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_EVIDENCE_RE = re.compile(r"```evidence\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class ProposalPassage:
    id: str
    text: str
    claim: str
    score: float
    method: str
    verifiable: bool
    ocr_sourced: bool


@dataclass
class Proposal:
    source: str
    target_page: str
    action: str                # "create" | "update"
    proposed_by: str
    created: str
    extraction_method: str
    sections: "list[PageSection]"
    passages: list[ProposalPassage] = field(default_factory=list)
    quality_warning: str | None = None
    status: str = "pending"
    target_cluster: str = ""   # wiki/ subdirectory for new pages; "" = root


def write_proposal(
    proposals_dir: Path,
    proposal: Proposal,
    source_slug: str,
) -> Path:
    """Write proposal to proposals_dir/YYYY-MM-DD-<source>-<target>.md."""
    date = datetime.date.today().isoformat()
    filename = f"{date}-{source_slug}-{proposal.target_page}.md"
    path = proposals_dir / filename
    proposals_dir.mkdir(parents=True, exist_ok=True)

    fm: dict = {
        "type": "proposal",
        "status": proposal.status,
        "source": proposal.source,
        "target_page": proposal.target_page,
        "action": proposal.action,
        "proposed_by": proposal.proposed_by,
        "created": proposal.created,
        "extraction_method": proposal.extraction_method,
    }
    if proposal.target_cluster:
        fm["target_cluster"] = proposal.target_cluster
    if proposal.quality_warning:
        fm["quality_warning"] = proposal.quality_warning

    frontmatter = "---\n" + yaml.dump(fm, default_flow_style=False, sort_keys=True).strip() + "\n---\n\n"

    body_parts: list[str] = []
    for section in proposal.sections:
        body_parts.append(f"%% section: {section.name} %%")
        body_parts.append(f"## {section.heading}")
        body_parts.append("")
        body_parts.append(section.content)
        body_parts.append("")
    body = "\n".join(body_parts).strip()

    evidence_data = [
        {
            "id": p.id,
            "text": p.text,
            "claim": p.claim,
            "score": p.score,
            "method": p.method,
            "verifiable": p.verifiable,
            "ocr_sourced": p.ocr_sourced,
        }
        for p in proposal.passages
    ]
    evidence_block = "\n\n```evidence\n" + json.dumps(evidence_data, indent=2) + "\n```\n"

    path.write_text(frontmatter + body + evidence_block, encoding="utf-8")
    return path


def read_proposal_meta(path: Path) -> dict:
    """Return frontmatter dict from a proposal file, or {} on failure."""
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def read_proposal_body(path: Path) -> str:
    """Return section body stripped of frontmatter and evidence block."""
    raw = path.read_text(encoding="utf-8")
    fm = _FRONTMATTER_RE.match(raw)
    body = raw[fm.end():] if fm else raw
    ev = _EVIDENCE_RE.search(body)
    if ev:
        body = body[: ev.start()]
    return body.strip()


def update_proposal_status(path: Path, status: str) -> None:
    """Rewrite the status field in frontmatter in-place."""
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return
    fm["status"] = status
    new_fm = "---\n" + yaml.dump(fm, default_flow_style=False, sort_keys=True).strip() + "\n---\n\n"
    path.write_text(new_fm + raw[m.end():], encoding="utf-8")


def list_pending_proposals(proposals_dir: Path) -> list[Path]:
    """Return sorted paths of all pending proposals in proposals_dir."""
    if not proposals_dir.is_dir():
        return []
    return sorted(
        p for p in proposals_dir.glob("*.md")
        if p.is_file() and read_proposal_meta(p).get("status") == "pending"
    )


def find_wiki_page(wiki_dir: Path, slug: str) -> Path | None:
    """Recursively find the page file for *slug* under wiki_dir.

    Supports nested cluster directories (e.g. wiki/structural-biology/boltz-2.md).
    Returns None if the page does not exist.
    """
    for p in wiki_dir.rglob(f"{slug}.md"):
        if not any(part.startswith(".") for part in p.relative_to(wiki_dir).parts):
            return p
    return None


def cluster_dirs(wiki_dir: Path) -> list[str]:
    """Return sorted list of existing cluster subdirectory names under wiki_dir."""
    if not wiki_dir.is_dir():
        return []
    return sorted(
        d.name for d in wiki_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
