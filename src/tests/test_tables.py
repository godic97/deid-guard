import json
import locale
import tempfile
import unittest
from pathlib import Path

from deidlib.tables import NotTabular, Table, is_data_file, read_tables, write_table
from tests.xlsx_fixture import write_xlsx


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_csv_utf8_with_bom(self):
        p = self.root / "a.csv"
        p.write_bytes("﻿이름,나이\n홍길동,30\n".encode("utf-8"))
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["이름", "나이"])
        self.assertEqual(t.rows, [["홍길동", "30"]])

    def test_csv_cp949(self):
        p = self.root / "a.csv"
        p.write_bytes("이름,나이\n홍길동,30\n".encode("cp949"))
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["홍길동", "30"]])

    def test_tsv(self):
        p = self.root / "a.tsv"
        p.write_text("id\tname\nP-1\tKim\n")
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["id", "name"])
        self.assertEqual(t.rows, [["P-1", "Kim"]])

    def test_delimited_table_is_named_after_the_file(self):
        for name, text in (("patients.csv", "id\nP-1\n"), ("visits.tsv", "id\nV-1\n")):
            with self.subTest(name):
                p = self.root / name
                p.write_text(text)
                (t,) = read_tables(p)
                self.assertEqual(t.name, Path(name).stem)

    def test_csv_with_carriage_return_line_endings(self):
        p = self.root / "a.csv"
        p.write_bytes(b"id,name\rP-1,Kim\rP-2,Lee\r")
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["id", "name"])
        self.assertEqual(t.rows, [["P-1", "Kim"], ["P-2", "Lee"]])

    def test_line_break_inside_a_quoted_csv_field_is_kept_as_is(self):
        p = self.root / "a.csv"
        p.write_bytes(b'id,memo\r\nP-1,"line 1\r\nline 2"\r\n')
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["P-1", "line 1\r\nline 2"]])

    def test_empty_csv(self):
        p = self.root / "empty.csv"
        p.write_bytes(b"")
        (t,) = read_tables(p)
        self.assertEqual((t.name, t.columns, t.rows), ("empty", [], []))

    def test_header_only_csv(self):
        p = self.root / "a.csv"
        p.write_text("id,name\n")
        (t,) = read_tables(p)
        self.assertEqual((t.name, t.columns, t.rows), ("a", ["id", "name"], []))

    def test_blank_header_cells_get_positional_names(self):
        p = self.root / "a.csv"
        p.write_text(",name,\n1,2,3\n")
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["col_1", "name", "col_3"])
        self.assertEqual(t.rows, [["1", "2", "3"]])

    def test_rows_longer_than_the_header_add_columns(self):
        p = self.root / "a.csv"
        p.write_text("a,b\n1,2,3,4\n5\n")
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["a", "b", "col_3", "col_4"])
        self.assertEqual(t.rows, [["1", "2", "3", "4"], ["5", None, None, None]])

    def test_short_rows_are_padded(self):
        p = self.root / "a.csv"
        p.write_text("a,b,c\n1,2\n")
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["1", "2", None]])

    def test_unsupported_extension(self):
        p = self.root / "notes.txt"
        p.write_text("x")
        with self.assertRaises(ValueError) as cm:
            read_tables(p)
        self.assertIn(".txt", str(cm.exception))

    # --- JSON ---------------------------------------------------------------

    def test_json_records(self):
        p = self.root / "a.json"
        p.write_text(json.dumps([{"id": 1, "name": "Kim"}, {"id": 2, "tags": ["x"]}]))
        (t,) = read_tables(p)
        self.assertEqual(t.name, "a")
        self.assertEqual(t.columns, ["id", "name", "tags"])
        self.assertEqual(t.rows, [["1", "Kim", None], ["2", None, '["x"]']])

    def test_json_values_become_text(self):
        p = self.root / "a.json"
        p.write_text(json.dumps([
            {"ok": True, "n": 3.0, "x": 2.5, "memo": "", "tags": ["한글"], "addr": {"시": "서울"}},
            {"ok": False, "n": -7, "x": None, "memo": "m", "tags": [], "addr": None},
        ]), encoding="utf-8")
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["ok", "n", "x", "memo", "tags", "addr"])
        self.assertEqual(t.rows, [
            ["true", "3", "2.5", None, '["한글"]', '{"시": "서울"}'],
            ["false", "-7", None, "m", "[]", None],
        ])

    def test_config_json_is_not_tabular(self):
        p = self.root / "package.json"
        p.write_text(json.dumps({"name": "my-app", "version": "1.0.0", "scripts": {"test": "x"}}))
        with self.assertRaises(NotTabular) as cm:
            read_tables(p)
        self.assertIn("package.json", str(cm.exception))

    def test_single_record_json_is_not_tabular(self):
        p = self.root / "a.json"
        p.write_text(json.dumps([{"name": "x"}]))
        with self.assertRaises(NotTabular):
            read_tables(p)

    def test_json_keyed_by_id_is_a_table(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({"P-1": {"name": "Kim"}, "P-2": {"name": "Lee"}}))
        (t,) = read_tables(p)
        self.assertEqual(t.name, "a")
        self.assertEqual(t.columns, ["_key", "name"])
        self.assertEqual(t.rows, [["P-1", "Kim"], ["P-2", "Lee"]])

    def test_pandas_split_json_is_a_table(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({"columns": ["id", "name"], "index": [0, 1], "data": [["P-1", "Kim"], ["P-2", "Lee"]]}))
        (t,) = read_tables(p)
        self.assertEqual(t.name, "a")
        self.assertEqual(t.columns, ["id", "name"])
        self.assertEqual(t.rows, [["P-1", "Kim"], ["P-2", "Lee"]])

    def test_pandas_split_with_fewer_columns_than_data(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({"columns": ["id"], "data": [["P-1", "Kim"], ["P-2"]]}))
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["id", "col_2"])
        self.assertEqual(t.rows, [["P-1", "Kim"], ["P-2", None]])

    def test_pandas_split_columns_that_are_not_a_list_are_ignored(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({"columns": "id", "data": [["P-1", "Kim"], ["P-2", "Lee"]]}))
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["col_1", "col_2"])
        self.assertEqual(t.rows, [["P-1", "Kim"], ["P-2", "Lee"]])

    def test_list_of_rows_json_is_a_table(self):
        p = self.root / "a.json"
        p.write_text(json.dumps([["P-1", "Kim"], ["P-2", "Lee"]]))
        (t,) = read_tables(p)
        self.assertEqual(t.name, "a")
        self.assertEqual(t.columns, ["col_1", "col_2"])
        self.assertEqual(t.rows, [["P-1", "Kim"], ["P-2", "Lee"]])

    def test_every_table_in_a_json_object_is_read(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({
            "meta": {"v": 1},
            "data": [["x", 1], ["y", 2]],
            "patients": [{"name": "Kim"}, {"name": "Lee"}],
        }))
        names = [t.name for t in read_tables(p)]
        self.assertEqual(names, ["data", "patients"])

    def test_values_in_a_json_object_that_are_not_tables_are_skipped(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({
            "tags": [1, 2],
            "empty": {},
            "patients": [{"name": "Kim"}, {"name": "Lee"}],
        }))
        (t,) = read_tables(p)
        self.assertEqual(t.name, "patients")
        self.assertEqual(t.rows, [["Kim"], ["Lee"]])

    def test_record_key_field_does_not_hide_the_id(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({"P-1": {"_key": "x"}, "P-2": {"_key": "y"}}))
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["__key", "_key"])
        self.assertEqual(t.rows, [["P-1", "x"], ["P-2", "y"]])

    def test_id_column_skips_every_name_the_records_use(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({"P-1": {"_key": "x", "__key": "y"}, "P-2": {"name": "Lee"}}))
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["___key", "_key", "__key", "name"])
        self.assertEqual(t.rows, [["P-1", "x", "y", None], ["P-2", None, None, "Lee"]])

    def test_jsonl(self):
        p = self.root / "visits.jsonl"
        p.write_text('{"id": "a"}\n\n{"id": "b"}\n')
        (t,) = read_tables(p)
        self.assertEqual(t.name, "visits")
        self.assertEqual(t.rows, [["a"], ["b"]])

    def test_jsonl_with_one_record_is_not_tabular(self):
        p = self.root / "visits.jsonl"
        p.write_text('{"id": "a"}\n')
        with self.assertRaises(NotTabular) as cm:
            read_tables(p)
        self.assertIn("visits", str(cm.exception))

    def test_jsonl_of_arrays_is_not_tabular(self):
        p = self.root / "a.jsonl"
        p.write_text('[1, 2]\n[3, 4]\n')
        with self.assertRaises(NotTabular):
            read_tables(p)

    # --- xlsx ---------------------------------------------------------------

    def test_xlsx_sheets_numbers_and_dates(self):
        p = self.root / "a.xlsx"
        write_xlsx(p, {
            "환자": [["id", "dob", "score"], ["P-1", ("date", 32874), 3.5], ["P-2", None, 4]],
            "Other": [["x"], ["y"]],
        })
        first, second = read_tables(p)
        self.assertEqual(first.name, "환자")
        self.assertEqual(first.columns, ["id", "dob", "score"])
        self.assertEqual(first.rows, [["P-1", "1990-01-01", "3.5"], ["P-2", None, "4"]])
        self.assertEqual(second.rows, [["y"]])

    def test_xlsx_date_with_a_time_of_day(self):
        p = self.root / "a.xlsx"
        write_xlsx(p, {"s": [["at"], [("date", 32874.75)]]})
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["1990-01-01 18:00:00"]])

    def test_xlsx_1904_date_system(self):
        for flag in ("1", "true"):
            with self.subTest(date1904=flag):
                p = self.root / f"a{flag}.xlsx"
                write_xlsx(p, {"s": [["dob"], [("date", 31412)], [("date", 0)]]}, workbook_pr=f'date1904="{flag}"')
                (t,) = read_tables(p)
                self.assertEqual(t.rows, [["1990-01-01"], ["1904-01-01"]])

    def test_xlsx_1900_date_system_when_the_1904_flag_is_off(self):
        for pr in ('date1904="0"', 'date1904="false"', ""):
            with self.subTest(workbook_pr=pr):
                p = self.root / "a.xlsx"
                write_xlsx(p, {"s": [["dob"], [("date", 32874)]]}, workbook_pr=pr)
                (t,) = read_tables(p)
                self.assertEqual(t.rows, [["1990-01-01"]])

    def test_xlsx_custom_number_formats(self):
        p = self.root / "a.xlsx"
        formats = {
            164: "yyyy-mm-dd",
            165: "DD/MM/YYYY",
            166: '[$-412]yyyy"년" m"월" d"일"',
            167: "0.00%",
            168: '#,##0" days"',
            169: "[Red]0.00",
            170: "General\\ \\m",
            171: None,
        }
        cell_xfs = (0, 14, 164, 165, 166, 167, 168, 169, 170, 171, None)
        cells = [
            ("date ymd", 2, "32874"),
            ("date dmy", 3, "32874"),
            ("date ko", 4, "32874"),
            ("percent", 5, "0.25"),
            ("days", 6, "12"),
            ("red", 7, "-3"),
            ("general", 8, "15"),
            ("no code", 9, "7"),
            ("no fmt id", 10, "8"),
        ]
        write_xlsx(p, {"s": [
            [name for name, _, _ in cells],
            [("xml", f'<c r="{{ref}}" s="{s}"><v>{v}</v></c>') for _, s, v in cells],
        ]}, num_fmts=formats, cell_xfs=cell_xfs)
        (t,) = read_tables(p)
        self.assertEqual(dict(zip(t.columns, t.rows[0])), {
            "date ymd": "1990-01-01",
            "date dmy": "1990-01-01",
            "date ko": "1990-01-01",
            "percent": "0.25",
            "days": "12",
            "red": "-3",
            "general": "15",
            "no code": "7",
            "no fmt id": "8",
        })

    def test_xlsx_cell_types(self):
        p = self.root / "a.xlsx"
        cells = [
            ("yes", '<c r="{ref}" t="b"><v>1</v></c>', "true"),
            ("no", '<c r="{ref}" t="b"><v>0</v></c>', "false"),
            ("error", '<c r="{ref}" t="e"><v>#N/A</v></c>', "#N/A"),
            ("dated error", '<c r="{ref}" s="1" t="e"><v>#VALUE!</v></c>', "#VALUE!"),
            ("formula text", '<c r="{ref}" t="str"><f>"00"&amp;7</f><v>007</v></c>', "007"),
            ("dated formula text", '<c r="{ref}" s="1" t="str"><v>N/A</v></c>', "N/A"),
            ("inline", '<c r="{ref}" t="inlineStr"><is><t>Kim</t></is></c>', "Kim"),
            ("iso date", '<c r="{ref}" t="d"><v>2020-01-02T03:04:05</v></c>', "2020-01-02T03:04:05"),
            ("exponent", '<c r="{ref}"><v>1.5E3</v></c>', "1500"),
            ("small exponent", '<c r="{ref}" t="n"><v>2.5e-1</v></c>', "0.25"),
            ("whole float", '<c r="{ref}"><v>-4.0</v></c>', "-4"),
        ]
        write_xlsx(p, {"s": [[name for name, _, _ in cells], [("xml", x) for _, x, _ in cells]]})
        (t,) = read_tables(p)
        self.assertEqual(dict(zip(t.columns, t.rows[0])), {name: want for name, _, want in cells})

    def test_xlsx_rich_and_spaced_strings(self):
        p = self.root / "a.xlsx"
        inline_rich = (
            '<c r="{ref}" t="inlineStr">\n  <is>\n    <r><t>Hong </t></r>\n'
            '    <r><rPr><b/></rPr><t/></r>\n    <r><t>Gildong</t></r>\n  </is>\n</c>'
        )
        write_xlsx(p, {"s": [
            ["spaced", "rich", "empty", "inline rich", "inline empty"],
            [
                ("si", "<si>\n  <t>Kim</t>\n</si>"),
                ("si", "<si><r><t>Hong </t></r><r><rPr><b/></rPr><t/></r><r><t>Gildong</t></r></si>"),
                ("si", "<si><t/></si>"),
                ("xml", inline_rich),
                ("xml", '<c r="{ref}" t="inlineStr"><is><t/></is></c>'),
            ],
        ]})
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["Kim", "Hong Gildong", None, "Hong Gildong", None]])

    def test_xlsx_cells_without_a_value_are_empty(self):
        p = self.root / "a.xlsx"
        write_xlsx(p, {"s": [
            ["a", "b", "c"],
            [("xml", '<c r="{ref}" s="1"/>'), ("xml", '<c r="{ref}"><v/></c>'), 5],
            [("xml", '<c r="{ref}" s="1"/>')],
            [6],
        ]})
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [[None, None, "5"], ["6", None, None]])

    def test_xlsx_empty_rows_are_dropped(self):
        p = self.root / "a.xlsx"
        write_xlsx(p, {"s": ['<row r="1"/>', ["id"], '<row r="3"/>', ["P-1"]]})
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["id"])
        self.assertEqual(t.rows, [["P-1"]])

    def test_xlsx_empty_sheet_is_an_empty_table(self):
        p = self.root / "a.xlsx"
        write_xlsx(p, {"Blank": [], "Spaces": ['<row r="1"/>'], "Data": [["id"], ["P-1"]]})
        tables = read_tables(p)
        self.assertEqual(
            [(t.name, t.columns, t.rows) for t in tables],
            [("Blank", [], []), ("Spaces", [], []), ("Data", ["id"], [["P-1"]])],
        )

    def test_xlsx_blank_header_cells_get_positional_names(self):
        p = self.root / "a.xlsx"
        write_xlsx(p, {"s": [[None, "name", None], ["P-1", "Kim", "x", "extra"]]})
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["col_1", "name", "col_3", "col_4"])
        self.assertEqual(t.rows, [["P-1", "Kim", "x", "extra"]])

    def test_xlsx_columns_past_z(self):
        p = self.root / "a.xlsx"
        header = [f"h{i}" for i in range(28)]
        write_xlsx(p, {"s": [header, [f"v{i}" for i in range(28)]]})
        (t,) = read_tables(p)
        self.assertEqual(t.columns, header)
        self.assertEqual(t.rows, [[f"v{i}" for i in range(28)]])

    def test_xlsx_sheet_parts_are_found_through_the_relationships(self):
        p = self.root / "a.xlsx"
        write_xlsx(
            p, {"First": [["a"], ["1"]], "Second": [["b"], ["2"]]},
            targets=["/xl/worksheets/first.xml", "XData.xml"],
        )
        self.assertEqual([(t.name, t.rows) for t in read_tables(p)], [("First", [["1"]]), ("Second", [["2"]])])

    def test_xlsx_part_with_a_doctype_is_refused(self):
        p = self.root / "a.xlsx"
        write_xlsx(p, {"s": [["id"], ["P-1"]]}, prolog="<!DOCTYPE worksheet>")
        with self.assertRaises(ValueError) as cm:
            read_tables(p)
        self.assertEqual(str(cm.exception), "xlsx part declares a DTD; refusing to parse")

    def test_xlsx_entity_declaration_is_refused_anywhere_in_the_part(self):
        p = self.root / "a.xlsx"
        prolog = "<!--" + "x" * 5000 + '--><!DOCTYPE worksheet [<!ENTITY who "Kim">]>'
        write_xlsx(p, {"s": [["id"], [("xml", '<c r="{ref}" t="inlineStr"><is><t>&who;</t></is></c>')]]},
                   prolog=prolog)
        with self.assertRaises(ValueError) as cm:
            read_tables(p)
        self.assertEqual(str(cm.exception), "xlsx part declares a DTD; refusing to parse")

    def test_xlsx_doctype_scan_covers_the_first_4096_bytes(self):
        # A DOCTYPE must sit in the prolog, so only the start of a part is
        # scanned for it; entity declarations are looked for everywhere.
        for start, refused in ((4087, True), (4088, False)):
            with self.subTest(start=start):
                p = self.root / f"a{start}.xlsx"
                prolog = "<!--" + "x" * (start - len("<!---->")) + "--><!DOCTYPE worksheet>"
                self.assertEqual(prolog.index("<!DOCTYPE"), start)
                write_xlsx(p, {"s": [["id"], ["P-1"]]}, prolog=prolog)
                if refused:
                    with self.assertRaises(ValueError):
                        read_tables(p)
                else:
                    (t,) = read_tables(p)
                    self.assertEqual(t.rows, [["P-1"]])

    def test_is_data_file(self):
        self.assertTrue(is_data_file("x/Data.CSV"))
        self.assertTrue(is_data_file("a.xlsx"))
        self.assertFalse(is_data_file("a.py"))


