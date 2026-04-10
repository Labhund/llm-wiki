from __future__ import annotations

import datetime
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import TYPE_CHECKING

from llm_wiki.adversary.claim_extractor import Claim, extract_claims
from llm_wiki.adversary.prompts import (
    Verdict,
    compose_verification_messages,
    parse_verification,
)
from llm_wiki.adversary.sampling import sample_claims
from llm_wiki.config import WikiConfig
from llm_wiki.ingest.extractor import extract_text
from llm_wiki.issues.queue import Issue, IssueQueue
from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
from llm_wiki.talk.discovery import ensure_talk_marker
from llm_wiki.talk.page import TalkEntry, TalkPage
from llm_wiki.vault import Vault, _state_dir_for

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class AdversaryResult:
    claims_checked: int = 0
    validated: list[str] = field(default_factory=list)         # claim ids
    failed: list[str] = field(default_factory=list)            # claim ids
    issues_filed: list[str] = field(default_factory=list)      # issue ids
    talk_posts: list[str] = field(default_factory=list)        # page slugs


class AdversaryAgent:
    """Verifies sampled wiki claims against their cited raw sources.

    Verdict pathways:
      - validated   → update ManifestOverrides.last_corroborated for the page
      - contradicted/unsupported → file a 'claim-failed' issue
      - ambiguous   → append an @adversary entry to the page's talk page,
                      ensure the parent has a discovery marker
      - raw extract fails → log + skip (auditor handles broken-citation)
    """

    def __init__(
        self,
        vault: Vault,
        vault_root: Path,
        llm: "LLMClient",
        queue: IssueQueue,
        config: WikiConfig,
        rng: Random | None = None,
    ) -> None:
        self._vault = vault
        self._vault_root = vault_root
        self._llm = llm
        self._queue = queue
        self._config = config
        self._rng = rng or Random()
        self._state_dir = _state_dir_for(vault_root)
        self._overrides_path = self._state_dir / "manifest_overrides.json"
        self._wiki_dir = vault_root / config.vault.wiki_dir.rstrip("/")

    def _load_last_run_ts(self) -> float | None:
        """Return the stored Unix timestamp of the last adversary run, or None."""
        path = self._state_dir / "adversary_last_run.txt"
        try:
            return float(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return None

    def _record_last_run_ts(self) -> None:
        """Atomically write the current time as the last-run timestamp."""
        path = self._state_dir / "adversary_last_run.txt"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=self._state_dir, prefix=".adversary-ts-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(time.time()))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _vault_unchanged_since_last_run(self) -> bool:
        """Return True if no file in wiki/ or raw/ has changed since the last run.

        Always returns False on the first run (no stored timestamp).
        Also returns False when adversary_force_recheck_days have elapsed since
        the last run — ensuring periodic re-verification even on a static vault.
        Skips hidden files (names starting with '.').
        """
        ts = self._load_last_run_ts()
        if ts is None:
            return False
        force_days = self._config.maintenance.adversary_force_recheck_days
        if (time.time() - ts) > force_days * 86400:
            return False
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        raw_dir  = self._vault_root / self._config.vault.raw_dir.rstrip("/")
        for search_dir in (wiki_dir, raw_dir):
            if not search_dir.exists():
                continue
            for f in search_dir.rglob("*"):
                if f.is_file() and not f.name.startswith(".") and f.stat().st_mtime > ts:
                    return False
        return True

    async def run(self) -> AdversaryResult:
        result = AdversaryResult()
        entries = self._vault.manifest_entries()
        if not entries:
            return result

        if self._vault_unchanged_since_last_run():
            logger.info("Adversary: vault unchanged since last run, skipping")
            return result

        raw_prefix = self._config.vault.raw_dir.rstrip("/")

        # 1. Extract claims from every non-synthesis page
        all_claims: list[Claim] = []
        for name in entries:
            page = self._vault.read_page(name)
            if page is None:
                continue
            if page.frontmatter.get("type") == "synthesis":
                continue  # resonance agent handles synthesis pages; adversary skips them
            all_claims.extend(extract_claims(page, raw_dir=raw_prefix))

        if not all_claims:
            self._record_last_run_ts()
            return result

        # 2. Sample
        n = self._config.maintenance.adversary_claims_per_run
        now = datetime.datetime.now(datetime.timezone.utc)

        # Build unread sources set for adversary upweighting
        unread_sources: set[str] = set()
        raw_dir = self._vault_root / raw_prefix
        if raw_dir.is_dir():
            from llm_wiki.ingest.source_meta import read_frontmatter
            for md_file in raw_dir.glob("*.md"):
                fm = read_frontmatter(md_file)
                if fm.get("reading_status") == "unread":
                    # Add both the companion path and the likely binary path
                    unread_sources.add(f"{raw_prefix}/{md_file.name}")
                    for ext in (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"):
                        binary = md_file.with_suffix(ext)
                        if binary.exists():
                            unread_sources.add(f"{raw_prefix}/{binary.name}")

        sampled = sample_claims(
            all_claims, entries, n=n, rng=self._rng, now=now,
            unread_sources=unread_sources,
            unread_weight=self._config.maintenance.adversary_unread_weight,
        )

        # 3. Verify each
        for claim in sampled:
            await self._process_claim(claim, result, now)

        self._record_last_run_ts()
        return result

    async def _process_claim(
        self,
        claim: Claim,
        result: AdversaryResult,
        now: datetime.datetime,
    ) -> None:
        # Resolve raw source
        raw_path = self._vault_root / claim.citation
        if not raw_path.exists():
            logger.info("Adversary: raw source missing for %s, skipping", claim.id)
            return

        extraction = await extract_text(raw_path)
        if not extraction.success:
            logger.info(
                "Adversary: extraction failed for %s (%s), skipping",
                raw_path, extraction.error,
            )
            return

        result.claims_checked += 1

        messages = compose_verification_messages(claim, raw_text=extraction.content)
        try:
            response = await self._llm.complete(
                messages, temperature=0.2, priority="maintenance",
                label=f"adversary:verify:{claim.page}",
            )
        except Exception:
            logger.exception("Adversary: LLM call failed for claim %s", claim.id)
            return

        verdict, confidence, explanation = parse_verification(response.content)
        if verdict is None:
            logger.info("Adversary: unparseable verdict for claim %s", claim.id)
            return

        if verdict == "validated":
            self._handle_validated(claim, result, now)
        elif verdict in ("contradicted", "unsupported"):
            self._handle_failed(claim, verdict, confidence, explanation, result)
        else:  # ambiguous
            self._handle_ambiguous(claim, explanation, result, now)

    def _handle_validated(
        self,
        claim: Claim,
        result: AdversaryResult,
        now: datetime.datetime,
    ) -> None:
        overrides = ManifestOverrides.load(self._overrides_path)
        existing = overrides.get(claim.page) or PageOverride()
        existing.last_corroborated = now.isoformat()
        overrides.set(claim.page, existing)
        overrides.save()
        result.validated.append(claim.id)

    def _handle_failed(
        self,
        claim: Claim,
        verdict: Verdict,
        confidence: float,
        explanation: str,
        result: AdversaryResult,
    ) -> None:
        issue = Issue(
            id=Issue.make_id("claim-failed", claim.page, claim.id),
            type="claim-failed",
            status="open",
            severity="critical",
            title=f"Claim on '{claim.page}' is {verdict}",
            page=claim.page,
            body=(
                f"The adversary checked the claim:\n\n> {claim.text}\n\n"
                f"against [[{claim.citation}]] and ruled it **{verdict}** "
                f"(confidence {confidence:.2f}).\n\n"
                f"Explanation: {explanation}"
            ),
            created=Issue.now_iso(),
            detected_by="adversary",
            metadata={
                "claim_id": claim.id,
                "section": claim.section,
                "citation": claim.citation,
                "verdict": verdict,
                "confidence": confidence,
            },
        )
        _, was_new = self._queue.add(issue)
        result.failed.append(claim.id)
        if was_new:
            result.issues_filed.append(issue.id)

    def _handle_ambiguous(
        self,
        claim: Claim,
        explanation: str,
        result: AdversaryResult,
        now: datetime.datetime,
    ) -> None:
        page_path = self._wiki_dir / f"{claim.page}.md"
        if not page_path.exists():
            logger.info("Adversary: parent page %s missing, cannot post talk entry", page_path)
            return

        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            index=0,
            timestamp=now.isoformat(),
            author="@adversary",
            body=(
                f"Checked claim against [[{claim.citation}]] — verdict is ambiguous.\n\n"
                f"> {claim.text}\n\n"
                f"{explanation}"
            ),
            severity="critical",
            type="adversary-finding",
        )
        talk.append(entry)
        ensure_talk_marker(page_path)
        result.talk_posts.append(claim.page)
