from llm_wiki.mcp.tools import _ok


def test_ok_produces_compact_json():
    result = _ok({"a": {"b": "c"}, "d": [1, 2, 3]})
    text = result[0].text
    assert "\n" not in text
    assert "  " not in text
    # Still valid JSON
    import json
    parsed = json.loads(text)
    assert parsed == {"a": {"b": "c"}, "d": [1, 2, 3]}


def test_ok_compact_is_smaller_than_pretty():
    import json
    data = {"issues": {"n": 3, "sev": {"critical": 1, "moderate": 2}}}
    compact = _ok(data)[0].text
    pretty = json.dumps(data, indent=2)
    assert len(compact) < len(pretty)
