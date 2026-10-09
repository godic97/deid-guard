"""Generalization: keep a coarser version of a quasi-identifier."""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"
SUPPORTED = ("birthdate", "date", "address", "zip", "age")

_DATE = re.compile(r"^\s*(\d{4})(?:[-./]?(\d{1,2})(?:[-./]?(\d{1,2}))?)?")


class GeneralizeError(ValueError):
    pass


def _korean_district(value: str) -> str:
    tokens = value.split()
    out = tokens[:1]
    for tok in tokens[1:3]:
        if re.search(r"(시|군|구)$", tok):
            out.append(tok)
            if not tok.endswith("시"):
                break
        else:
            break
    return " ".join(out)


def generalize(kind: str, value: str) -> str:
    v = value.strip()
    if kind == "birthdate":
        m = _DATE.match(v)
        return m.group(1) if m else REDACTED
    if kind == "date":
        m = _DATE.match(v)
        return f"{m.group(1)}-{int(m.group(2)):02d}" if m and m.group(2) else REDACTED
    if kind == "address":
        if re.search(r"[가-힣]", v):
            return _korean_district(v)
        parts = [p.strip() for p in v.split(",")]
        rest = [re.sub(r"\s*\d[\d-]*\s*", " ", p).strip() for p in parts[1:]]
        return ", ".join(p for p in rest if p) or REDACTED
    if kind == "zip":
        return v[:3] + "**" if len(v) > 3 else REDACTED
    if kind == "age":
        try:
            n = int(float(v))
        except (ValueError, OverflowError):
            return REDACTED
        if n < 0:
            return REDACTED
        lo = n - n % 5
        return f"{lo}-{lo + 4}"
    raise GeneralizeError(f"cannot generalize a '{kind}' column; supported: {', '.join(SUPPORTED)}")
