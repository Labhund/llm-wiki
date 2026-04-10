from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_wiki.ingest.agent import ConceptPlan
    from llm_wiki.ingest.page_writer import PageSection


@dataclass
class SynthesisResult:
    """Result of parsing a content-synthesis LLM response."""
    sections: "list[PageSection]" = field(default_factory=list)
    summary: str = ""


_CONCEPT_EXTRACTION_SYSTEM = """\
You are analyzing a document to identify its main concepts and entities for a \
knowledge wiki.

## Task

Identify the key concepts, techniques, and entities from the source text. For each:
1. Assign a URL-safe slug (lowercase, hyphens only — e.g. "srna-embeddings")
2. Give a human-readable title (e.g. "sRNA Embeddings")
3. Extract 1-3 short passages from the text that discuss this concept

Focus on concepts with enough substance to warrant their own wiki page. \
Exclude trivial mentions.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. No text outside the JSON.

{
  "concepts": [
    {
      "name": "concept-slug",
      "title": "Concept Title",
      "passages": ["relevant excerpt from source text"]
    }
  ]
}"""

_PAGE_CONTENT_SYSTEM = """\
You are writing content for a wiki page about a specific concept, based on a \
source document.

## Citation Rules (Non-Negotiable)

Use numbered footnotes — `[^N]` inline, defined in a References section:
- `[^1]` = [[{source_ref}]] (the primary source document)
- Every factual claim MUST end with `[^1]`. No exceptions.
- Do NOT embed [[raw/...]] links in body text — only in the References section.

## Wikilink Rules

Named concepts, models, methods, datasets, proteins, databases, and proper nouns \
get `[[slug]]` wikilinks:
- Known wiki slug → use it exactly.
- Named concept not yet in wiki (e.g. "Free Energy Perturbation", "TYK2") → invent \
  a kebab-case slug (e.g. `[[free-energy-perturbation]]`, `[[tyk2]]`). Red links are fine.
- Generic terms with no standalone identity → plain text, no brackets.

## Content Rules

- Do NOT interpret beyond what the source states.
- "X correlates with Y", not "X causes Y".
- Be concise. Every sentence earns its place.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. Add "references" as the LAST section:

{{
  "sections": [
    {{
      "name": "section-slug",
      "heading": "Section Heading",
      "content": "Markdown with [[wikilinks]] and [^1] footnote citations."
    }},
    {{
      "name": "references",
      "heading": "References",
      "content": "[^1]: [[{source_ref}]]"
    }}
  ]
}}"""


def compose_concept_extraction_messages(
    source_text: str,
    source_ref: str,
    budget: int = 8000,
) -> list[dict[str, str]]:
    """Build the message list for concept extraction."""
    truncated = source_text[: budget * 4]  # rough chars-per-token estimate
    user = (
        f"## Source Reference\n{source_ref}\n\n"
        f"## Source Text\n{truncated}"
    )
    return [
        {"role": "system", "content": _CONCEPT_EXTRACTION_SYSTEM},
        {"role": "user", "content": user},
    ]


