from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.adversary.claim_extractor import Claim, extract_claims
from llm_wiki.config import WikiConfig
from llm_wiki.resonance.prompts import ResonanceVerdict, compose_resonance_messages, parse_resonance
from llm_wiki.talk.discovery import ensure_talk_marker
from llm_wiki.talk.page import TalkEntry, TalkPage

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient
    from llm_wiki.vault import Vault

logger = logging.getLogger(__name__)

# Cap claims processed per new page to prevent runaway LLM spend.
_MAX_CLAIMS_PER_PAGE = 5


@dataclass
class ResonanceResult:
    pages_checked: int = 0
    resonance_posts: list[tuple[str, str]] = field(default_factory=list)
    # Each entry: (new_page_slug, existing_page_slug)


class ResonanceAgent:
    """Post-ingest resonance matching.

    For each newly created page, extracts claims and searches for related
    existing pages via tantivy. Asks the LLM whether each (new claim,
    existing claim) pair resonates. Posts a `resonance` talk entry on the
    existing page when resonance is confirmed.

    LLM calls run at priority='maintenance' so they never compete with
    user-facing queries.
    """

    def __init__(
        self,
        vault: "Vault",
        vault_root: Path,
        llm: "LLMClient",
        config: WikiConfig,
    ) -> None:
        self._vault = vault
        self._vault_root = vault_root
        self._llm = llm
        self._config = config
        self._wiki_dir = vault_root / config.vault.wiki_dir.rstrip("/")

    async def run_for_pages(self, new_page_slugs: list[str]) -> ResonanceResult:
        """Compare claims from new pages against existing wiki claims.

        Args:
            new_page_slugs: Slugs of pages just created by wiki_ingest.
        """
        result = ResonanceResult()
        if not new_page_slugs:
            return result

        new_slugs_set = set(new_page_slugs)
        n = self._config.maintenance.resonance_candidates_per_claim

        for slug in new_page_slugs:
            page = self._vault.read_page(slug)
            if page is None:
                continue
            claims = extract_claims(page)
            if not claims:
                continue

            for claim in claims[:_MAX_CLAIMS_PER_PAGE]:
                await self._check_claim(claim, new_slugs_set, n, result)

        return result

    async def _check_claim(
        self,
        claim: Claim,
        new_slugs_set: set[str],
        n: int,
        result: ResonanceResult,
    ) -> None:
        query = claim.text[:120]
        search_results = self._vault.search(query, limit=n + len(new_slugs_set))

        candidates = [r for r in search_results if r.name not in new_slugs_set][:n]

        for search_result in candidates:
            candidate_page = self._vault.read_page(search_result.name)
            if candidate_page is None:
                continue

            candidate_claims = extract_claims(candidate_page)
            if not candidate_claims:
                continue

            # Use the first claim as a representative for the candidate page.
            # This is an intentional simplification — the search found this page as
            # topically related; we compare against its primary claim rather than
            # scanning all claims.
            existing_claim = candidate_claims[0]

            messages = compose_resonance_messages(
                new_claim=claim.text,
                new_source=claim.citation,
                existing_claim=existing_claim.text,
                existing_page=candidate_page.path.stem,
            )

            try:
                response = await self._llm.complete(
                    messages, temperature=0.2, priority="maintenance"
                )
            except Exception:
                logger.exception(
                    "Resonance: LLM call failed for claim %s vs page %s",
                    claim.id, candidate_page.path.stem,
                )
                continue

            verdict = parse_resonance(response.content)
            result.pages_checked += 1

            if not verdict.resonates:
                continue

            self._post_resonance_entry(claim, existing_claim, verdict, result)

    def _post_resonance_entry(
        self,
        new_claim: Claim,
        existing_claim: Claim,
        verdict: ResonanceVerdict,
        result: ResonanceResult,
    ) -> None:
        page_path = self._wiki_dir / f"{existing_claim.page}.md"
        if not page_path.exists():
            logger.info(
                "Resonance: parent page %s missing, cannot post entry", page_path
            )
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        relation = verdict.relation or "relates to"
        note = verdict.note or ""

        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            index=0,
            timestamp=now.isoformat(),
            author="@resonance",
            body=(
                f"New source [[{new_claim.citation}]] may {relation} this claim.\n\n"
                f"> {new_claim.text}\n\n"
                f"{note}"
            ),
            severity="moderate",
            type="resonance",
        )
        talk.append(entry)
        ensure_talk_marker(page_path)
        result.resonance_posts.append((new_claim.page, existing_claim.page))
