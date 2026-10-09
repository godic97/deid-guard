import os
import tempfile
import unittest
from pathlib import Path

from deidlib.store import Store, entity_name


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_same_value_gets_same_token(self):
        a = self.store.pseudonym("PATIENT", "P-001")
        b = self.store.pseudonym("PATIENT", "P-001")
        c = self.store.pseudonym("PATIENT", "P-002")
        self.assertEqual(a, "PATIENT_000001")
        self.assertEqual(b, a)
        self.assertEqual(c, "PATIENT_000002")

    def test_tokens_survive_reopen(self):
        a = self.store.pseudonym("PHONE", "01012345678", raw="010-1234-5678")
        self.store.close()
        self.store = Store(self.root)
        self.assertEqual(self.store.pseudonym("PHONE", "01012345678"), a)
        self.assertEqual(self.store.reveal(a), "010-1234-5678")

    def test_reveal_unknown_token(self):
        self.assertIsNone(self.store.reveal("NOPE_000001"))

    def test_state_dir_is_gitignored(self):
        self.assertEqual((self.root / ".deid" / ".gitignore").read_text(), "*\n")

    def test_lookup_add_and_forget_column(self):
        self.store.add_lookups("f1", "name", [("홍길동", "NAME_000001")])
        self.store.add_lookups("f1", "memo", [("서울특별시 강남구 테헤란로 1", "[REDACTED]")])
        self.assertEqual(self.store.lookup("홍길동"), "NAME_000001")
        self.store.forget_column("f1", "name")
        self.assertIsNone(self.store.lookup("홍길동"))
        self.assertEqual(self.store.multiword_lookups(), [("서울특별시 강남구 테헤란로 1", "[REDACTED]")])

    def test_state_lives_in_dot_deid_state_map_sqlite(self):
        self.assertEqual(os.listdir(self.root), [".deid"])
        self.assertEqual(sorted(os.listdir(self.root / ".deid")), [".gitignore", "state"])
        self.assertIn("map.sqlite", os.listdir(self.root / ".deid" / "state"))

    def test_forget_file_drops_only_that_files_lookups(self):
        self.store.add_lookups("f1", "name", [("홍길동", "NAME_000001")])
        self.store.add_lookups("f1", "memo", [("서울특별시 강남구 테헤란로 1", "[REDACTED]")])
        self.store.add_lookups("f2", "name", [("김철수", "NAME_000002")])
        self.store.forget_file("f1")
        self.assertIsNone(self.store.lookup("홍길동"))
        self.assertEqual(self.store.multiword_lookups(), [])
        self.assertEqual(self.store.lookup("김철수"), "NAME_000002")

    def test_lookup_surfaces_are_stripped(self):
        self.store.add_lookups("f1", "name", [(" 김철수 ", "NAME_000009"), ("   ", "X")])
        self.assertEqual(self.store.lookup("김철수"), "NAME_000009")
        self.assertIsNone(self.store.lookup(""))


class EntityNameTest(unittest.TestCase):
    def test_ascii_column(self):
        self.assertEqual(entity_name("patient_no"), "PATIENT_NO")

    def test_non_ascii_column_uses_fallback(self):
        self.assertEqual(entity_name("환자번호", fallback="ID"), "ID")

    def test_default_fallback_is_ID(self):
        self.assertEqual(entity_name("환자번호"), "ID")
        self.assertEqual(entity_name("__"), "ID")

    def test_leading_digit(self):
        self.assertEqual(entity_name("2nd id"), "C_2ND_ID")


if __name__ == "__main__":
    unittest.main()
