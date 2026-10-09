"""Read and write the tabular formats the plugin guards, standard library only.

Every cell is kept as text (or None when empty), as it appears in the file.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

DATA_EXTENSIONS = (".csv", ".tsv", ".xlsx", ".json", ".jsonl")
ENCODINGS = ("utf-8-sig", "cp949", "latin-1")


class NotTabular(ValueError):
    """A JSON file that is not a list of records, such as a config file."""


@dataclass
class Table:
    name: str
    columns: List[str]
    rows: List[List[Optional[str]]] = field(default_factory=list)


def is_data_file(path: str) -> bool:
    return str(path).lower().endswith(DATA_EXTENSIONS)


def _decode(raw: bytes) -> str:
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("undecodable")


def _cell(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v if v != "" else None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


def _pad(rows: List[List[Optional[str]]], width: int) -> List[List[Optional[str]]]:
    return [(r + [None] * width)[:width] for r in rows]


def _read_delimited(path: Path, delimiter: str) -> Table:
    reader = csv.reader(io.StringIO(_decode(path.read_bytes()), newline=""), delimiter=delimiter)
    rows = [[_cell(c) for c in r] for r in reader if r]
    if not rows:
        return Table(path.stem, [], [])
    columns = [c or f"col_{i + 1}" for i, c in enumerate(rows[0])]
    width = max(len(columns), *(len(r) for r in rows))
    columns += [f"col_{i + 1}" for i in range(len(columns), width)]
    return Table(path.stem, columns, _pad(rows[1:], width))


def _records_table(name: str, records: list) -> Table:
    if len(records) < 2 or not all(isinstance(r, dict) for r in records):
        raise NotTabular(f"{name}: not a list of at least two records")
    columns: List[str] = []
    for rec in records:
        for k in rec if isinstance(rec, dict) else []:
            if k not in columns:
                columns.append(k)
    rows = [[_cell(rec.get(c)) if isinstance(rec, dict) else None for c in columns] for rec in records]
    return Table(name, columns, rows)


def _read_json(path: Path) -> Table:
    data = json.loads(_decode(path.read_bytes()))
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)), [data])
    return _records_table(path.stem, data if isinstance(data, list) else [data])


def _read_jsonl(path: Path) -> Table:
    lines = _decode(path.read_bytes()).splitlines()
    return _records_table(path.stem, [json.loads(l) for l in lines if l.strip()])


# --- xlsx ------------------------------------------------------------------

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_DATE_FMT_IDS = set(range(14, 23)) | {45, 46, 47}


def _xml(data: bytes):
    # xlsx parts never carry a DTD; refusing one rules out entity expansion
    # attacks without a third-party parser.
    if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data.upper():
        raise ValueError("xlsx part declares a DTD; refusing to parse")
    return ET.fromstring(data)


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _is_date_format(code: str) -> bool:
    code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", code).lower()
    return bool(re.search(r"[ymd]", code)) and "general" not in code


def _serial_to_text(serial: float, is_1904: bool) -> str:
    base = datetime(1904, 1, 1) if is_1904 else datetime(1899, 12, 30)
    dt = base + timedelta(days=serial)
    return dt.strftime("%Y-%m-%d") if float(serial).is_integer() else dt.strftime("%Y-%m-%d %H:%M:%S")


def _read_xlsx(path: Path) -> List[Table]:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        wb = _xml(z.read("xl/workbook.xml"))
        is_1904 = (wb.find("m:workbookPr", _NS) is not None
                   and wb.find("m:workbookPr", _NS).get("date1904") in ("1", "true"))
        rels = _xml(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels}

        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in _xml(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))

        date_styles = set()
        if "xl/styles.xml" in names:
            st = _xml(z.read("xl/styles.xml"))
            custom = {int(f.get("numFmtId")): f.get("formatCode", "")
                      for f in st.findall("m:numFmts/m:numFmt", _NS)}
            for i, xf in enumerate(st.findall("m:cellXfs/m:xf", _NS)):
                fid = int(xf.get("numFmtId", "0"))
                if fid in _DATE_FMT_IDS or (fid in custom and _is_date_format(custom[fid])):
                    date_styles.add(i)

        tables = []
        for sheet in wb.findall("m:sheets/m:sheet", _NS):
            target = targets[sheet.get(_REL)].lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            ws = _xml(z.read(target))
            grid: List[List[Optional[str]]] = []
            for row in ws.findall("m:sheetData/m:row", _NS):
                cells: dict = {}
                for c in row.findall("m:c", _NS):
                    t, v = c.get("t"), c.find("m:v", _NS)
                    if t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
                    elif v is None or v.text is None:
                        continue
                    elif t == "s":
                        val = shared[int(v.text)]
                    elif t in ("str", "e"):
                        val = v.text
                    elif t == "b":
                        val = "true" if v.text == "1" else "false"
                    elif int(c.get("s", "0")) in date_styles:
                        val = _serial_to_text(float(v.text), is_1904)
                    else:
                        val = _cell(float(v.text)) if re.match(r"^-?[\d.]+(E[-+]?\d+)?$", v.text, re.I) else v.text
                    cells[_col_index(c.get("r"))] = _cell(val)
                width = max(cells) + 1 if cells else 0
                grid.append([cells.get(i) for i in range(width)])
            grid = [r for r in grid if any(x is not None for x in r)]
            if not grid:
                tables.append(Table(sheet.get("name"), [], []))
                continue
            width = max(len(r) for r in grid)
            header = (grid[0] + [None] * width)[:width]
            columns = [h or f"col_{i + 1}" for i, h in enumerate(header)]
            tables.append(Table(sheet.get("name"), columns, _pad(grid[1:], width)))
        return tables


def read_tables(path) -> List[Table]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".csv":
        return [_read_delimited(path, ",")]
    if ext == ".tsv":
        return [_read_delimited(path, "\t")]
    if ext == ".json":
        return [_read_json(path)]
    if ext == ".jsonl":
        return [_read_jsonl(path)]
    if ext == ".xlsx":
        return _read_xlsx(path)
    raise ValueError(f"unsupported file type: {ext}")


def write_table(table: Table, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext in (".json", ".jsonl"):
        records = [dict(zip(table.columns, r)) for r in table.rows]
        if ext == ".json":
            path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t" if ext == ".tsv" else ",")
        w.writerow(table.columns)
        w.writerows([["" if c is None else c for c in r] for r in table.rows])
