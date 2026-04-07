def count_tokens(text: str) -> int:
    """Estimate token count. Roughly 4 characters per token for English text.

    This is a fast heuristic. Swap for tiktoken or tokenizers if precision
    matters for your model.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def fits_budget(text: str, budget: int) -> bool:
    """Check if text fits within a token budget."""
    return count_tokens(text) <= budget
