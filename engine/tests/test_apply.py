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
