"""Apply column decisions: write a de-identified copy and update the masks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .generalize import REDACTED, SUPPORTED, generalize
from .profile import (MANDATORY, PENDING_KINDS, default_entity, file_id, fingerprint, load_tables,
                      maskable, norm_for, profile_columns, rel_path)
from .scrub import Scrubber
from .store import Store, entity_name
from .tables import Table, write_table

ACTIONS = ("pseudonymize", "generalize", "drop", "keep")


class ApplyError(ValueError):
    pass


def state_path(root: Path, fid: str) -> Path:
    return Path(root) / ".deid" / "state" / "files" / f"{fid}.json"


def load_state(root: Path, fid: str) -> Optional[Dict]:
    p = state_path(root, fid)
    return json.loads(p.read_text()) if p.exists() else None


def save_state(root: Path, fid: str, state: Dict) -> None:
    p = state_path(root, fid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _match(decisions: List[Dict], tables: List[Table], cols_by_table: List[List[Dict]]) -> Dict:
    chosen = {}
    for d in decisions:
        col, action = str(d.get("column", "")), d.get("action")
        if action not in ACTIONS:
            raise ApplyError(f"column '{col}': action must be one of {', '.join(ACTIONS)}")
        hit = False
        for t, cols in zip(tables, cols_by_table):
            if d.get("table") not in (None, t.name):
                continue
            for c in cols:
                if c["name"] == col or col == f"#{c['index']}":
                    chosen[(t.name, c["index"])] = d
                    hit = True
        if not hit:
            names = sorted({c["name"] for cols in cols_by_table for c in cols})
            raise ApplyError(f"no column '{col}'; columns are: {', '.join(names)}")
    return chosen


def _out_paths(root: Path, rel: str, tables: List[Table]) -> Dict:
    base = Path(root) / ".deid" / "out" / rel
    if rel.lower().endswith(".xlsx"):
        sheets = {t.name: base.parent / (base.name + ".sheets") / f"{t.name}.csv" for t in tables}
        return {"sheets": sheets, "read": base.parent / (base.name + ".md")}
    return {"sheets": {tables[0].name: base}, "read": base}


def apply_decisions(store: Store, root: Path, path: Path, decisions: List[Dict]) -> Dict:
    root, path = Path(root), Path(path)
    rel, fid = rel_path(root, path), file_id(root, path)
    loaded = load_tables(path)
    tables = [t for t, _, _ in loaded]
    cols_by_table = [profile_columns(t) for t in tables]
    chosen = _match(decisions, tables, cols_by_table)

    plans = []
    for t, cols in zip(tables, cols_by_table):
        plan = []
        for c in cols:
            d = chosen.get((t.name, c["index"]), {})
            kind = d.get("kind") or c["kind"]
            action, note = d.get("action", c["default_action"]), ""
            if kind in MANDATORY and action in ("keep", "generalize"):
                action, note = "pseudonymize", f"{kind} is forced; pseudonymized instead of '{d['action']}'"
            if action == "generalize" and kind not in SUPPORTED:
                raise ApplyError(
                    f"column '{c['name']}' ({kind or 'unknown kind'}) cannot be generalized; "
                    f"pass \"kind\" as one of {', '.join(SUPPORTED)} or choose another action"
                )
            entity = d.get("entity")
            entity = entity_name(entity) if entity else (c["entity"] if c["kind"] == kind else default_entity(c["name"], kind))
            plan.append({"col": c, "kind": kind, "action": action, "entity": entity, "note": note})
        plans.append(plan)

    # Masks first, so free text in kept columns is scrubbed against them.
    store.forget_file(fid)
    for t, plan in zip(tables, plans):
        for p in plan:
            i, kind, action = p["col"]["index"] - 1, p["kind"], p["action"]
            key = f"{t.name}\x00{p['col']['name']}"
            pairs = {}
            for r in t.rows:
                v = r[i]
                if v is None or not v.strip() or v in pairs:
                    continue
                if action == "pseudonymize":
                    p.setdefault("tokens", {})[v] = store.pseudonym(p["entity"], norm_for(kind, v), raw=v.strip())
                    if maskable(v, kind):
                        pairs[v] = p["tokens"][v]
                elif action == "generalize" and maskable(v, kind):
                    pairs[v] = generalize(kind, v)
                elif action == "drop" and (kind in PENDING_KINDS or kind in MANDATORY) and maskable(v, kind):
                    pairs[v] = REDACTED
            store.add_lookups(fid, key, pairs.items())
    store.commit()

    scrubber = Scrubber(store)
    outputs = _out_paths(root, rel, tables)
    for t, plan in zip(tables, plans):
        keep = [p for p in plan if p["action"] != "drop"]
        rows = []
        for r in t.rows:
            row = []
            for p in keep:
                v = r[p["col"]["index"] - 1]
                if v is None:
                    row.append(None)
                elif p["action"] == "pseudonymize":
                    row.append(p["tokens"][v])
                elif p["action"] == "generalize":
                    row.append(generalize(p["kind"], v))
                elif p["col"]["type"] in ("int", "float", "date"):
                    row.append(v)
                else:
                    row.append(scrubber.scrub(v)[0])
            rows.append(row)
        write_table(Table(t.name, [p["col"]["name"] for p in keep], rows), outputs["sheets"][t.name])
    store.commit()

    out_rel = [str(p.relative_to(root)) for p in outputs["sheets"].values()]
    if outputs["read"] not in outputs["sheets"].values():
        outputs["read"].write_text(
            f"# deid-guard: de-identified copy of `{rel}`\n\nOne CSV per sheet:\n\n"
            + "\n".join(f"- `{p}`" for p in out_rel) + "\n",
            encoding="utf-8",
        )

    lines = [f"deid-guard applied decisions to `{rel}`.", ""]
    for t, plan in zip(tables, plans):
        if len(tables) > 1:
            lines.append(f"Sheet {t.name}:")
        for p in plan:
            how = p["action"]
            if how == "pseudonymize":
                how += f" as {p['entity']}_######"
            elif how == "generalize":
                how += f" ({p['kind']})"
            lines.append(f"- {p['col']['name']}: {how}" + (f"  [{p['note']}]" if p["note"] else ""))
    lines += ["", "De-identified copy: " + ", ".join(f"`{p}`" for p in out_rel),
              f"Reading `{rel}` now returns the copy. Use the copy for analysis."]

    state = {
        "file": rel, "fingerprint": fingerprint(path), "decisions": decisions,
        "outputs": out_rel, "read_path": str(outputs["read"]),
    }
    save_state(root, fid, state)
    return {"file": rel, "outputs": out_rel, "read_path": str(outputs["read"]), "summary": "\n".join(lines)}