def compose_page_content_messages(
    concept_title: str,
    passages: list[str],
    source_ref: str,
) -> list[dict[str, str]]:
    """Build the message list for page content generation."""
    system = _PAGE_CONTENT_SYSTEM.format(source_ref=source_ref)
    passages_text = "\n\n".join(f"- {p}" for p in passages)
    user = (
        f"## Concept\n{concept_title}\n\n"
        f"## Source Reference\n{source_ref}\n\n"
        f"## Relevant Passages\n{passages_text}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_json_response(text: str) -> dict:
    """Extract JSON from LLM response (handles fenced blocks)."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON in response: {text[:200]}")


def parse_concept_extraction(text: str) -> list[ConceptPlan]:
    """Parse JSON concept extraction response → list of ConceptPlan."""
    from llm_wiki.ingest.agent import ConceptPlan  # avoid circular at module level
    try:
        data = _parse_json_response(text)
        if not isinstance(data, dict):
            return []
        concepts = data.get("concepts") or []
        return [
            ConceptPlan(
                name=c["name"],
                title=c.get("title", c["name"]),
                passages=c.get("passages") if isinstance(c.get("passages"), list) else [],
                cluster=c.get("cluster", "") or "",
            )
            for c in concepts
            if isinstance(c, dict) and isinstance(c.get("name"), str) and c.get("name")
        ]
    except (ValueError, KeyError, TypeError):
        return []


_OVERVIEW_SYSTEM = """\
You are analyzing the opening of a scientific document to identify its primary \
concepts for a knowledge wiki.

## Task

Identify PRIMARY concepts — named models, datasets, methods, or tools that:
- Exist independently and may be referenced by other papers
- Warrant their own wiki page

Do NOT create concepts for paper-specific details like "training data curation", \
"ablation study", "experimental setup", "loss function variants" — these become \
sections WITHIN a primary concept page.

For each concept:
1. Assign a URL-safe slug (lowercase, hyphens only)
2. Check if it matches an existing wiki page slug — if yes, set action to "update" \
and use the EXACT existing slug
3. If new, set action to "create"
4. List 2-6 section names (sub-topics that will be sections on the page)
5. Assign a cluster (wiki subdirectory). Use an EXISTING cluster name if it fits; \
otherwise invent a short lowercase-hyphenated name (e.g. "structural-biology", \
"ml-methods"). All concepts from one paper should share a cluster unless they \
clearly belong to different domains.

## Existing Wiki Pages (check slugs before naming new concepts)

<<<MANIFEST>>>

## Existing Clusters (prefer these over inventing new ones)

<<<CLUSTER_DIRS>>>

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object:

{
  "concepts": [
    {
      "name": "exact-slug",
      "title": "Human Readable Title",
      "action": "create",
      "cluster": "structural-biology",
      "section_names": ["overview", "architecture", "benchmarks"]
    }
  ]
}"""


_PASSAGE_COLLECTION_SYSTEM = """\
You are extracting verbatim passages from a document chunk for specified concepts.

For each concept listed, extract 1-3 SHORT verbatim passages (exact quotes, \
not paraphrases) from the text below that directly describe or discuss that concept.

Only extract passages that are ACTUALLY IN THE TEXT. Do not invent or paraphrase.
If a concept does not appear in this chunk, return an empty list for it.

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object mapping concept slugs to passage lists:

{
  "concept-slug": ["exact verbatim passage from text", ...],
  "other-concept": []
}"""


_CONTENT_SYNTHESIS_SYSTEM = """\
You are writing wiki content for a specific concept using verbatim source passages.

## Citation Rules (Non-Negotiable)

Use numbered footnotes — `[^N]` inline, defined in a References section:
- `[^1]` = [[<<<SOURCE_REF>>>]] (the primary source)
- Every factual claim MUST end with `[^1]`. No exceptions.
- Do NOT embed [[raw/...]] in body text — only in the References section.

## Wikilink Rules

Named concepts, models, methods, datasets, proteins, databases, and proper nouns \
get `[[slug]]` wikilinks:
1. Slug in EXISTING WIKI list below → use that exact slug.
2. Slug in BATCH list below → use that exact slug.
3. Named concept NOT in either list (e.g. "Free Energy Perturbation", "TYK2") → \
   invent a kebab-case slug (e.g. `[[free-energy-perturbation]]`, `[[tyk2]]`). \
   Red links are fine — they flag pages to create.
4. Generic terms with no standalone identity → plain text, no brackets.

## Existing wiki pages (use [[slug]] for these)

<<<MANIFEST>>>

## Concepts in this ingest batch (also use [[slug]])

<<<BATCH_SLUGS>>>

## Content Rules

- Synthesize — do not transcribe passages verbatim.
- Be concise and precise. Every sentence earns its place.
- Do not interpret beyond what passages state.
- "X correlates with Y" not "X causes Y".

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. Include "references" as the LAST section:

{
  "summary": "One sentence (≤20 words) describing the concept.",
  "sections": [
    {
      "name": "section-slug",
      "heading": "Section Heading",
      "content": "Markdown with [[wikilinks]] and [^1] footnote citations."
    },
    {
      "name": "references",
      "heading": "References",
      "content": "[^1]: [[<<<SOURCE_REF>>>]]"
    }
  ]
}"""


_DIGEST_CHUNK_SYSTEM = """\
You are building a running digest of a scientific paper as you read it chunk by chunk.

Your job: update the digest with everything NEW and important in the current chunk.
Preserve all previously captured content unless you can merge it cleanly.

Capture:
- Named models, methods, datasets, baselines, and tools (with their key properties)
- Quantitative results and comparisons (exact numbers where stated)
- Core claims and findings, with enough context to cite them accurately
- Limitations, open problems, and future directions
- Cross-references between components

Do NOT truncate or drop prior content to save space.
Return ONLY the updated digest text — no preamble, no JSON."""


_DEEP_READ_SYNTHESIS_SYSTEM = """\
You are writing a wiki page for a specific concept.

You have a comprehensive digest of the full paper — you understand the \
whole document. Write from that understanding. Do not transcribe; synthesize.

Think like an expert explaining this concept to a knowledgeable colleague: \
integrate the methodology, results, comparisons to baselines, and limitations. \
Every sentence should carry information that earns its place.

## Citation Rules (Non-Negotiable)

Use numbered footnotes — `[^N]` inline, defined in a References section:
- `[^1]` = [[<<<SOURCE_REF>>>]] (the primary source)
- Every factual claim MUST end with `[^1]`. No exceptions.
- Do NOT embed [[raw/...]] in body text — only in the References section.

## Wikilink Rules

Named concepts, models, methods, datasets, proteins, databases, and proper nouns \
get `[[slug]]` wikilinks:
1. Slug in EXISTING WIKI list → use that exact slug.
2. Slug in BATCH list → use that exact slug.
3. Named concept NOT in either list → invent a kebab-case slug. Red links are fine.
4. Generic terms → plain text.

## Existing wiki pages

<<<MANIFEST>>>

## Concepts in this ingest batch

<<<BATCH_SLUGS>>>

## Content Rules

- Synthesize, do not transcribe.
- Write with depth. A good wiki page explains WHY, not just WHAT.
- Include quantitative results where they ground a claim.
- Do not interpret beyond what the paper states.
- "X correlates with Y" not "X causes Y".

## Structural Contract (Non-Negotiable)

Respond with a SINGLE JSON object. Include "references" as the LAST section:

{
  "summary": "One sentence (≤20 words) describing the concept.",
  "sections": [
    {
      "name": "section-slug",
      "heading": "Section Heading",
      "content": "Markdown with [[wikilinks]] and [^1] footnote citations."
    },
    {
      "name": "references",
      "heading": "References",
      "content": "[^1]: [[<<<SOURCE_REF>>>]]"
    }
  ]
}"""


def compose_digest_chunk_messages(
    chunk_text: str,
    running_digest: str,
    chunk_index: int,
    total_chunks: int,
) -> list[dict[str, str]]:
    """Build messages for one iteration of the rolling paper digest."""
    progress = f"chunk {chunk_index + 1} of {total_chunks}"
    prior = (
        f"## Running Digest So Far\n{running_digest}"
        if running_digest
        else "## Running Digest So Far\n(none — this is the first chunk)"
    )
    user = f"{prior}\n\n## New Chunk ({progress})\n{chunk_text}"
    return [
        {"role": "system", "content": _DIGEST_CHUNK_SYSTEM},
        {"role": "user", "content": user},
    ]


def compose_deep_read_synthesis_messages(
    concept: "ConceptPlan",
    paper_context: str,
    source_ref: str,
    manifest_lines: list[str],
    batch_concepts: "list[ConceptPlan]",
) -> list[dict[str, str]]:
    """Build messages for deep-read synthesis — full paper context, no pre-collected passages."""
    manifest = "\n".join(manifest_lines) if manifest_lines else "(empty wiki)"
    batch_slugs = "\n".join(f"- {c.name}: {c.title}" for c in batch_concepts)
    system = (
        _DEEP_READ_SYNTHESIS_SYSTEM
        .replace("<<<SOURCE_REF>>>", source_ref)
        .replace("<<<MANIFEST>>>", manifest)
        .replace("<<<BATCH_SLUGS>>>", batch_slugs or "(none)")
    )
    section_hint = (
        "## Requested sections\n" + "\n".join(f"- {s}" for s in concept.section_names)
        if concept.section_names
        else ""
    )
    # Paper context FIRST so it forms a stable prefix across all per-concept
    # synthesis calls in the same batch — this maximises KV-cache hits on
    # OpenAI-compatible providers (automatic prefix caching) and is the correct
    # structure for Anthropic cache_control markers too.
    user = (
        f"## Full Paper Context\n{paper_context}\n\n"
        f"## Concept to write\n{concept.title} (`{concept.name}`)\n\n"
        f"{section_hint}"
    ).strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def compose_overview_messages(
    chunk_text: str,
    manifest_lines: list[str],
    source_ref: str,
    cluster_dir_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build messages for the overview concept-identification pass."""
    manifest = "\n".join(manifest_lines) if manifest_lines else "(empty wiki)"
    clusters = (
        "\n".join(f"- {c}" for c in cluster_dir_names)
        if cluster_dir_names
        else "(none yet — invent appropriate names)"
    )
    system = (
        _OVERVIEW_SYSTEM
        .replace("<<<MANIFEST>>>", manifest)
        .replace("<<<CLUSTER_DIRS>>>", clusters)
    )
    user = f"## Source Reference\n{source_ref}\n\n## Document Opening\n{chunk_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def compose_passage_collection_messages(
    chunk_text: str,
    concepts: "list[ConceptPlan]",
) -> list[dict[str, str]]:
    """Build messages for extracting verbatim passages from one chunk."""
    concept_list = "\n".join(f"- {c.name}: {c.title}" for c in concepts)
    user = f"## Concepts to find\n{concept_list}\n\n## Document Chunk\n{chunk_text}"
    return [
        {"role": "system", "content": _PASSAGE_COLLECTION_SYSTEM},
        {"role": "user", "content": user},
    ]


