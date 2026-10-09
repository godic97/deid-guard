"""JSON-in, JSON-out command line the Claude Code mod calls.

    python3 deid.py <command> [path|token] --root <project dir>  < stdin.json

Every command prints one JSON object. Failures print {"error": ...} and
exit with status 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .apply import apply_decisions, load_state
from .guard import guard
from .scrub import Scrubber, restore
from .store import Store
from .tables import is_data_file

SKIP_DIRS = {".git", ".deid", ".claude", "node_modules", ".venv", "venv", "env", "__pycache__",
             "dist", "build", "site-packages", ".tox", ".mypy_cache", ".pytest_cache"}
MAX_SCAN_FILES = 2000
MAX_SCAN_BYTES = 200 * 1024 * 1024


def scan(store: Store, root: Path) -> dict:
    """Profile every data file under root so their values are masked from the start.

    Spreadsheets and CSV files go first, so a project full of JSON config
    cannot push them past the file limit. Unchanged files cost one stat.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if is_data_file(name) and path.stat().st_size <= MAX_SCAN_BYTES:
                found.append(path)
    found.sort(key=lambda p: (p.suffix.lower() in (".json", ".jsonl"), str(p)))

    files, errors = [], []
    for i, path in enumerate(found):
        if i >= MAX_SCAN_FILES:
            return {"files": files, "errors": errors, "truncated": True}
        rel = str(path.relative_to(root))
        try:
            if guard(store, root, path)["status"] != "not_data":
                files.append(rel)
        except Exception as e:
            errors.append({"file": rel, "error": f"{type(e).__name__}: {e}"})
    return {"files": files, "errors": errors, "truncated": False}


def _stdin() -> dict:
    data = sys.stdin.read()
    return json.loads(data) if data.strip() else {}


def _resolve(root: Path, arg: str) -> Path:
    p = Path(arg)
    return p if p.is_absolute() else root / p


def run(argv) -> dict:
    ap = argparse.ArgumentParser(prog="deid")
    ap.add_argument("command", choices=["ping", "guard", "scan", "plan", "apply", "scrub", "restore", "reveal", "status"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "ping":
        return {"ok": True, "version": __version__, "python": sys.version.split()[0]}

    store = Store(root)
    try:
        if args.command == "guard":
            return guard(store, root, _resolve(root, args.arg))
        if args.command == "scan":
            return scan(store, root)
        if args.command == "plan":
            return apply_decisions(store, root, _resolve(root, args.arg), _stdin().get("decisions", []), dry_run=True)
        if args.command == "apply":
            return apply_decisions(store, root, _resolve(root, args.arg), _stdin().get("decisions", []))
        if args.command == "scrub":
            scrubber = Scrubber(store)
            texts, hits = [], 0
            for t in _stdin().get("texts", []):
                new, n = scrubber.scrub(t)
                texts.append(new)
                hits += n
            store.commit()
            return {"texts": texts, "hits": hits}
        if args.command == "restore":
            return {"texts": [restore(store, t) for t in _stdin().get("texts", [])]}
        if args.command == "reveal":
            return {"token": args.arg, "raw": store.reveal(args.arg or "")}
        if args.command == "status":
            files = []
            for p in sorted((root / ".deid" / "state" / "files").glob("*.json")):
                s = load_state(root, p.stem)
                if s.get("not_data"):
                    continue
                files.append({
                    "file": s["file"],
                    "status": "pending" if s.get("decisions") is None else "decided",
                    "outputs": s.get("outputs", []),
                })
            return {"files": files}
    finally:
        store.close()
    return {"error": "unreachable"}


def main(argv=None) -> int:
    try:
        out = run(sys.argv[1:] if argv is None else argv)
    except Exception as e:  # reported to the mod as data, never as a traceback
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0
