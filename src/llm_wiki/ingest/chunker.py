from __future__ import annotations

from llm_wiki.tokens import count_tokens


def chunk_text(
    text: str,
    chunk_tokens: int = 6000,
    overlap: float = 0.15,
) -> list[str]:
    """Split text into overlapping chunks of approximately chunk_tokens tokens.

    Splits on paragraph boundaries (double newlines) to avoid mid-sentence
    cuts. A single paragraph larger than chunk_tokens is kept whole rather
    than split mid-word.

    Args:
        text:         Source text to chunk.
        chunk_tokens: Target token count per chunk.
        overlap:      Fraction of chunk_tokens to repeat between adjacent chunks.

    Returns:
        List of text chunks, possibly empty for blank input.
    """
    if not text.strip():
        return []

    overlap_tokens = int(chunk_tokens * overlap)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if current_tokens + para_tokens > chunk_tokens and current:
            chunks.append("\n\n".join(current))
            # Trim from the front until we are at or below overlap_tokens
            while current and (current_tokens - count_tokens(current[0])) >= overlap_tokens:
                removed = current.pop(0)
                current_tokens -= count_tokens(removed)
        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks
