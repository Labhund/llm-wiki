from __future__ import annotations

import pytest

from llm_wiki.daemon.scheduler import parse_interval


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("30s", 30.0),
        ("1s", 1.0),
        ("15m", 900.0),
        ("6h", 21600.0),
        ("12h", 43200.0),
        ("2d", 172800.0),
        ("0s", 0.0),
    ],
)
def test_parse_interval_valid(spec: str, expected: float):
    assert parse_interval(spec) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "abc",
        "5",          # missing unit
        "5x",         # unknown unit
        "h6",         # number after unit
        "-5m",        # negative
        "5.5h",       # fractional not supported in v1
        "5 hours",    # long-form units not supported
    ],
)
def test_parse_interval_invalid_raises(spec: str):
    with pytest.raises(ValueError):
        parse_interval(spec)


def test_parse_interval_strips_whitespace():
    assert parse_interval("  6h  ") == 21600.0
