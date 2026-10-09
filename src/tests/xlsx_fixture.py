"""Builds tiny .xlsx files with the standard library, for tests only."""
import zipfile
from xml.sax.saxutils import escape

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, sheets, *, workbook_pr=None, num_fmts=None, cell_xfs=(0, 14), targets=None, prolog=""):
    """Write an .xlsx file.

    sheets: {name: rows}. A row is a list of cells, or a raw ``<row>`` element
    string (for example ``'<row r="2"/>'``). A cell is one of:

    - None: no cell
    - str: a shared string
    - int or float: a number
    - ("date", serial): a number in style 1 (built-in date format 14 by default)
    - ("si", xml): a shared string given as a raw ``<si>`` element
    - ("xml", template): a raw ``<c>`` element; ``{ref}`` becomes the cell reference

    workbook_pr: attributes of a ``<workbookPr>`` element, such as
        ``'date1904="1"'``; None leaves the element out.
    num_fmts: {numFmtId: formatCode} for custom number formats; a formatCode of
        None leaves the attribute out.
    cell_xfs: the numFmtId of each cell style, by style index; None leaves the
        attribute out.
    targets: the relationship target of each sheet part; a leading "/" makes it
        absolute, otherwise it is relative to ``xl/``.
    prolog: text placed before the root element of every sheet part.
    """
    strings = []

    def sid(si_xml):
        if si_xml not in strings:
            strings.append(si_xml)
        return strings.index(si_xml)

    sheet_xml = []
    for rows in sheets.values():
        out = []
        for r, row in enumerate(rows, start=1):
            if isinstance(row, str):
                out.append(row)
                continue
            cells = []
            for c, v in enumerate(row):
                ref = f"{_col(c)}{r}"
                if v is None:
                    continue
                if isinstance(v, tuple) and v[0] == "xml":
                    cells.append(v[1].replace("{ref}", ref))
                elif isinstance(v, tuple) and v[0] == "si":
                    cells.append(f'<c r="{ref}" t="s"><v>{sid(v[1])}</v></c>')
                elif isinstance(v, tuple):
                    cells.append(f'<c r="{ref}" s="1"><v>{v[1]}</v></c>')
                elif isinstance(v, str):
                    cells.append(f'<c r="{ref}" t="s"><v>{sid(f"<si><t>{escape(v)}</t></si>")}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_xml.append(
            f'{prolog}<worksheet xmlns="{_MAIN}">'
            f'<sheetData>{"".join(out)}</sheetData></worksheet>'
        )

    if targets is None:
        targets = [f"worksheets/sheet{i}.xml" for i in range(1, len(sheets) + 1)]
    ns = f'xmlns="{_MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    pr = "" if workbook_pr is None else f"<workbookPr {workbook_pr}/>"
    workbook = f'<workbook {ns}>{pr}<sheets>' + "".join(
        f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(sheets, start=1)
    ) + "</sheets></workbook>"
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="{escape(t)}"/>'
        for i, t in enumerate(targets, start=1)
    ) + "</Relationships>"
    shared = f'<sst xmlns="{_MAIN}">' + "".join(strings) + "</sst>"
    fmts = ""
    if num_fmts:
        fmts = "<numFmts>" + "".join(
            f'<numFmt numFmtId="{i}"/>' if code is None
            else f'<numFmt numFmtId="{i}" formatCode="{escape(code, {chr(34): "&quot;"})}"/>'
            for i, code in num_fmts.items()
        ) + "</numFmts>"
    xfs = "".join("<xf/>" if f is None else f'<xf numFmtId="{f}"/>' for f in cell_xfs)
    styles = f'<styleSheet xmlns="{_MAIN}">{fmts}<cellXfs count="{len(cell_xfs)}">{xfs}</cellXfs></styleSheet>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/styles.xml", styles)
        for t, xml in zip(targets, sheet_xml):
            z.writestr(t[1:] if t.startswith("/") else "xl/" + t, xml)
