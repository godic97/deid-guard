"""Profile a data file: what each column probably is, without showing values.

The profile is what the model sees instead of the file until the user has
decided how to treat each column. Profiling also registers the values of
mandatory and pending columns with the scrubber, so they are masked in any
tool output even before a decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .detectors import classify, find_all
from .generalize import generalize
from .store import Store, entity_name
from .tables import Table, read_tables

SURNAMES = set(
    "김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민류나진지엄채원천방공"
    "현함변염여추도소석선설마길연위표명기반라왕금옥육인맹제모탁국어은편용예경봉사부"
)
# Last syllables that mark a header word or category rather than a person.
NON_NAME_ENDINGS = set("명일값자비과처량액율률별형료금간류번호도계표부실팀장소")
HANGUL = re.compile(r"^[가-힣]+$")

MANDATORY = {"RRN", "PHONE", "EMAIL", "CARD", "SSN", "DRIVER", "PASSPORT", "ACCOUNT"}
PENDING_KINDS = {"name", "identifier", "address", "birthdate"}
QUASI_KINDS = {"zip", "age", "gender", "date"}
DEFAULT_ACTION = {
    "name": "pseudonymize", "identifier": "pseudonymize",
    "address": "generalize", "birthdate": "generalize", "zip": "generalize",
    "age": "keep", "gender": "keep", "date": "keep", "free_text": "keep", None: "keep",
}
ENTITY_FALLBACK = {
    "환자번호": "PATIENT", "환자id": "PATIENT", "등록번호": "PATIENT", "차트번호": "CHART",
    "회원번호": "MEMBER", "회원id": "MEMBER", "고객번호": "CUSTOMER", "사번": "EMPLOYEE",
    "직원번호": "EMPLOYEE", "학번": "STUDENT", "아이디": "USER",
}

NAME_COL = re.compile(
    r"^(성명|이름|성함|환자명|고객명|회원명|보호자명|수진자명|피보험자명|담당자명|의사명|직원명|학생명)$"
    r"|^(full|first|last|given|family|patient|customer|member|user|person|guardian)?_?name$|^name_?(kr|en|ko)?$"
)
ID_TOKENS = {"id", "no", "num", "number", "code", "key", "mrn", "uid", "uuid", "pid"}
ID_COL_KR = re.compile(r"번호|아이디|사번|학번|차트|등록")
ZIP_COL = re.compile(r"zip|postal|postcode|우편")
ADDR_COL = re.compile(r"addr|address|주소|거주지|도로명|지번|street|residence")
BIRTH_COL = re.compile(r"birth|dob|생년월일|생일|출생")
AGE_COL = re.compile(r"^age$|나이|연령")
GENDER_COL = re.compile(r"^(sex|gender)$|성별")
FREE_COL = re.compile(r"memo|note|comment|remark|description|비고|메모|소견|내용|상담|기록|특이사항")
DATE_RE = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})([ T].*)?$|^(\d{4})(\d{2})(\d{2})$")
KR_ADDR = re.compile(r"\S+(시|도)\s+\S+(시|군|구)(\s|$)|\S+(로|길)\s*\d+")


PERSONAL_KEY = re.compile(
    r"성명|이름|성함|환자명|고객명|회원명|보호자|주민|rrn|ssn|social|phone|mobile|^tel|전화|연락처|휴대|"
    r"e-?mail|이메일|주소|address|^addr|birth|^dob$|생년월일|생일|patient|환자|member|회원|customer|고객|"
    r"^mrn$|차트|passport|여권|account|계좌|card|카드|license|면허|employee|사번|학번",
    re.I,
)
GENERIC_KEY = re.compile(r"^(name|full_?name|id|no|number|value)$", re.I)


def register_json_leaves(store: Store, fid: str, path: Path) -> int:
    """Mask values under personal-looking keys in a JSON file that is not a table.

    A leaf counts when its own key looks personal, or when it has a generic
    key (name, id) under a personal one ({"patient": {"name": ...}}), or
    when it is a Korean person name under a "name" key.
    """
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    if path.suffix.lower() == ".jsonl":
        data = []
        for line in text.splitlines():
            try:
                data.append(json.loads(line))
            except ValueError:
                continue
    else:
        data = json.loads(text)
    pairs: Dict[str, str] = {}

    def walk(node, key: str, personal_above: bool) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k), personal_above or bool(PERSONAL_KEY.search(str(k))))
            return
        if isinstance(node, list):
            for v in node:
                walk(v, key, personal_above)
            return
        if node is None or isinstance(node, bool):
            return
        value = str(node).strip()
        mine = bool(PERSONAL_KEY.search(key))
        generic_under_personal = personal_above and bool(GENERIC_KEY.match(key))
        korean_name = GENERIC_KEY.match(key) and name_like(value)
        if not (mine or generic_under_personal or korean_name) or value in pairs:
            return
        kind = classify(value)
        if korean_name or (kind is None and re.search(r"name|성명|이름|성함|명$", key, re.I)):
            kind = kind or "name"
        if not maskable(value, kind):
            return
        entity = kind if kind in MANDATORY else ("NAME" if kind == "name" else entity_name(key, fallback="ID"))
        pairs[value] = store.pseudonym(entity, norm_for(kind, value), raw=value)

    walk(data, "", False)
    store.add_lookups(fid, JSON_FIELDS_KEY, pairs.items())
    store.commit()
    return len(pairs)


JSON_FIELDS_KEY = "#json-fields"


def mask_key(table: str, column: str) -> str:
    """The masks of one column. A JSON list never equals JSON_FIELDS_KEY."""
    return json.dumps([table, column], ensure_ascii=False)


def _norm_col(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def name_like(value: str) -> bool:
    v = value.strip()
    return (2 <= len(v) <= 4 and bool(HANGUL.match(v)) and v[0] in SURNAMES
            and v[-1] not in NON_NAME_ENDINGS)


def shape(value: str) -> str:
    out = []
    for ch in value[:24]:
        if ch.isdigit():
            out.append("#")
        elif "A" <= ch <= "Z":
            out.append("A")
        elif "a" <= ch <= "z":
            out.append("a")
        elif "가" <= ch <= "힣":
            out.append("가")
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out) + ("…" if len(value) > 24 else "")


def _is_date(v: str) -> bool:
    m = DATE_RE.match(v)
    if not m:
        return False
    month, day = (int(m.group(2)), int(m.group(3))) if m.group(1) else (int(m.group(6)), int(m.group(7)))
    return 1 <= month <= 12 and 1 <= day <= 31


def _value_type(values: List[str]) -> str:
    if not values:
        return "empty"
    if all(re.fullmatch(r"-?\d+", v) for v in values):
        return "date" if all(len(v) == 8 and _is_date(v) for v in values) else "int"
    if all(re.fullmatch(r"-?\d+\.\d+|-?\d+", v) for v in values):
        return "float"
    if all(_is_date(v) for v in values):
        return "date"
    return "str"


def _id_column(name: str) -> bool:
    tokens = re.split(r"[_\s]+|(?<=[a-z])(?=[A-Z])", name.strip())
    return (tokens and tokens[-1].lower() in ID_TOKENS) or bool(ID_COL_KR.search(name))


def _infer_kind(name: str, values: List[str], vtype: str, unique: float, top_shape: float) -> Optional[str]:
    n = _norm_col(name)
    if values:
        # Mandatory kinds together, so a column mixing phones and emails is
        # forced, and the verdict does not depend on row order.
        kinds = Counter(k for k in (classify(v) for v in values) if k in MANDATORY)
        if kinds and sum(kinds.values()) / len(values) >= 0.5:
            return max(sorted(kinds), key=lambda k: kinds[k])
    if BIRTH_COL.search(n):
        return "birthdate"
    if ZIP_COL.search(n):
        return "zip"
    if ADDR_COL.search(n):
        return "address"
    if NAME_COL.search(n):
        return "name"
    if GENDER_COL.search(n):
        return "gender"
    if AGE_COL.search(n):
        return "age"
    if _id_column(name) and vtype in ("str", "int") and unique >= 0.5:
        return "identifier"
    if FREE_COL.search(n):
        return "free_text"
    if vtype == "date":
        return "date"
    distinct = len(set(values))
    if values and distinct >= 5 and sum(name_like(v) for v in values) / len(values) >= 0.6:
        return "name"
    if values and sum(bool(KR_ADDR.search(v)) for v in values) / len(values) >= 0.5:
        return "address"
    if vtype == "str" and values and sum(len(v) for v in values) / len(values) >= 30:
        return "free_text"
    if (vtype in ("str", "int") and len(values) >= 10 and unique >= 0.95 and top_shape >= 0.8
            and any(len(v) >= 4 and re.search(r"\d", v) for v in values[:50])):
        return "identifier"
    return None


def _status(kind: Optional[str]) -> str:
    if kind in MANDATORY:
        return "forced"
    if kind in PENDING_KINDS:
        return "pending"
    if kind in QUASI_KINDS:
        return "quasi"
    if kind == "free_text":
        return "free_text"
    return "kept"


def default_entity(column: str, kind: Optional[str]) -> str:
    if kind in MANDATORY:
        return kind
    if kind == "name":
        return "NAME"
    known = ENTITY_FALLBACK.get(_norm_col(column))
    return known or entity_name(column, fallback="ID")


def maskable(value: str, kind: Optional[str]) -> bool:
    """Whether a value is distinctive enough to replace wherever it appears."""
    v = value.strip()
    if kind == "name" and HANGUL.match(v):
        return len(v) >= 2
    if v.isdigit():
        return len(v) >= 5
    return len(v) >= 3


def norm_for(kind: Optional[str], value: str) -> str:
    if kind in MANDATORY:
        m = find_all(value)
        if len(m) == 1:
            return m[0].norm
    return value.strip()


def _header_is_data(table: Table) -> bool:
    header = table.columns
    if not header:
        return False
    if any(classify(h) for h in header):
        return True
    if sum(bool(re.fullmatch(r"[\d.\-/: ]+", h)) for h in header) >= max(1, len(header) // 2):
        return True
    same = 0
    for i, h in enumerate(header):
        vals = [r[i] for r in table.rows[:200] if r[i] is not None]
        if len(vals) >= 3:
            top, count = Counter(shape(v) for v in vals).most_common(1)[0]
            if count / len(vals) >= 0.8 and shape(h) == top and re.search(r"[#]", top):
                same += 1
    return same >= max(1, len(header) // 2)


def _names_in_header(header: List[str]) -> List[int]:
    hits = [i for i, h in enumerate(header)
            if name_like(re.sub(r"[\d_\-\s()./]+", "", h)) and len(re.sub(r"[\d_\-\s()./]+", "", h)) == 3]
    return hits if len(hits) >= 2 else []


def file_id(root: Path, path: Path) -> str:
    return hashlib.sha1(rel_path(root, path).encode()).hexdigest()[:12]


def rel_path(root: Path, path: Path) -> str:
    path = Path(path).resolve()
    try:
        return str(path.relative_to(Path(root).resolve()))
    except ValueError:
        return "_abs" + str(path)


def fingerprint(path: Path) -> str:
    # ctime moves on every write and cannot be set back the way mtime can.
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}:{st.st_ino}"


def load_tables(path: Path) -> List[Tuple[Table, bool, List[int]]]:
    """Tables with header fixes applied: (table, header_masked, masked_columns)."""
    out = []
    for t in read_tables(path):
        if _header_is_data(t):
            rows = [list(t.columns)] + t.rows
            cols = [f"col_{i + 1}" for i in range(len(t.columns))]
            out.append((Table(t.name, cols, rows), True, list(range(len(cols)))))
            continue
        masked = _names_in_header(t.columns)
        cols = [f"col_{i + 1}" if i in masked else c for i, c in enumerate(t.columns)]
        out.append((Table(t.name, cols, t.rows), False, masked))
    return out


def profile_columns(table: Table) -> List[Dict]:
    cols = []
    for i, name in enumerate(table.columns):
        values = [r[i].strip() for r in table.rows if r[i] is not None and r[i].strip()]
        unique = len(set(values)) / len(values) if values else 0.0
        shapes = Counter(shape(v) for v in values).most_common(3)
        top_shape = shapes[0][1] / len(values) if shapes else 0.0
        vtype = _value_type(values)
        kind = _infer_kind(name, values, vtype, unique, top_shape)
        cols.append({
            "index": i + 1,
            "name": name,
            "type": vtype,
            "non_null": len(values),
            "unique": round(unique, 3),
            "shapes": [(s, round(c / len(values), 2)) for s, c in shapes] if values else [],
            "kind": kind,
            "status": _status(kind),
            "default_action": "pseudonymize" if kind in MANDATORY else DEFAULT_ACTION.get(kind, "keep"),
            "entity": default_entity(name, kind),
        })
    return cols


def register_masks(store: Store, fid: str, table: Table, col: Dict) -> None:
    """Mask a mandatory or pending column's values everywhere until decided."""
    kind, i = col["kind"], col["index"] - 1
    if col["status"] not in ("forced", "pending"):
        return
    key = mask_key(table.name, col["name"])
    pairs = {}
    for r in table.rows:
        v = r[i]
        if v is None or not v.strip() or v in pairs or not maskable(v, kind):
            continue
        if col["status"] == "pending" and col["default_action"] == "generalize":
            pairs[v] = generalize(kind, v)
        else:
            pairs[v] = store.pseudonym(col["entity"], norm_for(kind, v), raw=v.strip())
    store.add_lookups(fid, key, pairs.items())


def _percent(ratio: float) -> int:
    return int(ratio * 100 + 0.5)


def _fmt_shapes(shapes) -> str:
    return ", ".join(f"`{s}` {_percent(p)}%" for s, p in shapes[:2]) or "-"


def render_card(rel: str, tables: List[Dict]) -> str:
    lines = [
        f"# deid-guard: `{rel}` (values withheld)",
        "",
        "deid-guard replaced this data file with its profile. No cell values are shown.",
        "",
    ]
    for t in tables:
        lines.append(f"## {t['name']} ({t['rows']} rows, {len(t['columns'])} columns)")
        if t["header_masked"]:
            lines.append("The first row looked like data, so columns are named `col_N` and that row counts as data.")
        elif t["masked_headers"]:
            lines.append("Some headers looked like personal names and are shown as `col_N`.")
        lines += ["", "| # | column | type | non-null | unique | shape | detected | status | default |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for c in t["columns"]:
            lines.append(
                f"| {c['index']} | {c['name']} | {c['type']} | {c['non_null']} | {_percent(c['unique'])}% "
                f"| {_fmt_shapes(c['shapes'])} | {c['kind'] or '-'} | {c['status']} | {c['default_action']} |"
            )
        lines.append("")
    lines += [
        "Status: `forced` = always pseudonymized; `pending` = masked everywhere until decided;",
        "`quasi` = may identify a person in combination; `free_text` = detectors scrub it inline.",
        "",
        "Next steps for the assistant:",
        "1. Judge from the column names and shapes which other columns identify a person",
        "   (patient/member/employee numbers, names, contact data, addresses, birth dates).",
        "2. Ask the user with AskUserQuestion which `pending`, `quasi`, `free_text` and suspicious",
        "   columns to de-identify and how: pseudonymize (stable token), generalize",
        "   (birthdate->year, date->month, address->district, zip->3 digits, age->5-year band),",
        "   drop, or keep. Name the columns; never guess values.",
        "3. Call `mcp__deid-guard__apply` with the file and the decisions. Columns left out get",
        "   the default shown above. Afterwards reading this file returns the de-identified copy.",
    ]
    return "\n".join(lines)


def profile_file(store: Store, root: Path, path: Path) -> Dict:
    root, path = Path(root), Path(path)
    rel, fid = rel_path(root, path), file_id(root, path)
    # Masks are never dropped here: a value that left the file may still sit
    # in a copy or an old output. Only a user-approved "keep" removes them.
    tables = []
    for table, header_masked, masked in load_tables(path):
        cols = profile_columns(table)
        for c in cols:
            register_masks(store, fid, table, c)
        tables.append({
            "name": table.name, "rows": len(table.rows), "columns": cols,
            "header_masked": header_masked, "masked_headers": masked,
        })
    if path.suffix.lower() in (".json", ".jsonl"):
        # Personal fields outside the tables, such as {"owner": {"성명": ...}}.
        register_json_leaves(store, fid, path)
    store.commit()
    result = {"file": rel, "file_id": fid, "fingerprint": fingerprint(path), "tables": tables}
    result["card"] = render_card(rel, tables)
    return result
