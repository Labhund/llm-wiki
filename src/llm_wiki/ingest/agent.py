from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.extractor import extract_text
from llm_wiki.ingest.page_writer import write_page
from llm_wiki.ingest.prompts import (
    compose_concept_extraction_messages,
    compose_page_content_messages,
    parse_concept_extraction,
    parse_page_content,
)

if TYPE_CHECKING:
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class ConceptPlan:
    """A concept identified from source content."""
    name: str                                   # URL-safe slug: "srna-embeddings"
    title: str                                  # Human-readable: "sRNA Embeddings"
    passages: list[str] = field(default_factory=list)


@dataclass
class IngestResult:
    """Result of ingesting one source document."""
    source_path: Path
    pages_created: list[str] = field(default_factory=list)   # concept slugs
    pages_updated: list[str] = field(default_factory=list)   # concept slugs

    @property
    def concepts_found(self) -> int:
        return len(self.pages_created) + len(self.pages_updated)


class IngestAgent:
    """Orchestrates: extract → identify concepts → write wiki pages.

    Args:
        llm:    LLMClient instance (from traverse.llm_client). All calls are
                submitted at priority="ingest". The queue wires this for future
                priority scheduling — currently FIFO.
        config: WikiConfig — uses config.vault.wiki_dir to locate wiki directory.
    """

    def __init__(self, llm: LLMClient, config: WikiConfig) -> None:
        self._llm = llm
        self._config = config

    async def ingest(self, source_path: Path, vault_root: Path) -> IngestResult:
        """Ingest one source file into the wiki.

        Args:
            source_path: Absolute path to the source file (PDF, DOCX, markdown, etc.)
            vault_root:  Root directory of the vault.

        Returns:
            IngestResult listing which pages were created vs. updated.
        """
        result = IngestResult(source_path=source_path)
        wiki_dir = vault_root / self._config.vault.wiki_dir.rstrip("/")

        # Derive the citation reference: relative to vault_root if possible
        try:
            source_ref = str(source_path.relative_to(vault_root))
        except ValueError:
            source_ref = source_path.name

        # 1. Extract text
        extraction = await extract_text(source_path)
        if not extraction.success:
            logger.warning(
                "Extraction failed for %s: %s", source_path, extraction.error
            )
            return result

        # 2. Identify concepts
        budget = self._config.budgets.default_ingest
        messages = compose_concept_extraction_messages(
            source_text=extraction.content,
            source_ref=source_ref,
            budget=budget,
        )
        response = await self._llm.complete(messages, temperature=0.3, priority="ingest")
        concepts = parse_concept_extraction(response.content)

        if not concepts:
            logger.info("No concepts identified in %s", source_path)
            return result

        # 3. Generate + write one page per concept
        wiki_dir.mkdir(parents=True, exist_ok=True)
        for concept in concepts:
            page_messages = compose_page_content_messages(
                concept_title=concept.title,
                passages=concept.passages,
                source_ref=source_ref,
            )
            page_response = await self._llm.complete(
                page_messages, temperature=0.5, priority="ingest"
            )
            sections = parse_page_content(page_response.content)

            if not sections:
                logger.warning(
                    "No sections generated for concept %r from %s",
                    concept.name, source_path,
                )
                continue

            written = write_page(wiki_dir, concept.name, concept.title, sections, source_ref)
            if written.was_update:
                result.pages_updated.append(concept.name)
            else:
                result.pages_created.append(concept.name)

        return result
