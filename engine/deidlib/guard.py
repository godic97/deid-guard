"""Decide what reading a data file returns: its profile card or its copy."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from .apply import apply_decisions, load_state, save_state
from .profile import file_id, fingerprint, profile_file, rel_path
from .store import Store
from .tables import NotTabular


def card_path(root: Path, rel: str) -> Path:
    return Path(root) / ".deid" / "cards" / (rel + ".md")


def guard(store: Store, root: Path, path: Path) -> Dict:
    root, path = Path(root), Path(path)
    rel, fid = rel_path(root, path), file_id(root, path)
    state = load_state(root, fid)
    current = fingerprint(path)

    if state and state.get("not_data") and state["fingerprint"] == current:
        return {"status": "not_data", "file": rel, "read_path": str(path)}

    if state and state.get("decisions") is not None:
        if state["fingerprint"] != current or not Path(state["read_path"]).exists():
            state = apply_decisions(store, root, path, state["decisions"])
        return {"status": "decided", "file": rel, "read_path": state["read_path"]}

    card = card_path(root, rel)
    if not state or state["fingerprint"] != current or not card.exists():
        try:
            result = profile_file(store, root, path)
        except NotTabular:
            store.forget_file(fid)
            save_state(root, fid, {"file": rel, "fingerprint": current, "not_data": True})
            return {"status": "not_data", "file": rel, "read_path": str(path)}
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(result["card"], encoding="utf-8")
        save_state(root, fid, {"file": rel, "fingerprint": current, "decisions": None})
    return {"status": "pending", "file": rel, "read_path": str(card)}
