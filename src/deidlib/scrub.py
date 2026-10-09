"""Replace personal data in free text: known values first, then detectors."""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from .detectors import find_all
from .store import SPAN_RE, Store

# Longest first, so 에게 wins over 에.
PARTICLES = sorted(
    ["이", "가", "은", "는", "을", "를", "의", "에", "에게", "께", "께서", "과", "와", "도", "만",
     "로", "으로", "님", "씨", "에서", "한테", "이랑", "랑", "이다", "이며", "이고"],
    key=len,
    reverse=True,
)
_FLOAT_ZERO = re.compile(r"^(\d+)\.0+$")
TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d{6}\b")


class Scrubber:
    """Scrubs many texts against one store, caching lookups between them."""

    def __init__(self, store: Store):
        self.store = store
        self.multiword = store.multiword_lookups()
        self._cache: Dict[str, Optional[str]] = {}

    def _lookup(self, surface: str) -> Optional[str]:
        if surface not in self._cache:
            self._cache[surface] = self.store.lookup(surface)
        return self._cache[surface]

    def _replace_span(self, span: str) -> Tuple[str, int]:
        hit = self._lookup(span)
        if hit is not None:
            return hit, 1
        m = _FLOAT_ZERO.match(span)
        if m:
            hit = self._lookup(m.group(1))
            if hit is not None:
                return hit, 1
        for p in PARTICLES:
            if span.endswith(p) and len(span) > len(p):
                hit = self._lookup(span[: -len(p)])
                if hit is not None:
                    return hit + p, 1
        if "_" in span:
            parts = span.split("_")
            hits = 0
            for i, part in enumerate(parts):
                if part:
                    new, n = self._replace_span(part)
                    parts[i], hits = new, hits + n
            if hits:
                return "_".join(parts), hits
        return span, 0

    def scrub(self, text: str) -> Tuple[str, int]:
        hits = 0

        for surface, replacement in self.multiword:
            if surface in text:
                hits += text.count(surface)
                text = text.replace(surface, replacement)

        out, last = [], 0
        for m in find_all(text):
            out.append(text[last : m.start])
            out.append(self.store.pseudonym(m.kind, m.norm, raw=m.value))
            last = m.end
            hits += 1
        out.append(text[last:])
        text = "".join(out)

        out, last = [], 0
        for m in SPAN_RE.finditer(text):
            new, n = self._replace_span(m.group(0))
            if n:
                out.append(text[last : m.start()])
                out.append(new)
                last = m.end()
                hits += n
        out.append(text[last:])
        return "".join(out), hits


def scrub(store: Store, text: str) -> Tuple[str, int]:
    return Scrubber(store).scrub(text)


def restore(store: Store, text: str) -> str:
    known = store.known_tokens(TOKEN_RE.findall(text))
    if not known:
        return text
    return TOKEN_RE.sub(lambda m: known.get(m.group(0), m.group(0)), text)
