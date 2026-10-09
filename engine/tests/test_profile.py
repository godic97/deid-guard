import tempfile
import unittest
from pathlib import Path

from deidlib.profile import profile_file
from deidlib.store import Store
from tests.fixtures import patient_rows, write_patients


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)
        self.path = write_patients(self.root / "data" / "patients.csv")
        self.result = profile_file(self.store, self.root, self.path)
        self.cols = {c["name"]: c for c in self.result["tables"][0]["columns"]}

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_kinds(self):
        kinds = {n: c["kind"] for n, c in self.cols.items()}
        self.assertEqual(kinds, {
            "patient_no": "identifier", "성명": "name", "주민번호": "RRN", "연락처": "PHONE",
            "생년월일": "birthdate", "주소": "address", "우편번호": "zip", "성별": "gender",
            "나이": "age", "방문일": "date", "진단": None, "메모": "free_text",
        })

    def test_status(self):
        self.assertEqual(self.cols["주민번호"]["status"], "forced")
        self.assertEqual(self.cols["patient_no"]["status"], "pending")
        self.assertEqual(self.cols["주소"]["status"], "pending")
        self.assertEqual(self.cols["나이"]["status"], "quasi")
        self.assertEqual(self.cols["진단"]["status"], "kept")

    def test_card_has_no_raw_values(self):
        card = self.result["card"]
        for row in patient_rows():
            for value in row:
                if len(value) >= 4 and not value.isdigit():
                    self.assertNotIn(value, card)
        self.assertIn("patient_no", card)
        self.assertIn("mcp__deid-guard__apply", card)

    def test_shape_is_reported(self):
        self.assertEqual(self.cols["patient_no"]["shapes"][0][0], "A-######")

    def test_pending_values_are_masked_before_any_decision(self):
        self.assertEqual(self.store.lookup("김민준"), "NAME_000001")
        self.assertEqual(self.store.lookup("P-100101"), "PATIENT_NO_000001")
        self.assertEqual(self.store.lookup("900110-1234567"), "RRN_000001")
        self.assertEqual(self.store.lookup("1960-01-10"), "1960")

    def test_short_numbers_are_not_masked_globally(self):
        self.assertIsNone(self.store.lookup("35"))


class HeaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_headerless_file_masks_header_and_keeps_first_row_as_data(self):
        path = write_patients(self.root / "raw.csv", header=False)
        result = profile_file(self.store, self.root, path)
        table = result["tables"][0]
        self.assertTrue(table["header_masked"])
        self.assertEqual(table["columns"][0]["name"], "col_1")
        self.assertEqual(table["rows"], 20)
        self.assertNotIn("P-100101", result["card"])
        self.assertEqual(self.store.lookup("P-100101"), "COL_1_000001")

    def test_person_names_in_header_are_masked(self):
        path = self.root / "pivot.csv"
        path.write_text("구분,김민준_2024,이서연_2024,박도윤_2024\n방문수,3,4,5\n진료비,10,20,30\n")
        result = profile_file(self.store, self.root, path)
        names = [c["name"] for c in result["tables"][0]["columns"]]
        self.assertEqual(names, ["구분", "col_2", "col_3", "col_4"])
        self.assertNotIn("김민준", result["card"])


if __name__ == "__main__":
    unittest.main()
