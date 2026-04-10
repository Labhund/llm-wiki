from __future__ import annotations

import datetime
import logging
import re as _re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from llm_wiki.config import WikiConfig
from llm_wiki.ingest.chunker import chunk_text
from llm_wiki.ingest.extractor import extract_text
from llm_wiki.ingest.grounding import ground_passage
from llm_wiki.ingest.page_writer import write_page
from llm_wiki.ingest.proposals import (
    Proposal,
    ProposalPassage,
    cluster_dirs as _get_cluster_dirs,
    write_proposal,
)
from llm_wiki.ingest.prompts import (
    compose_concept_extraction_messages,
    compose_content_synthesis_messages,
    compose_deep_read_synthesis_messages,
    compose_digest_chunk_messages,
    compose_overview_messages,
    compose_page_content_messages,
    compose_passage_collection_messages,
    parse_concept_extraction,
    parse_content_synthesis,
    parse_overview_extraction,
    parse_page_content,
    parse_passage_collection,
)

if TYPE_CHECKING:
    from llm_wiki.daemon.writes import PageWriteService
    from llm_wiki.traverse.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class ConceptPlan:
    """A concept identified from source content."""
    name: str                                   # URL-safe slug: "srna-embeddings"
    title: str                                  # Human-readable: "sRNA Embeddings"
    passages: list[str] = field(default_factory=list)
    action: str = "create"                      # "create" | "update"
    section_names: list[str] = field(default_factory=list)
    cluster: str = ""                           # target wiki/ subdirectory; "" = root


@dataclass
class ConceptPreview:
    """Preview of a concept that would be created/updated (dry-run only)."""
    name: str
    title: str
    is_update: bool
    passages: list[str] = field(default_factory=list)
    sections: list = field(default_factory=list)


