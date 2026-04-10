from llm_wiki.ingest.grounding import ground_passage, _bigram_f1, _is_visual_content, GroundingResult


def test_bigram_f1_identical():
    assert _bigram_f1("the cat sat on the mat", "the cat sat on the mat") == 1.0


def test_bigram_f1_no_overlap():
    assert _bigram_f1("hello world foo", "bar baz qux quux") == 0.0


def test_bigram_f1_partial():
    score = _bigram_f1("the cat sat", "the cat ran away")
    assert 0.0 < score < 1.0


def test_bigram_f1_empty():
    assert _bigram_f1("", "some text") == 0.0


def test_ground_passage_high_score_for_exact_match():
    source = "Boltz-2 achieves state-of-the-art performance on binding affinity prediction."
    result = ground_passage(source, source)
    assert result.score == 1.0
    assert result.verifiable is True
    assert result.ocr_sourced is False


def test_ground_passage_low_score_for_mismatch():
    passage = "Boltz-2 achieves state-of-the-art performance."
    source = "Completely unrelated text about something else entirely."
    result = ground_passage(passage, source)
    assert result.score < 0.3


def test_ground_passage_visual_content_unverifiable():
    passage = "See Figure 3 for the architecture diagram."
    result = ground_passage(passage, "some source text")
    assert result.verifiable is False
    assert result.score == 0.0


def test_ground_passage_equation_unverifiable():
    passage = "The loss is defined as L = Σ α_i x_i."
    result = ground_passage(passage, "some source text")
    assert result.verifiable is False


def test_ground_passage_ocr_sourced_flag():
    result = ground_passage("some text", "some text here", ocr_sourced=True)
    assert result.ocr_sourced is True


def test_is_visual_content_figure():
    assert _is_visual_content("See Figure 1 for details.")
    assert _is_visual_content("Shown in Fig. 3.")
    assert not _is_visual_content("The model achieves high accuracy.")
