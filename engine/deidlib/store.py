"""Local state: pseudonym mapping and the replacement table the scrubber reads.

Everything lives under <root>/.deid/state/, which the plugin never lets the
model read.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS counters (entity TEXT PRIMARY KEY, last INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS mapping (
  entity TEXT NOT NULL, norm TEXT NOT NULL, raw TEXT NOT NULL, token TEXT NOT NULL UNIQUE,
  PRIMARY KEY (entity, norm)
);
CREATE TABLE IF NOT EXISTS lookups (
  file_id TEXT NOT NULL, column TEXT NOT NULL, surface TEXT NOT NULL, replacement TEXT NOT NULL,
  multiword INTEGER NOT NULL, PRIMARY KEY (file_id, column, surface)
);
CREATE INDEX IF NOT EXISTS lookups_surface ON lookups (surface);
"""


# A run of value characters as the scrubber splits text; trailing joiners are
# trimmed so "홍길동." and "P-0012," yield the bare value.
SPAN_RE = re.compile(r"[0-9A-Za-z가-힣](?:[0-9A-Za-z가-힣@.+\-_]*[0-9A-Za-z가-힣])?")


def entity_name(column: str, fallback: str = "ID") -> str:
    name = re.sub(r"[^A-Z0-9]+", "_", column.upper()).strip("_")
    if not name:
        return fallback
    if name[0].isdigit():
        name = "C_" + name
    return name


def deid_dir(root: Path) -> Path:
    d = Path(root) / ".deid"
    (d / "state").mkdir(parents=True, exist_ok=True)
    ignore = d / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")
    return d


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = deid_dir(self.root)
        self.db = sqlite3.connect(str(self.dir / "state" / "map.sqlite"))
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def commit(self) -> None:
        self.db.commit()

    def pseudonym(self, entity: str, norm: str, raw: Optional[str] = None) -> str:
        row = self.db.execute(
            "SELECT token FROM mapping WHERE entity = ? AND norm = ?", (entity, norm)
        ).fetchone()
        if row:
            return row[0]
        self.db.execute(
            "INSERT INTO counters (entity, last) VALUES (?, 1) "
            "ON CONFLICT(entity) DO UPDATE SET last = last + 1",
            (entity,),
        )
        (n,) = self.db.execute("SELECT last FROM counters WHERE entity = ?", (entity,)).fetchone()
        token = f"{entity}_{n:06d}"
        self.db.execute(
            "INSERT INTO mapping (entity, norm, raw, token) VALUES (?, ?, ?, ?)",
            (entity, norm, raw if raw is not None else norm, token),
        )
        return token

    def reveal(self, token: str) -> Optional[str]:
        row = self.db.execute("SELECT raw FROM mapping WHERE token = ?", (token,)).fetchone()
        return row[0] if row else None

    def known_tokens(self, tokens: Iterable[str]) -> dict:
        out = {}
        for t in set(tokens):
            raw = self.reveal(t)
            if raw is not None:
                out[t] = raw
        return out

    def add_lookups(self, file_id: str, column: str, pairs: Iterable[Tuple[str, str]]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO lookups (file_id, column, surface, replacement, multiword) "
            "VALUES (?, ?, ?, ?, ?)",
            ((file_id, column, s, r, 0 if SPAN_RE.fullmatch(s) else 1)
             for s, r in ((s.strip(), r) for s, r in pairs) if s),
        )

    def forget_column(self, file_id: str, column: str) -> None:
        self.db.execute("DELETE FROM lookups WHERE file_id = ? AND column = ?", (file_id, column))

    def forget_file(self, file_id: str) -> None:
        self.db.execute("DELETE FROM lookups WHERE file_id = ?", (file_id,))

    def lookup(self, surface: str) -> Optional[str]:
        row = self.db.execute(
            "SELECT replacement FROM lookups WHERE surface = ? LIMIT 1", (surface,)
        ).fetchone()
        return row[0] if row else None

    def multiword_lookups(self) -> List[Tuple[str, str]]:
        rows = self.db.execute(
            "SELECT surface, replacement FROM lookups WHERE multiword = 1 "
            "GROUP BY surface ORDER BY length(surface) DESC"
        ).fetchall()
        return [(s, r) for s, r in rows]