def compose_content_synthesis_messages(
    concept: "ConceptPlan",
    passages: list[str],
    source_ref: str,
    manifest_lines: list[str],
    batch_concepts: "list[ConceptPlan]",
) -> list[dict[str, str]]:
    """Build messages for synthesising wiki sections from passages."""
    manifest = "\n".join(manifest_lines) if manifest_lines else "(empty wiki)"
    batch_slugs = "\n".join(f"- {c.name}: {c.title}" for c in batch_concepts)
    system = (
        _CONTENT_SYNTHESIS_SYSTEM
        .replace("<<<SOURCE_REF>>>", source_ref)
        .replace("<<<MANIFEST>>>", manifest)
        .replace("<<<BATCH_SLUGS>>>", batch_slugs or "(none)")
    )
    passages_text = "\n\n".join(f"- {p}" for p in passages)
    section_hint = (
        "## Requested sections\n" + "\n".join(f"- {s}" for s in concept.section_names)
        if concept.section_names
        else ""
    )
    user = (
        f"## Concept\n{concept.title}\n\n"
        f"## Source Reference\n{source_ref}\n\n"
        f"{section_hint}\n\n"
        f"## Relevant Passages\n{passages_text}"
    ).strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_overview_extraction(text: str) -> "list[ConceptPlan]":
    """Parse overview pass response → list of ConceptPlan with action + section_names."""
    from llm_wiki.ingest.agent import ConceptPlan
    try:
        data = _parse_json_response(text)
        concepts = data.get("concepts") or [] if isinstance(data, dict) else []
        return [
            ConceptPlan(
                name=c["name"],
                title=c.get("title", c["name"]),
                action=c.get("action", "create"),
                section_names=(
                    c.get("section_names")
                    if isinstance(c.get("section_names"), list)
                    else []
                ),
                cluster=c.get("cluster", "") or "",
                passages=[],
            )
            for c in concepts
            if isinstance(c, dict) and isinstance(c.get("name"), str) and c.get("name")
        ]
    except (ValueError, KeyError, TypeError):
        return []


