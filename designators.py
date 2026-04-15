"""designators.py — Parsing and expansion of component designator strings.

All functions are pure (no side effects, no state) and can be tested
independently of the rest of the application.
"""

import re
from typing import List


def parse_text_lines(raw_text: str) -> List[str]:
    """Return stripped, non-empty lines from a block of raw text."""
    return [line.strip() for line in raw_text.splitlines() if line.strip()]


def expand_designators(line: str) -> List[str]:
    """Flatten all designator segments in a placement line into a single list."""
    return [comp for segment in expand_designator_segments(line) for comp in segment]


def expand_designator_segments(line: str) -> List[List[str]]:
    """Parse a placement line into comma-separated segments.

    Each segment is a list of component designators.  Ranges like ``R1-R5``
    are expanded; single tokens like ``C10`` are returned as-is.  Annotation
    tokens beginning with ``SEE VIEW`` or ``FOR `` are skipped.

    Returns:
        A list of segments, where each segment is a list of designator strings.
    """
    text = line.upper().strip()
    text = text.replace("\u2013", "-").replace("\u2014", "-")  # normalise em/en dashes
    text = re.sub(r"\([^)]*\)", "", text)                      # remove parenthetical notes
    text = re.sub(r"(?<=[A-Z0-9])\.(?=[A-Z])", ",", text)     # treat separator dots as commas
    text = text.strip().rstrip(".")

    segments: List[List[str]] = []
    for token in (tok.strip() for tok in text.split(",") if tok.strip()):
        if token.startswith("SEE VIEW") or token.startswith("FOR "):
            continue
        components = expand_designator_token(token)
        if components:
            segments.append(components)

    return segments


def expand_designator_token(token: str) -> List[str]:
    """Expand a single token into one or more component designators.

    Handles:
    - Numeric ranges:  ``R1-R5``, ``C01-C09`` (zero-padding preserved)
    - Single items:    ``U3``, ``TP12A``

    Returns an empty list if the token does not match a recognised pattern.
    """
    # --- range: e.g. R1-R5, C01-C09, U3A-U8A ---
    range_match = re.fullmatch(
        r"([A-Z]+)(\d+)([A-Z]*)\s*-\s*([A-Z]+)?(\d+)([A-Z]*)", token
    )
    if range_match:
        p1, n1, s1, p2, n2, s2 = range_match.groups()
        p2 = p2 or p1
        s2 = s2 or s1
        if p1 == p2 and s1 == s2:
            start_num, end_num = int(n1), int(n2)
            if start_num <= end_num:
                has_padding = n1.startswith("0") or n2.startswith("0")
                width = max(len(n1), len(n2)) if has_padding else 0
                if width:
                    return [f"{p1}{n:0{width}d}{s1}" for n in range(start_num, end_num + 1)]
                return [f"{p1}{n}{s1}" for n in range(start_num, end_num + 1)]

    # --- single: e.g. C10, TP3A ---
    single_match = re.fullmatch(r"([A-Z]+)(\d+)([A-Z]*)", token)
    if single_match:
        p, n, s = single_match.groups()
        return [f"{p}{n}{s}"]

    return []
