"""Pattern detectors for personal data that never needs a judgement call.

Every match here is treated as mandatory: the engine replaces it no matter
what the model or the user decides about the surrounding column.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class Match:
    kind: str
    start: int
    end: int
    value: str
    norm: str


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _valid_yymmdd(s: str) -> bool:
    month, day = int(s[2:4]), int(s[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _norm_phone(value: str) -> str:
    d = _digits(value)
    if d.startswith("82") and value.lstrip().startswith("+82"):
        d = "0" + d[2:]
    if d.startswith("1") and len(d) == 11 and value.lstrip().startswith("+1"):
        d = d[1:]
    return d


def _check_rrn(m: re.Match) -> bool:
    return _valid_yymmdd(_digits(m.group(0))[:6])


def _check_card(m: re.Match) -> bool:
    d = _digits(m.group(0))
    return 13 <= len(d) <= 19 and len(set(d)) > 1 and _luhn(d)


# Order matters: earlier detectors win when spans overlap.
_B = r"(?<![\w-])"  # no digit, letter or hyphen right before
_E = r"(?![\w-])"
_DETECTORS: List[tuple] = [
    ("EMAIL", re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w-])"), None, str.lower),
    ("RRN", re.compile(_B + r"\d{6}\s?-\s?[1-8]\d{6}" + _E), _check_rrn, _digits),
    ("RRN", re.compile(_B + r"\d{6}[1-8]\d{6}" + _E), _check_rrn, _digits),
    ("DRIVER", re.compile(_B + r"(?:1[1-9]|2[0-8])-\d{2}-\d{6}-\d{2}" + _E), None, _digits),
    ("CARD", re.compile(_B + r"\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,7}" + _E), _check_card, _digits),
    ("SSN", re.compile(_B + r"(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}" + _E), None, _digits),
    ("PHONE", re.compile(r"(?<![\w+-])(?:\+82[-.\s]?(?:0)?1[016789]|01[016789])[-.\s]?\d{3,4}[-.\s]?\d{4}" + _E), None, _norm_phone),
    ("PHONE", re.compile(_B + r"0(?:2|[3-6][1-5]|70)[-.)\s]\s?\d{3,4}[-.\s]\d{4}" + _E), None, _norm_phone),
    ("PHONE", re.compile(r"(?<![\w+-])(?:\+1[-.\s]?)?(?:\([2-9]\d{2}\)\s?|[2-9]\d{2}[-.\s])\d{3}[-.\s]\d{4}" + _E), None, _norm_phone),
]

# Detectors that only fire when a context word sits shortly before the value.
_CONTEXT_DETECTORS: List[tuple] = [
    ("PASSPORT", re.compile(r"(?:여권|passport)[^\n]{0,12}?([A-Z]\d{8}|[A-Z]\d{3}[A-Z]\d{4})(?![\w-])", re.I)),
    ("ACCOUNT", re.compile(r"(?:계좌|통장|예금|account|acct)[^\n\d]{0,12}?(\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,3})?)(?![\w-])", re.I)),
]


def find_all(text: str) -> List[Match]:
    """Return non-overlapping matches in text order."""
    found: List[Match] = []
    taken: List[tuple] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in taken)

    def add(kind: str, start: int, end: int, norm: Callable[[str], str]) -> None:
        if free(start, end):
            value = text[start:end]
            found.append(Match(kind, start, end, value, norm(value)))
            taken.append((start, end))

    for kind, pattern in _CONTEXT_DETECTORS:
        for m in pattern.finditer(text):
            add(kind, m.start(1), m.end(1), lambda v: v.upper() if kind == "PASSPORT" else _digits(v))

    for kind, pattern, check, norm in _DETECTORS:
        for m in pattern.finditer(text):
            if check is None or check(m):
                add(kind, m.start(), m.end(), norm)

    found.sort(key=lambda m: m.start)
    return found


def classify(value: str) -> Optional[str]:
    """Kind of a value that is one detector match end to end, else None."""
    value = value.strip()
    matches = find_all(value)
    if len(matches) == 1 and matches[0].start == 0 and matches[0].end == len(value):
        return matches[0].kind
    return None
