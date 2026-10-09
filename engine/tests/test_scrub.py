import tempfile
import unittest
from pathlib import Path

from deidlib.scrub import restore, scrub
from deidlib.store import Store


class ScrubTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name))

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_detected_values_get_consistent_tokens(self):
        out, hits = scrub(self.store, "연락처 010-1234-5678 / 01012345678")
        self.assertEqual(out, "연락처 PHONE_000001 / PHONE_000001")
        self.assertEqual(hits, 2)

    def test_lookup_value_followed_by_korean_particle(self):
        self.store.add_lookups("f", "name", [("홍길동", "NAME_000001")])
        out, _ = scrub(self.store, "홍길동이 내원함, 홍길동님")
        self.assertEqual(out, "NAME_000001이 내원함, NAME_000001님")

    def test_lookup_value_inside_underscored_name(self):
        self.store.add_lookups("f", "id", [("P-0012", "PATIENT_000001")])
        out, _ = scrub(self.store, "saved P-0012_report.csv")
        self.assertEqual(out, "saved PATIENT_000001_report.csv")

    def test_numeric_lookup_printed_as_float(self):
        self.store.add_lookups("f", "id", [("102345", "PATIENT_000002")])
        out, _ = scrub(self.store, "0    102345.0\n1    102345")
        self.assertEqual(out, "0    PATIENT_000002\n1    PATIENT_000002")

    def test_multiword_lookup(self):
        self.store.add_lookups("f", "addr", [("서울특별시 강남구 테헤란로 1", "서울특별시 강남구")])
        out, hits = scrub(self.store, "주소: 서울특별시 강남구 테헤란로 1 (본점)")
        self.assertEqual(out, "주소: 서울특별시 강남구 (본점)")
        self.assertEqual(hits, 1)

    def test_lookup_does_not_match_inside_a_longer_word(self):
        self.store.add_lookups("f", "id", [("A123", "ID_000001")])
        out, hits = scrub(self.store, "XA1234 and A123")
        self.assertEqual(out, "XA1234 and ID_000001")
        self.assertEqual(hits, 1)

    def test_clean_text_is_unchanged(self):
        text = "def f(x):\n    return x + 1  # 2024-01-01"
        self.assertEqual(scrub(self.store, text), (text, 0))

    def test_restore_known_tokens_only(self):
        token = self.store.pseudonym("PATIENT", "P-0012")
        text = f"rows where id == '{token}' and ERROR_000001"
        self.assertEqual(restore(self.store, text), "rows where id == 'P-0012' and ERROR_000001")


if __name__ == "__main__":
    unittest.main()
