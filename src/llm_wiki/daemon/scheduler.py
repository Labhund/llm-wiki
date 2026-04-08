from __future__ import annotations

import re

_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(spec: str) -> float:
    """Parse an interval spec ('30s', '15m', '6h', '2d') to seconds.

    Returns:
        The interval in seconds as a float.

    Raises:
        ValueError: if the spec is malformed (empty, missing unit, unknown
        unit, fractional, negative, or contains long-form unit names).
    """
    if not isinstance(spec, str):
        raise ValueError(f"Interval spec must be a string, got {type(spec).__name__}")
    stripped = spec.strip()
    match = _INTERVAL_RE.match(stripped)
    if match is None:
        raise ValueError(f"Invalid interval spec: {spec!r}")
    value = int(match.group(1))
    unit = match.group(2)
    return float(value * _UNIT_SECONDS[unit])
