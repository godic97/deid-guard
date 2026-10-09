import contextlib
import csv
import json
import locale
import tempfile
import unittest
from pathlib import Path

from deidlib.apply import apply_decisions, load_state
from deidlib.guard import card_path, guard
from deidlib.profile import file_id, fingerprint
from deidlib.store import Store
from tests.fixtures import write_patients


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


@contextlib.contextmanager
def ctype_locale(*names):
    """Run with the first available LC_CTYPE locale, which sets the default
    text encoding; without any of them, run in the current locale."""
    old = locale.setlocale(locale.LC_CTYPE)
    for name in names:
        try:
            locale.setlocale(locale.LC_CTYPE, name)
            break
        except locale.Error:
            continue
    try:
        yield
    finally:
        locale.setlocale(locale.LC_CTYPE, old)


class GuardStateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)
        self.path = write_patients(self.root / "data" / "patients.csv")
        self.card = self.root / ".deid" / "cards" / "data" / "patients.csv.md"
        self.copy = self.root / ".deid" / "out" / "data" / "patients.csv"

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def guard(self, path=None):
        return guard(self.store, self.root, path or self.path)

    def write_config(self, name="conf.json"):
        p = self.root / name
        p.write_text('{"name": "my-app", "port": 8080}', encoding="utf-8")
        return p

    def write_people(self, p):
        p.write_text(json.dumps([{"성명": "박지민", "x": 1}, {"성명": "최유리", "x": 2}], ensure_ascii=False),
                     encoding="utf-8")
        return p

    # --- pending: the profile card -------------------------------------------

    def test_card_path(self):
        self.assertEqual(card_path(self.root, "data/patients.csv"), self.card)

    def test_first_read_returns_the_card_and_saves_pending_state(self):
        self.assertEqual(self.guard(), {"status": "pending", "file": "data/patients.csv", "read_path": str(self.card)})
        self.assertIn("values withheld", self.card.read_text(encoding="utf-8"))
        self.assertEqual(load_state(self.root, file_id(self.root, self.path)),
                         {"file": "data/patients.csv", "fingerprint": fingerprint(self.path), "decisions": None})

    def test_card_is_utf8_whatever_the_locale(self):
        with ctype_locale("C", "POSIX"):
            self.guard()
        card = self.card.read_bytes().decode("utf-8")
        self.assertIn("| 2 | 성명 |", card)

    def test_deleted_card_is_written_again(self):
        self.guard()
        self.card.unlink()
        self.assertEqual(self.guard()["read_path"], str(self.card))
        self.assertIn("values withheld", self.card.read_text(encoding="utf-8"))

    # --- decided: the de-identified copy ----------------------------------------

    def test_decided_file_returns_its_copy(self):
        apply_decisions(self.store, self.root, self.path, [{"column": "나이", "action": "drop"}])
        self.assertEqual(self.guard(), {"status": "decided", "file": "data/patients.csv", "read_path": str(self.copy)})

    def test_deleted_copy_is_rebuilt_with_the_saved_decisions(self):
        apply_decisions(self.store, self.root, self.path, [{"column": "나이", "action": "drop"}])
        self.copy.unlink()
        self.assertEqual(self.guard(), {"status": "decided", "file": "data/patients.csv", "read_path": str(self.copy)})
        header, rows = read_csv(self.copy)
        self.assertNotIn("나이", header)
        self.assertEqual(len(rows), 20)

    def test_changed_file_is_reapplied_and_its_state_updated(self):
        decisions = [{"column": "나이", "action": "drop"}]
        apply_decisions(self.store, self.root, self.path, decisions)
        write_patients(self.path, n=25)
        self.assertEqual(self.guard()["status"], "decided")
        _, rows = read_csv(self.copy)
        self.assertEqual(len(rows), 25)
        state = load_state(self.root, file_id(self.root, self.path))
        self.assertEqual(state["fingerprint"], fingerprint(self.path))
        self.assertEqual(state["decisions"], decisions)

    # --- not_data: config files pass through --------------------------------------

    def test_config_json_is_not_data_and_its_state_says_so(self):
        p = self.write_config()
        self.assertEqual(self.guard(p), {"status": "not_data", "file": "conf.json", "read_path": str(p)})
        self.assertEqual(load_state(self.root, file_id(self.root, p)),
                         {"file": "conf.json", "fingerprint": fingerprint(p), "not_data": True})
        self.assertFalse(card_path(self.root, "conf.json").exists())

    def test_file_rewritten_as_config_never_returns_its_old_card(self):
        p = self.write_people(self.root / "people.json")
        self.assertEqual(self.guard(p)["status"], "pending")
        p.write_text('{"name": "my-app"}', encoding="utf-8")
        expected = {"status": "not_data", "file": "people.json", "read_path": str(p)}
        self.assertEqual(self.guard(p), expected)
        self.assertEqual(self.guard(p), expected)
        self.assertEqual(self.guard(p), expected)

    def test_config_file_that_becomes_a_table_is_profiled_again(self):
        p = self.write_config("people.json")
        self.assertEqual(self.guard(p)["status"], "not_data")
        self.write_people(p)
        self.assertEqual(self.guard(p), {"status": "pending", "file": "people.json",
                                         "read_path": str(card_path(self.root, "people.json"))})
        self.assertEqual(self.store.lookup("박지민"), "NAME_000001")
        self.assertEqual(load_state(self.root, file_id(self.root, p)),
                         {"file": "people.json", "fingerprint": fingerprint(p), "decisions": None})


if __name__ == "__main__":
    unittest.main()