class WriteTest(unittest.TestCase):
    TABLE = Table("a", ["id", "memo"], [["1", "홍길동, 30"], ["2", None]])

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_csv_round_trip(self):
        p = self.root / "out" / "a.csv"
        write_table(Table("a", ["id", "memo"], [["1", "a,b"], ["2", None]]), p)
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["1", "a,b"], ["2", None]])

    def test_json_round_trip(self):
        p = self.root / "a.json"
        write_table(Table("a", ["id"], [["1"], [None]]), p)
        self.assertEqual(json.loads(p.read_text()), [{"id": "1"}, {"id": None}])

    def test_csv_output(self):
        p = self.root / "a.csv"
        write_table(self.TABLE, p)
        self.assertEqual(p.read_bytes().decode("utf-8"), 'id,memo\r\n1,"홍길동, 30"\r\n2,\r\n')

    def test_tsv_output(self):
        p = self.root / "A.TSV"
        write_table(self.TABLE, p)
        self.assertEqual(p.read_bytes().decode("utf-8"), "id\tmemo\r\n1\t홍길동, 30\r\n2\t\r\n")

    def test_json_output(self):
        p = self.root / "a.json"
        write_table(self.TABLE, p)
        self.assertEqual(
            p.read_bytes().decode("utf-8"),
            '[\n {\n  "id": "1",\n  "memo": "홍길동, 30"\n },\n {\n  "id": "2",\n  "memo": null\n }\n]',
        )

    def test_jsonl_output(self):
        p = self.root / "a.jsonl"
        write_table(self.TABLE, p)
        self.assertEqual(
            p.read_bytes().decode("utf-8"),
            '{"id": "1", "memo": "홍길동, 30"}\n{"id": "2", "memo": null}\n',
        )
        (t,) = read_tables(p)
        self.assertEqual(t.rows, self.TABLE.rows)

    def test_output_is_utf8_whatever_the_locale(self):
        # A Korean Windows locale defaults to cp949; output must still be UTF-8.
        old = locale.setlocale(locale.LC_CTYPE)
        self.addCleanup(locale.setlocale, locale.LC_CTYPE, old)
        locale.setlocale(locale.LC_CTYPE, "C")
        for name in ("a.csv", "a.tsv", "a.json", "a.jsonl"):
            with self.subTest(name):
                p = self.root / name
                write_table(self.TABLE, p)
                self.assertIn("홍길동", p.read_bytes().decode("utf-8"))



class XlsxFormatBugTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_escaped_letters_in_a_number_format_do_not_make_it_a_date(self):
        p = self.root / "m.xlsx"
        write_xlsx(p, {"s": [["len"], [("date", 1.5)]]}, num_fmts={164: "0.0\\ \\m"}, cell_xfs=(0, 164))
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["1.5"]])

    def test_phonetic_runs_are_not_part_of_the_text(self):
        p = self.root / "p.xlsx"
        si = "<si><t>東京</t><rPh sb=\"0\" eb=\"2\"><t>トウキョウ</t></rPh><phoneticPr fontId=\"1\"/></si>"
        rich = "<si><r><t>大</t></r><r><t>阪</t></r><rPh sb=\"0\" eb=\"2\"><t>オオサカ</t></rPh></si>"
        write_xlsx(p, {"s": [["city"], [("si", si)], [("si", rich)]]})
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["東京"], ["大阪"]])

    def test_inline_string_without_text_is_empty(self):
        p = self.root / "i.xlsx"
        write_xlsx(p, {"s": [["a", "b"], [("xml", '<c r="{ref}" t="inlineStr"/>'), "x"]]})
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [[None, "x"]])

if __name__ == "__main__":
    unittest.main()
