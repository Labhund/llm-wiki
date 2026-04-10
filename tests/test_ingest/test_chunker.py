from llm_wiki.ingest.chunker import chunk_text


def test_chunk_text_empty():
    assert chunk_text("", chunk_tokens=100) == []


def test_chunk_text_short_fits_one_chunk():
    text = "Hello world.\n\nSecond paragraph."
    chunks = chunk_text(text, chunk_tokens=1000)
    assert len(chunks) == 1
    assert "Hello world" in chunks[0]
    assert "Second paragraph" in chunks[0]


def test_chunk_text_splits_on_paragraphs():
    # 3 large paragraphs, each ~50 words → force split at chunk_tokens=30
    para = "word " * 50
    text = (para.strip() + "\n\n") * 3
    chunks = chunk_text(text, chunk_tokens=30, overlap=0.0)
    assert len(chunks) >= 2


def test_chunk_text_overlap_repeats_content():
    para = "word " * 40
    text = (para.strip() + "\n\n") * 4
    chunks = chunk_text(text, chunk_tokens=50, overlap=0.3)
    if len(chunks) >= 2:
        # Last paragraph of chunk N should appear in chunk N+1
        last_para_of_chunk0 = chunks[0].split("\n\n")[-1].strip()
        assert last_para_of_chunk0 in chunks[1]


def test_chunk_text_single_huge_paragraph():
    # A single paragraph larger than chunk_tokens is kept as-is (not split mid-para)
    big = "word " * 500
    chunks = chunk_text(big.strip(), chunk_tokens=100, overlap=0.0)
    assert len(chunks) == 1
