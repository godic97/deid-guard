import json
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

    def test_short_rows_are_padded(self):
        p = self.root / "a.csv"
        p.write_text("a,b,c\n1,2\n")
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["1", "2", None]])

    def test_json_records(self):
        p = self.root / "a.json"
        p.write_text(json.dumps([{"id": 1, "name": "Kim"}, {"id": 2, "tags": ["x"]}]))
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["id", "name", "tags"])
        self.assertEqual(t.rows, [["1", "Kim", None], ["2", None, '["x"]']])

    def test_config_json_is_not_tabular(self):
        p = self.root / "package.json"
        p.write_text(json.dumps({"name": "my-app", "version": "1.0.0", "scripts": {"test": "x"}}))
        with self.assertRaises(NotTabular):
            read_tables(p)

    def test_single_record_json_is_not_tabular(self):
        p = self.root / "a.json"
        p.write_text(json.dumps([{"name": "x"}]))
        with self.assertRaises(NotTabular):
            read_tables(p)

    def test_json_keyed_by_id_is_a_table(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({"P-1": {"name": "Kim"}, "P-2": {"name": "Lee"}}))
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["_key", "name"])
        self.assertEqual(t.rows, [["P-1", "Kim"], ["P-2", "Lee"]])

    def test_pandas_split_json_is_a_table(self):
        p = self.root / "a.json"
        p.write_text(json.dumps({"columns": ["id", "name"], "index": [0, 1], "data": [["P-1", "Kim"], ["P-2", "Lee"]]}))
        (t,) = read_tables(p)
        self.assertEqual(t.columns, ["id", "name"])
        self.assertEqual(t.rows, [["P-1", "Kim"], ["P-2", "Lee"]])

    def test_list_of_rows_json_is_a_table(self):
        p = self.root / "a.json"
        p.write_text(json.dumps([["P-1", "Kim"], ["P-2", "Lee"]]))
        (t,) = read_tables(p)
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

    def test_jsonl(self):
        p = self.root / "a.jsonl"
        p.write_text('{"id": "a"}\n\n{"id": "b"}\n')
        (t,) = read_tables(p)
        self.assertEqual(t.rows, [["a"], ["b"]])

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

    def test_is_data_file(self):
        self.assertTrue(is_data_file("x/Data.CSV"))
        self.assertTrue(is_data_file("a.xlsx"))
        self.assertFalse(is_data_file("a.py"))


class WriteTest(unittest.TestCase):
    def test_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out" / "a.csv"
            write_table(Table("a", ["id", "memo"], [["1", "a,b"], ["2", None]]), p)
            (t,) = read_tables(p)
            self.assertEqual(t.rows, [["1", "a,b"], ["2", None]])

    def test_json_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            write_table(Table("a", ["id"], [["1"], [None]]), p)
            self.assertEqual(json.loads(p.read_text()), [{"id": "1"}, {"id": None}])


if __name__ == "__main__":
    unittest.main()
