import csv
import tempfile
import unittest
from pathlib import Path

from deidlib.apply import ApplyError, apply_decisions
from deidlib.guard import guard
from deidlib.store import Store
from tests.fixtures import HEADER, write_patients


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)
        self.path = write_patients(self.root / "data" / "patients.csv")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def apply(self, decisions=(), path=None):
        return apply_decisions(self.store, self.root, path or self.path, list(decisions))

    def test_defaults(self):
        result = self.apply()
        header, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual(header, HEADER)
        first = dict(zip(header, rows[0]))
        self.assertEqual(first["patient_no"], "PATIENT_NO_000001")
        self.assertEqual(first["성명"], "NAME_000001")
        self.assertEqual(first["주민번호"], "RRN_000001")
        self.assertEqual(first["연락처"], "PHONE_000001")
        self.assertEqual(first["생년월일"], "1960")
        self.assertEqual(first["주소"], "서울특별시 강남구")
        self.assertEqual(first["우편번호"], "062**")
        self.assertEqual(first["나이"], "20")
        self.assertEqual(first["진단"], "감기")
        self.assertEqual(first["메모"], "NAME_000001 환자, 보호자 연락처 PHONE_000021")

    def test_output_path_is_under_deid_out(self):
        result = self.apply()
        self.assertEqual(result["outputs"], [".deid/out/data/patients.csv"])

    def test_generalized_values_are_generalized_in_tool_output_too(self):
        self.apply()
        self.assertEqual(self.store.lookup("06200"), "062**")

    def test_keep_unmasks_a_pending_column(self):
        self.apply([{"column": "patient_no", "action": "keep"}])
        self.assertIsNone(self.store.lookup("P-100101"))

    def test_drop_removes_column_and_redacts_its_values(self):
        result = self.apply([{"column": "성명", "action": "drop"}])
        header, _ = read_csv(self.root / result["outputs"][0])
        self.assertNotIn("성명", header)
        self.assertEqual(self.store.lookup("김민준"), "[REDACTED]")

    def test_forced_column_cannot_be_kept(self):
        result = self.apply([{"column": "주민번호", "action": "keep"}])
        _, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual(rows[0][2], "RRN_000001")
        self.assertIn("주민번호", result["summary"])
        self.assertIn("forced", result["summary"])

    def test_generalize_quasi_column(self):
        result = self.apply([{"column": "나이", "action": "generalize"}, {"column": "방문일", "action": "generalize"}])
        header, rows = read_csv(self.root / result["outputs"][0])
        first = dict(zip(header, rows[0]))
        self.assertEqual(first["나이"], "20-24")
        self.assertEqual(first["방문일"], "2024-03")

    def test_pseudonymize_any_column_with_custom_entity(self):
        result = self.apply([{"column": "진단", "action": "pseudonymize", "entity": "DX"}])
        _, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual(rows[0][10], "DX_000001")

    def test_tokens_are_shared_across_files(self):
        other = self.root / "data" / "visits.csv"
        other.write_text("patient_no,cost\nP-100102,100\nP-100101,200\n")
        self.apply()
        result = self.apply(path=other, decisions=[{"column": "patient_no", "action": "pseudonymize", "entity": "PATIENT_NO"}])
        _, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual([r[0] for r in rows], ["PATIENT_NO_000002", "PATIENT_NO_000001"])

    def test_dry_run_reports_unmasked_pending_columns_without_writing(self):
        result = apply_decisions(self.store, self.root, self.path,
                                 [{"column": "patient_no", "action": "keep"}, {"column": "진단", "action": "keep"}],
                                 dry_run=True)
        self.assertEqual(result["unmasks"], ["patient_no"])
        self.assertEqual(result["columns"], ["patient_no"])
        self.assertEqual(result["file"], "data/patients.csv")
        self.assertFalse((self.root / ".deid" / "out").exists())

    def test_sheet_names_cannot_escape_the_output_dir(self):
        from tests.xlsx_fixture import write_xlsx
        book = self.root / "book.xlsx"
        write_xlsx(book, {"../../escape": [["a"], ["1"]], "ok": [["b"], ["2"]]})
        result = self.apply(path=book)
        out = (self.root / ".deid" / "out").resolve()
        for rel in result["outputs"]:
            self.assertTrue((self.root / rel).resolve().is_relative_to(out), rel)
        self.assertEqual(len(set(result["outputs"])), 2)

    def test_kind_override_cannot_unmask_a_forced_column(self):
        result = self.apply([{"column": "주민번호", "action": "keep", "kind": "zip"}])
        _, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual(rows[0][2], "RRN_000001")

    def test_kind_override_cannot_change_how_a_pending_column_is_generalized(self):
        with self.assertRaises(ApplyError):
            self.apply([{"column": "성명", "action": "generalize", "kind": "address"}])

    def test_kind_override_still_works_for_other_columns(self):
        other = self.root / "v.csv"
        other.write_text("bd\n1990-03-04\n")
        result = self.apply(path=other, decisions=[{"column": "bd", "action": "generalize", "kind": "birthdate"}])
        _, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual(rows[0][0], "1990")

    def test_json_with_several_tables_gets_one_copy_per_table(self):
        p = self.root / "multi.json"
        p.write_text('{"visits": [{"id": "V-1"}, {"id": "V-2"}], "staff": [{"성명": "윤서하"}, {"성명": "한지우"}]}')
        result = self.apply(path=p)
        self.assertEqual(len(result["outputs"]), 2)
        self.assertTrue(result["read_path"].endswith("multi.json.md"))

    def test_unknown_column_is_an_error(self):
        with self.assertRaises(ApplyError):
            self.apply([{"column": "nope", "action": "keep"}])

    def test_generalize_unsupported_kind_is_an_error(self):
        with self.assertRaises(ApplyError):
            self.apply([{"column": "성명", "action": "generalize"}])

    def test_summary_has_no_raw_values(self):
        summary = self.apply()["summary"]
        self.assertNotIn("김민준", summary)
        self.assertNotIn("P-100101", summary)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)
        self.path = write_patients(self.root / "patients.csv")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_first_touch_profiles_and_points_at_card(self):
        g = guard(self.store, self.root, self.path)
        self.assertEqual(g["status"], "pending")
        self.assertEqual(g["read_path"], str(self.root / ".deid/cards/patients.csv.md"))
        self.assertIn("values withheld", (self.root / ".deid/cards/patients.csv.md").read_text())

    def test_after_apply_points_at_copy(self):
        guard(self.store, self.root, self.path)
        apply_decisions(self.store, self.root, self.path, [])
        g = guard(self.store, self.root, self.path)
        self.assertEqual(g["status"], "decided")
        self.assertEqual(g["read_path"], str(self.root / ".deid/out/patients.csv"))

    def test_masks_survive_a_file_rewritten_as_config_json(self):
        import json as _json
        json_path = self.root / "people.json"
        json_path.write_text(_json.dumps([{"성명": "박지민", "x": 1}, {"성명": "최유리", "x": 2}]))
        guard(self.store, self.root, json_path)
        self.assertEqual(self.store.lookup("박지민"), "NAME_000001")
        json_path.write_text(_json.dumps({"name": "my-app"}))
        self.assertEqual(guard(self.store, self.root, json_path)["status"], "not_data")
        self.assertEqual(self.store.lookup("박지민"), "NAME_000001")
        self.assertEqual(self.store.lookup("최유리"), "NAME_000002")

    def test_masks_survive_rows_removed_from_the_file(self):
        guard(self.store, self.root, self.path)
        write_patients(self.path, n=3)
        apply_decisions(self.store, self.root, self.path, [])
        self.assertEqual(self.store.lookup("홍길동"), "NAME_000020")

    def test_keep_on_a_column_that_no_longer_looks_pending_keeps_old_masks(self):
        guard(self.store, self.root, self.path)
        self.assertIsNotNone(self.store.lookup("P-100101"))
        lines = self.path.read_bytes().decode("utf-8").splitlines()
        diluted = [lines[0]] + [l.replace(l.split(",")[0], "P-100101", 1) for l in lines[1:]]
        self.path.write_bytes("\n".join(diluted).encode("utf-8"))
        apply_decisions(self.store, self.root, self.path, [{"column": "patient_no", "action": "keep"}])
        self.assertIsNotNone(self.store.lookup("P-100101"))

    def test_content_change_with_restored_mtime_is_noticed(self):
        import os
        guard(self.store, self.root, self.path)
        st = os.stat(self.path)
        self.path.write_bytes(self.path.read_bytes().replace(b"P-100101", b"Q-100101"))
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(os.stat(self.path).st_size, st.st_size)
        guard(self.store, self.root, self.path)
        self.assertIsNotNone(self.store.lookup("Q-100101"))

    def test_personal_fields_beside_tables_in_json_are_masked(self):
        p = self.root / "mixed.json"
        p.write_text('{"rows": [{"a": 1}, {"a": 2}], "owner": {"성명": "윤서하", "phone": "010-7777-8888"}}')
        guard(self.store, self.root, p)
        self.assertIsNotNone(self.store.lookup("윤서하"))

    def test_personal_fields_in_non_tabular_json_are_masked(self):
        p = self.root / "one.json"
        p.write_text('{"name": "my-app", "환자": {"성명": "박지민", "연락처": "010-3333-4444", "patient_id": "H-2024-0012"}}')
        self.assertEqual(guard(self.store, self.root, p)["status"], "not_data")
        self.assertIsNotNone(self.store.lookup("박지민"))
        self.assertIsNotNone(self.store.lookup("H-2024-0012"))
        self.assertIsNone(self.store.lookup("my-app"))

    def test_changed_file_is_reapplied_with_same_decisions(self):
        apply_decisions(self.store, self.root, self.path, [{"column": "나이", "action": "drop"}])
        write_patients(self.path, n=25)
        g = guard(self.store, self.root, self.path)
        self.assertEqual(g["status"], "decided")
        header, rows = read_csv(self.root / ".deid/out/patients.csv")
        self.assertEqual(len(rows), 25)
        self.assertNotIn("나이", header)


if __name__ == "__main__":
    unittest.main()
