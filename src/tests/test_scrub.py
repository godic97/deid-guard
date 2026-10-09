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
        out, hits = scrub(self.store, "홍길동이 내원함, 홍길동님")
        self.assertEqual(out, "NAME_000001이 내원함, NAME_000001님")
        self.assertEqual(hits, 2)

    def test_lookup_value_inside_underscored_name(self):
        self.store.add_lookups("f", "id", [("P-0012", "PATIENT_000001")])
        out, hits = scrub(self.store, "saved P-0012_report.csv")
        self.assertEqual(out, "saved PATIENT_000001_report.csv")
        self.assertEqual(hits, 1)

    def test_numeric_lookup_printed_as_float(self):
        self.store.add_lookups("f", "id", [("102345", "PATIENT_000002")])
        out, hits = scrub(self.store, "0    102345.0\n1    102345")
        self.assertEqual(out, "0    PATIENT_000002\n1    PATIENT_000002")
        self.assertEqual(hits, 2)

    def test_multiword_lookup(self):
        self.store.add_lookups("f", "addr", [("서울특별시 강남구 테헤란로 1", "서울특별시 강남구")])
        out, hits = scrub(self.store, "주소: 서울특별시 강남구 테헤란로 1 (본점)")
        self.assertEqual(out, "주소: 서울특별시 강남구 (본점)")
        self.assertEqual(hits, 1)

    def test_every_multiword_occurrence_is_counted(self):
        self.store.add_lookups("f", "addr", [
            ("서울특별시 강남구 테헤란로 1", "서울특별시 강남구"),
            ("경기도 성남시 분당구 판교역로 2", "경기도 성남시 분당구"),
        ])
        out, hits = scrub(self.store, "A: 서울특별시 강남구 테헤란로 1 / B: 경기도 성남시 분당구 판교역로 2 / "
                                      "C: 서울특별시 강남구 테헤란로 1")
        self.assertEqual(out, "A: 서울특별시 강남구 / B: 경기도 성남시 분당구 / C: 서울특별시 강남구")
        self.assertEqual(hits, 3)

    def test_hits_add_up_across_multiword_detector_and_lookup_passes(self):
        self.store.add_lookups("f", "name", [("홍길동", "NAME_000001")])
        self.store.add_lookups("f", "addr", [("서울특별시 강남구 테헤란로 1", "서울특별시 강남구")])
        out, hits = scrub(self.store, "홍길동, 서울특별시 강남구 테헤란로 1, 보호자 010-9000-8000, 홍길동님")
        self.assertEqual(out, "NAME_000001, 서울특별시 강남구, 보호자 PHONE_000001, NAME_000001님")
        self.assertEqual(hits, 4)

    def test_restore_brings_back_the_detected_value_as_written(self):
        out, _ = scrub(self.store, "연락처 010-1234-5678")
        self.assertEqual(out, "연락처 PHONE_000001")
        self.assertEqual(restore(self.store, out), "연락처 010-1234-5678")

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



class RestoreParticleTest(unittest.TestCase):
    def test_restore_undoes_a_token_followed_by_a_korean_particle(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d))
            try:
                store.add_lookups("f", "name", [("홍길동", store.pseudonym("NAME", "홍길동"))])
                text = "홍길동이 내원함, 홍길동 보호자"
                scrubbed, _ = scrub(store, text)
                self.assertEqual(scrubbed, "NAME_000001이 내원함, NAME_000001 보호자")
                self.assertEqual(restore(store, scrubbed), text)
            finally:
                store.close()

    def test_restore_does_not_match_inside_a_longer_token(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d))
            try:
                store.pseudonym("NAME", "홍길동")
                self.assertEqual(restore(store, "XNAME_000001 NAME_0000012"), "XNAME_000001 NAME_0000012")
            finally:
                store.close()

if __name__ == "__main__":
    unittest.main()
