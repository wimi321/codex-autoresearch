from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def extract_last_number(output: str) -> float:
    matches = _NUMBER_RE.findall(output)
    if not matches:
        raise ValueError("no numeric metric found in verify output")
    return float(matches[-1])


def is_improvement(candidate: float, baseline: float, direction: str, min_delta: float) -> bool:
    if direction == "higher":
        return candidate > baseline + min_delta
    if direction == "lower":
        return candidate < baseline - min_delta
    raise ValueError("direction must be 'higher' or 'lower'")