def parse_passage_collection(text: str, concept_names: list[str]) -> dict[str, list[str]]:
    """Parse passage collection response → {slug: [passage, ...]}."""
    try:
        data = _parse_json_response(text)
        if not isinstance(data, dict):
            return {}
        result: dict[str, list[str]] = {}
        for name in concept_names:
            passages = data.get(name)
            if isinstance(passages, list):
                result[name] = [p for p in passages if isinstance(p, str) and p.strip()]
        return result
    except (ValueError, KeyError, TypeError):
        return {}


def parse_content_synthesis(text: str) -> SynthesisResult:
    """Parse content synthesis response → SynthesisResult with sections and summary."""
    from llm_wiki.ingest.page_writer import PageSection
    try:
        data = _parse_json_response(text)
        if not isinstance(data, dict):
            return SynthesisResult()
        sections_raw = data.get("sections") or []
        sections = [
            PageSection(
                name=s["name"],
                heading=s.get("heading", s["name"].replace("-", " ").title()),
                content=s.get("content", ""),
            )
            for s in sections_raw
            if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"]
        ]
        summary = data.get("summary") or ""
        if not isinstance(summary, str):
            summary = ""
        return SynthesisResult(sections=sections, summary=summary)
    except (ValueError, KeyError, TypeError):
        return SynthesisResult()


def parse_page_content(text: str) -> list[PageSection]:
    """Parse JSON page content response → list of PageSection."""
    from llm_wiki.ingest.page_writer import PageSection  # avoid circular at module level
    try:
        data = _parse_json_response(text)
        if not isinstance(data, dict):
            return []
        sections = data.get("sections") or []
        return [
            PageSection(
                name=s["name"],
                heading=s.get("heading", s["name"]),
                content=s.get("content", ""),
            )
            for s in sections
            if isinstance(s, dict) and isinstance(s.get("name"), str) and s.get("name")
        ]
    except (ValueError, KeyError, TypeError):
        return []
