#!/usr/bin/env python3
"""
Simple wiki traversal prototype.

This is a minimal implementation demonstrating multi-turn traversal
with working memory. It doesn't use an LLM yet — the decision logic
is hardcoded. Real implementation would use LLM for:
- Extracting learned information from pages
- Scoring candidate pages
- Deciding continue/stop
- Synthesizing final answers
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Set


@dataclass
class WorkingMemory:
    """Agent's internal working memory between traversal turns."""
    turn: int
    pages_read: Dict[str, str] = field(default_factory=dict)  # path -> learned
    questions: List[str] = field(default_factory=list)
    candidates: List[Dict[str, float]] = field(default_factory=list)  # {path, score}
    hypothesis: Optional[str] = None


@dataclass
class Page:
    """A wiki page."""
    path: str
    content: str
    links: List[str]


@dataclass
class SearchResult:
    """Result from index search."""
    path: str
    title: str
    summary: str
    topic: str
    relevance: float


class WikiTraversal:
    """Multi-turn wiki traversal with working memory."""

    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)
        self.memory: Optional[WorkingMemory] = None
        self.read_pages: Set[str] = set()

    def search_index(self, query: str, limit: int = 10) -> List[SearchResult]:
        """
        Search wiki/index.md for relevant pages.

        For now: simple keyword matching. Real implementation would use:
        - BM25 over index entries
        - Vector search on page summaries
        - LLM semantic matching
        """
        index_path = self.wiki_root / "wiki" / "index.md"

        if not index_path.exists():
            return []

        index_content = index_path.read_text()

        # Parse index (naive: look for links with summaries)
        # Format: - [[wiki/topic/article.md]] - Summary text
        pattern = r'- \[\[(.*?)\]\] - (.*)'
        matches = re.findall(pattern, index_content)

        results = []
        for path, summary in matches:
            # Simple keyword scoring
            query_lower = query.lower()
            summary_lower = summary.lower()

            score = 0
            for word in query_lower.split():
                if word in summary_lower:
                    score += 1

            # Normalize by summary length
            score = score / len(summary_lower.split()) if summary_lower.split() else 0

            if score > 0:
                title = path.split('/')[-1].replace('.md', '')
                topic = path.split('/')[1] if len(path.split('/')) > 1 else 'root'

                results.append(SearchResult(
                    path=path,
                    title=title,
                    summary=summary,
                    topic=topic,
                    relevance=score
                ))

        # Sort by relevance
        results.sort(key=lambda r: r.relevance, reverse=True)
        return results[:limit]

    def read_page(self, path: str) -> Optional[Page]:
        """Read a single wiki page."""
        full_path = self.wiki_root / path

        if not full_path.exists():
            return None

        content = full_path.read_text()

        # Extract wikilinks
        links = re.findall(r'\[\[(.*?)\]\]', content)

        return Page(path=path, content=content, links=links)

    def start_traversal(self, query: str) -> WorkingMemory:
        """Initialize traversal with first search and page read."""
        results = self.search_index(query)

        if not results:
            raise ValueError(f"No results for query: {query}")

        # Pick top result
        first_page_path = results[0].path
        page = self.read_page(first_page_path)

        if not page:
            raise ValueError(f"Could not read page: {first_page_path}")

        # Initialize working memory
        self.memory = WorkingMemory(turn=1)
        self.read_pages.add(page.path)

        # Simulate LLM learning from page (hardcoded for prototype)
        learned = self._mock_learn_from_page(page)

        self.memory.pages_read[page.path] = learned

        # Extract candidate pages from links
        for link in page.links:
            self.memory.candidates.append({
                "path": link,
                "score": 1.0,  # All links equal for prototype
            })

        return self.memory

    def _mock_learn_from_page(self, page: Page) -> str:
        """
        Mock LLM learning — in reality, this would be an LLM call.

        The LLM would:
        1. Read the page content
        2. Extract key information
        3. Identify new questions
        4. Note interesting links

        For prototype, we return a simple summary.
        """
        # Extract first paragraph (naive)
        paragraphs = page.content.split('\n\n')
        first_para = paragraphs[0] if paragraphs else ""

        # Remove markdown formatting
        clean = re.sub(r'[#*`\[\]]', '', first_para)
        clean = clean.strip()

        return clean[:200] if clean else "No summary available"

    def next_turn(self) -> Optional[WorkingMemory]:
        """
        Execute next turn of traversal.

        Returns:
            Updated working memory if continuing
            None if done (ready to answer)
        """
        if not self.memory:
            raise ValueError("Traversal not started. Call start_traversal first.")

        # Check stop condition (naive: stop after 3 turns for prototype)
        if self.memory.turn >= 3:
            return None

        # Pick next candidate page (naive: highest score, not yet read)
        candidates_sorted = sorted(
            self.memory.candidates,
            key=lambda c: c["score"],
            reverse=True
        )

        next_page = None
        for candidate in candidates_sorted:
            if candidate["path"] not in self.read_pages:
                next_page = candidate["path"]
                break

        if not next_page:
            # No more candidates
            return None

        # Read next page
        page = self.read_page(next_page)
        if not page:
            # Page doesn't exist, skip
            return self.next_turn()

        # Update working memory
        self.memory.turn += 1
        self.read_pages.add(page.path)

        learned = self._mock_learn_from_page(page)
        self.memory.pages_read[page.path] = learned

        # Add new candidates from this page
        for link in page.links:
            if link not in [c["path"] for c in self.memory.candidates]:
                self.memory.candidates.append({
                    "path": link,
                    "score": 1.0,
                })

        return self.memory

    def synthesize_answer(self) -> Dict[str, any]:
        """
        Synthesize final answer from working memory.

        In reality, this would be an LLM call with the full working memory
        and all page contents.

        For prototype, we return a simple summary.
        """
        if not self.memory:
            raise ValueError("Traversal not completed.")

        # Collect all learned information
        learned_summaries = [
            f"- {path}: {learned}"
            for path, learned in self.memory.pages_read.items()
        ]

        answer = "Based on wiki traversal:\n\n" + "\n".join(learned_summaries)

        return {
            "answer": answer,
            "citations": list(self.read_pages),
            "turns": self.memory.turn,
        }


def demo_traversal():
    """Demo the traversal prototype."""
    print("=== Wiki Traversal Demo ===\n")

    # Initialize (use absolute path for demo)
    import os
    wiki_root = os.path.expanduser("~/repos/llm-wiki")
    traversal = WikiTraversal(wiki_root)

    # Start traversal
    query = "validate sRNA embeddings"
    print(f"Query: {query}\n")

    try:
        memory = traversal.start_traversal(query)
        print(f"Turn {memory.turn}: Read {list(memory.pages_read.keys())[0]}")
        print(f"  Learned: {memory.pages_read[list(memory.pages_read.keys())[0]]}\n")

        # Continue traversal
        while True:
            memory = traversal.next_turn()
            if memory is None:
                break

            print(f"Turn {memory.turn}: Read {list(memory.pages_read.keys())[-1]}")
            print(f"  Learned: {memory.pages_read[list(memory.pages_read.keys())[-1]]}\n")

        # Synthesize answer
        answer = traversal.synthesize_answer()
        print("=== Final Answer ===\n")
        print(answer["answer"])
        print(f"\nCitations: {len(answer['citations'])} pages over {answer['turns']} turns")

    except ValueError as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    demo_traversal()