@dataclass
class IngestResult:
    """Result of ingesting one source document."""
    source_path: Path
    pages_created: list[str] = field(default_factory=list)   # concept slugs
    pages_updated: list[str] = field(default_factory=list)   # concept slugs
    dry_run: bool = False
    concepts_planned: list[ConceptPreview] = field(default_factory=list)
    source_chars: int = 0
    extraction_warning: str | None = None    # set when extraction quality is suspect

    @property
    def concepts_found(self) -> int:
        if self.dry_run:
            return len(self.concepts_planned)
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

    async def ingest(
        self,
        source_path: Path,
        vault_root: Path,
        *,
        author: str = "cli",
        connection_id: str = "cli",
        write_service: "PageWriteService | None" = None,
        dry_run: bool = False,
        source_type: str = "paper",
        on_progress: "Callable[[dict], Awaitable[None]] | None" = None,
    ) -> IngestResult:
        """Ingest one source file into the wiki.

        When `dry_run` is True, runs extraction and concept/page generation
        but skips all filesystem writes. The result contains `concepts_planned`
        with previews of what would be created/updated.

        When `write_service` is provided, all page creates/appends are routed
        through it so they journal under the caller's session and land in the
        commit pipeline. When `write_service` is None, falls back to the
        legacy direct-write path (used by older code paths only — new code
        should always pass write_service).
        """
        from llm_wiki.ingest.source_meta import init_companion, write_companion_body
        companion = init_companion(source_path, vault_root, source_type=source_type)

        wiki_dir = vault_root / self._config.vault.wiki_dir.rstrip("/")
        result = IngestResult(
            source_path=source_path,
            dry_run=dry_run,
        )

        try:
            source_ref = str(source_path.relative_to(vault_root))
        except ValueError:
            source_ref = source_path.name

        if on_progress:
            await on_progress({"stage": "extracting"})

        extraction = await extract_text(
            source_path,
            ingest_config=self._config.ingest,
        )
        if not extraction.success:
            logger.warning(
                "Extraction failed for %s: %s", source_path, extraction.error
            )
            return result

        result.source_chars = len(extraction.content)
        if extraction.quality_warning:
            result.extraction_warning = extraction.quality_warning

        if companion:
            try:
                write_companion_body(companion, extraction.content)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to write companion body for %s: %s", source_path, e)

        budget = self._config.budgets.default_ingest
        messages = compose_concept_extraction_messages(
            source_text=extraction.content,
            source_ref=source_ref,
            budget=budget,
        )
        response = await self._llm.complete(
            messages, temperature=0.3, priority="ingest",
            label=f"ingest:extract:{source_path.name}",
        )
        concepts = parse_concept_extraction(response.content)

        if on_progress:
            await on_progress({"stage": "concepts_found", "count": len(concepts)})

        if not concepts:
            logger.info("No concepts identified in %s", source_path)
            return result

        # Dry-run: stop here — no page-content generation
        if dry_run:
            for concept in concepts:
                page_path = wiki_dir / f"{concept.name}.md"
                result.concepts_planned.append(ConceptPreview(
                    name=concept.name,
                    title=concept.title,
                    is_update=page_path.exists(),
                    passages=concept.passages,
                    sections=[],
                ))
            return result

        # Live ingest: generate page content and write
        # Note: enumerate used because Task 3 (on_progress) needs the index
        for i, concept in enumerate(concepts):
            page_messages = compose_page_content_messages(
                concept_title=concept.title,
                passages=concept.passages,
                source_ref=source_ref,
            )
            page_response = await self._llm.complete(
                page_messages, temperature=0.5, priority="ingest",
                label=f"ingest:write:{concept.name}",
            )
            sections = parse_page_content(page_response.content)
            if not sections:
                logger.warning(
                    "No sections generated for concept %r from %s",
                    concept.name, source_path,
                )
                continue

            created_before = len(result.pages_created)

            if write_service is not None:
                await self._write_via_service(
                    write_service, wiki_dir, concept, sections, source_ref,
                    author=author, connection_id=connection_id, result=result,
                )
            else:
                # Legacy direct-write path
                # NOTE: this path uses compose_page_content_messages / parse_page_content,
                # which does not produce a summary.  cluster comes from parse_concept_extraction,
                # which also does not request cluster from the LLM (the extraction prompt omits it),
                # so concept.cluster is always "" here.  summary is left empty and may be
                # backfilled later by the librarian.  TODO: migrate callers to write_service.
                wiki_dir.mkdir(parents=True, exist_ok=True)
                written = write_page(
                    wiki_dir, concept.name, concept.title, sections, source_ref,
                    cluster=concept.cluster,
                    summary="",  # not available in legacy pipeline; backfilled by librarian
                )
                if written.was_update:
                    result.pages_updated.append(concept.name)
                else:
                    result.pages_created.append(concept.name)

            if on_progress:
                action = "created" if len(result.pages_created) > created_before else "updated"
                await on_progress({
                    "stage": "concept_done",
                    "name": concept.name,
                    "title": concept.title,
                    "action": action,
                    "num": i + 1,
                    "total": len(concepts),
                })

        # Resonance matching post-step (gated by config)
        # pages_created only — resonance seeds new pages, not updates to existing ones
        if self._config.maintenance.resonance_matching and result.pages_created:
            try:
                # Lazy import: keeps resonance module optional; a broken import never aborts ingest
                from llm_wiki.resonance.agent import ResonanceAgent
                from llm_wiki.vault import Vault
                vault = Vault.scan(vault_root, self._config)
                resonance_agent = ResonanceAgent(
                    vault=vault,
                    vault_root=vault_root,
                    llm=self._llm,
                    config=self._config,
                )
                await resonance_agent.run_for_pages(result.pages_created)
            except Exception:
                logger.exception("Resonance post-step failed — ingest result unaffected")

        return result

    async def _write_via_service(
        self,
        service: "PageWriteService",
        wiki_dir: Path,
        concept: ConceptPlan,
        sections: list,
        source_ref: str,
        *,
        author: str,
        connection_id: str,
        result: IngestResult,
    ) -> None:
        """Route a concept through the supervised write surface."""
        page_path = wiki_dir / f"{concept.name}.md"
        body = self._sections_to_body(sections)
        if not page_path.exists():
            wr = await service.create(
                title=concept.title,
                body=body,
                citations=[source_ref],
                author=author,
                connection_id=connection_id,
                intent=f"ingest from {source_ref}",
                force=True,  # ingest must not be blocked by near-match heuristics
            )
            if wr.status == "ok":
                result.pages_created.append(concept.name)
        else:
            # Append a new section labeled with the source
            wr = await service.append(
                page=concept.name,
                section_heading=f"From {source_ref}",
                body=body,
                citations=[source_ref],
                author=author,
                connection_id=connection_id,
                intent=f"ingest update from {source_ref}",
            )
            if wr.status == "ok":
                result.pages_updated.append(concept.name)

    async def ingest_as_proposals(
        self,
        source_path: Path,
        vault_root: Path,
        proposals_dir: "Path | None",
        manifest_lines: list[str],
        *,
        author: str = "cli",
        dry_run: bool = False,
        connection_id: str = "cli",
        write_service: "PageWriteService | None" = None,
        on_progress: "Callable[[dict], Awaitable[None]] | None" = None,
    ) -> IngestResult:
        """Multi-chunk wiki-aware ingest: deep-read synthesis per concept.

        Output mode is determined by the combination of arguments:
        - ``write_service`` provided → write pages directly (streaming live ingest)
        - ``proposals_dir`` provided → write proposal files for librarian review
        - Both provided → write_service takes precedence
        - Neither → concepts are identified but nothing is written (dry_run implies this)

        Args:
            source_path:    Absolute path to source (must be inside vault_root/raw/).
            vault_root:     Vault root directory.
            proposals_dir:  Where to write proposal files (inbox/proposals/).
                            Pass None when using write_service for direct writes.
            manifest_lines: Existing wiki manifest, one "slug  title" line each.
            author:         Who triggered the ingest (for proposal/page metadata).
            dry_run:        Stop after overview pass — returns concept plan only.
            connection_id:  Session ID forwarded to write_service.
            write_service:  If provided, write pages directly via supervised surface.
            on_progress:    Async callback for streaming progress frames.
        """
        from llm_wiki.ingest.source_meta import init_companion, write_companion_body

        result = IngestResult(source_path=source_path, dry_run=False)

        try:
            source_ref = str(source_path.relative_to(vault_root))
        except ValueError:
            source_ref = source_path.name

        if on_progress:
            await on_progress({"stage": "extracting"})

        extraction = await extract_text(source_path, ingest_config=self._config.ingest)
        if not extraction.success:
            logger.warning("Extraction failed for %s: %s", source_path, extraction.error)
            return result

        result.source_chars = len(extraction.content)
        if extraction.quality_warning:
            result.extraction_warning = extraction.quality_warning

        companion = init_companion(source_path, vault_root)
        if companion:
            try:
                write_companion_body(companion, extraction.content)
            except Exception as exc:
                logger.warning("Failed to write companion for %s: %s", source_path, exc)

        chunks = chunk_text(
            extraction.content,
            chunk_tokens=self._config.ingest.chunk_tokens,
            overlap=self._config.ingest.chunk_overlap,
        )
        if not chunks:
            return result

        wiki_dir = vault_root / self._config.vault.wiki_dir.rstrip("/")
        existing_clusters = _get_cluster_dirs(wiki_dir)

        # Overview pass on chunk 0
        overview_msgs = compose_overview_messages(
            chunk_text=chunks[0],
            manifest_lines=manifest_lines,
            source_ref=source_ref,
            cluster_dir_names=existing_clusters,
        )
        overview_resp = await self._llm.complete(
            overview_msgs, temperature=0.2, priority="ingest",
            label=f"ingest:overview:{source_path.name}",
        )
        concepts = parse_overview_extraction(overview_resp.content)

        if not concepts:
            logger.info("No concepts identified in %s", source_path)
            return result

        if on_progress:
            await on_progress({"stage": "concepts_found", "count": len(concepts)})

        # Dry-run: stop after overview — one LLM call on chunk[0], same prompt as live ingest
        if dry_run:
            result.dry_run = True
            for concept in concepts:
                page_path = wiki_dir / f"{concept.name}.md"
                result.concepts_planned.append(ConceptPreview(
                    name=concept.name,
                    title=concept.title,
                    is_update=concept.action == "update" or page_path.exists(),
                    passages=[],
                    sections=concept.section_names,
                ))
            return result

        if on_progress:
            await on_progress({"stage": "building_context", "total_chunks": len(chunks)})

        # Build paper context: full text if it fits, rolling digest if too large
        paper_context = await self._build_paper_context(
            extraction.content,
            chunks=chunks,
            source_name=source_path.name,
        )

        source_slug = _re.sub(r"[^a-z0-9-]", "-", source_path.stem.lower()).strip("-")
        ocr_sourced = extraction.extraction_method == "image_ocr"
        synth_temp = self._config.ingest.synthesis_temperature

        # Deep-read synthesis + write per concept
        for i, concept in enumerate(concepts):
            synth_msgs = compose_deep_read_synthesis_messages(
                concept=concept,
                paper_context=paper_context,
                source_ref=source_ref,
                manifest_lines=manifest_lines,
                batch_concepts=concepts,
            )
            synth_resp = await self._llm.complete(
                synth_msgs, temperature=synth_temp, priority="ingest",
                label=f"ingest:synthesize:{concept.name}",
            )
            synthesis = parse_content_synthesis(synth_resp.content)
            sections = synthesis.sections
            if not sections:
                logger.warning("No sections generated for %r — skipping", concept.name)
                continue

            if write_service is not None:
                # Direct write via supervised write surface (streaming live ingest)
                await self._write_via_service(
                    write_service, wiki_dir, concept, sections, source_ref,
                    author=author, connection_id=connection_id, result=result,
                )
                if on_progress:
                    action = "created" if concept.name in result.pages_created else "updated"
                    await on_progress({
                        "stage": "concept_done",
                        "name": concept.name,
                        "title": concept.title,
                        "action": action,
                        "num": i + 1,
                        "total": len(concepts),
                    })
            else:
                # Proposal write path — include grounding evidence for librarian review
                proposal_passages: list[ProposalPassage] = []
                for idx, section in enumerate(s for s in sections if s.name != "references"):
                    first_sentence = section.content.split(".")[0].strip() if section.content else ""
                    if not first_sentence:
                        continue
                    gr = ground_passage(first_sentence, extraction.content, ocr_sourced=ocr_sourced)
                    proposal_passages.append(ProposalPassage(
                        id=f"p{idx + 1}",
                        text=first_sentence,
                        claim=first_sentence[:120],
                        score=gr.score,
                        method=gr.method,
                        verifiable=gr.verifiable,
                        ocr_sourced=gr.ocr_sourced,
                    ))

                proposal = Proposal(
                    source=source_ref,
                    target_page=concept.name,
                    action=concept.action,
                    proposed_by=author,
                    created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    extraction_method=extraction.extraction_method,
                    sections=sections,
                    passages=proposal_passages,
                    quality_warning=result.extraction_warning,
                    target_cluster=concept.cluster,
                )
                if proposals_dir is not None:
                    write_proposal(proposals_dir, proposal, source_slug=source_slug)

                if concept.action == "create":
                    result.pages_created.append(concept.name)
                else:
                    result.pages_updated.append(concept.name)

        return result

    async def _build_paper_context(
        self,
        full_text: str,
        chunks: list[str],
        source_name: str,
    ) -> str:
        """Return the paper context to pass to synthesis.

        If the full text is within the configured `full_context_chars` limit,
        return it directly — the synthesis model reads the whole thing.

        Otherwise, run a rolling digest loop: read each chunk sequentially,
        accumulating a structured summary of the whole paper. The digest
        is used for synthesis instead of the raw text.
        """
        if len(full_text) <= self._config.ingest.full_context_chars:
            logger.info(
                "Paper fits in context (%d chars) — passing full text to synthesis",
                len(full_text),
            )
            return full_text

        logger.info(
            "Paper too large (%d chars > %d limit) — building rolling digest over %d chunks",
            len(full_text), self._config.ingest.full_context_chars, len(chunks),
        )
        digest = ""
        for i, chunk in enumerate(chunks):
            msgs = compose_digest_chunk_messages(
                chunk_text=chunk,
                running_digest=digest,
                chunk_index=i,
                total_chunks=len(chunks),
            )
            resp = await self._llm.complete(
                msgs, temperature=0.2, priority="ingest",
                label=f"ingest:digest:{source_name}:{i}",
            )
            digest = resp.content.strip()
            logger.debug("Digest after chunk %d/%d: %d chars", i + 1, len(chunks), len(digest))

        return digest

    @staticmethod
    def _sections_to_body(sections: list) -> str:
        parts = []
        for s in sections:
            parts.append(f"%% section: {s.name} %%")
            parts.append(f"## {s.heading}")
            parts.append("")
            parts.append(s.content)
            parts.append("")
        return "\n".join(parts).strip()
