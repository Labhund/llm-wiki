from llm_wiki.tokens import count_tokens, fits_budget


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_short():
    # ~4 chars per token heuristic
    result = count_tokens("hello world")
    assert 2 <= result <= 4


def test_count_tokens_longer():
    text = "The quick brown fox jumps over the lazy dog. " * 10
    result = count_tokens(text)
    assert 90 <= result <= 130


def test_fits_budget():
    assert fits_budget("hello", budget=100)
    long_text = "word " * 10000
    assert not fits_budget(long_text, budget=100)
