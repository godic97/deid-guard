import contextlib
import csv
import json
import locale
import tempfile
import unittest
from pathlib import Path

from deidlib.apply import ApplyError, _safe_name, apply_decisions, load_state, save_state, state_path
from deidlib.guard import guard
from deidlib.profile import file_id, fingerprint, mask_key
from deidlib.store import Store
from tests.fixtures import HEADER, write_patients

PATIENT_COLUMN_LINES = [
    "- patient_no: pseudonymize as PATIENT_NO_######",
    "- 성명: pseudonymize as NAME_######",
    "- 주민번호: pseudonymize as RRN_######",
    "- 연락처: pseudonymize as PHONE_######",
    "- 생년월일: generalize (birthdate)",
    "- 주소: generalize (address)",
    "- 우편번호: generalize (zip)",
    "- 성별: keep",
    "- 나이: keep",
    "- 방문일: keep",
    "- 진단: keep",
    "- 메모: keep",
]


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

    # --- helpers for the tests below ---------------------------------------

    def fid(self, path=None):
        return file_id(self.root, path or self.path)

    def lookups(self, surface):
        """Every mask the store holds for one value: (file_id, column key, replacement)."""
        return self.store.db.execute(
            "SELECT file_id, column, replacement FROM lookups WHERE surface = ? ORDER BY file_id, column",
            (surface,),
        ).fetchall()

    def csv_file(self, name, text):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p

    def two_tables(self):
        p = self.root / "multi.json"
        p.write_text(json.dumps({
            "visits": [{"성명": "윤서하", "n": 1}, {"성명": "한지우", "n": 2}],
            "staff": [{"성명": "강민호", "n": 3}, {"성명": "서지안", "n": 4}],
        }, ensure_ascii=False), encoding="utf-8")
        return p

    # --- state ---------------------------------------------------------------

    def test_state_file_lives_under_deid_state_files(self):
        fid = self.fid()
        self.assertEqual(state_path(self.root, fid), self.root / ".deid" / "state" / "files" / f"{fid}.json")

    def test_save_state_creates_its_directories_and_writes_readable_json(self):
        with tempfile.TemporaryDirectory() as d:
            fresh = Path(d)
            self.assertIsNone(load_state(fresh, "abc"))
            state = {"file": "환자.csv", "decisions": [{"column": "성명", "action": "drop"}]}
            save_state(fresh, "abc", state)
            self.assertEqual(load_state(fresh, "abc"), state)
            self.assertEqual(
                state_path(fresh, "abc").read_bytes().decode("utf-8"),
                '{\n "file": "환자.csv",\n "decisions": [\n  {\n   "column": "성명",\n   "action": "drop"\n  }\n ]\n}',
            )

    def test_apply_saves_decisions_outputs_and_fingerprint(self):
        decisions = [{"column": "나이", "action": "drop"}]
        result = self.apply(decisions)
        copy = self.root / ".deid" / "out" / "data" / "patients.csv"
        self.assertEqual(load_state(self.root, self.fid()), {
            "file": "data/patients.csv",
            "fingerprint": fingerprint(self.path),
            "decisions": decisions,
            "outputs": [".deid/out/data/patients.csv"],
            "read_path": str(copy),
        })
        self.assertEqual(result["file"], "data/patients.csv")
        self.assertEqual(result["outputs"], [".deid/out/data/patients.csv"])
        self.assertEqual(result["read_path"], str(copy))

    def test_dry_run_saves_no_state_and_registers_no_masks(self):
        apply_decisions(self.store, self.root, self.path, [{"column": "성명", "action": "keep"}], dry_run=True)
        self.assertIsNone(load_state(self.root, self.fid()))
        self.assertEqual(self.lookups("김민준"), [])

    # --- matching decisions to columns ---------------------------------------

    def test_bad_action_names_the_column_and_the_allowed_actions(self):
        with self.assertRaises(ApplyError) as cm:
            self.apply([{"column": "성명", "action": "mask"}])
        self.assertEqual(str(cm.exception), "column '성명': action must be one of pseudonymize, generalize, drop, keep")
        self.assertFalse((self.root / ".deid" / "out").exists())

    def test_decision_without_a_column_matches_nothing(self):
        p = self.csv_file("odd.csv", "None,a\nx1,1\nx2,2\n")
        with self.assertRaises(ApplyError) as cm:
            self.apply([{"action": "drop"}], path=p)
        self.assertEqual(str(cm.exception), "no column ''; columns are: None, a")

    def test_unknown_column_error_lists_every_column_once(self):
        with self.assertRaises(ApplyError) as cm:
            self.apply([{"column": "nope", "action": "keep"}], path=self.two_tables())
        self.assertEqual(str(cm.exception), "no column 'nope'; columns are: n, 성명")

    def test_table_restricts_a_decision_to_that_table(self):
        p = self.two_tables()
        result = self.apply([{"column": "성명", "table": "staff", "action": "drop"}], path=p)
        self.assertEqual(result["outputs"], [".deid/out/multi.json.sheets/visits.csv",
                                             ".deid/out/multi.json.sheets/staff.csv"])
        visits_header, visits = read_csv(self.root / result["outputs"][0])
        staff_header, staff = read_csv(self.root / result["outputs"][1])
        self.assertEqual(visits_header, ["성명", "n"])
        self.assertEqual(visits, [["NAME_000001", "1"], ["NAME_000002", "2"]])
        self.assertEqual(staff_header, ["n"])
        self.assertEqual(staff, [["3"], ["4"]])
        fid = self.fid(p)
        self.assertEqual(self.lookups("강민호"), [(fid, mask_key("staff", "성명"), "[REDACTED]")])
        self.assertEqual(self.lookups("윤서하"), [(fid, mask_key("visits", "성명"), "NAME_000001")])

    def test_table_that_does_not_exist_is_an_error(self):
        with self.assertRaises(ApplyError) as cm:
            self.apply([{"column": "성명", "table": "nope", "action": "keep"}], path=self.two_tables())
        self.assertEqual(str(cm.exception), "no column '성명'; columns are: n, 성명")

    # --- output paths ----------------------------------------------------------

    def test_safe_name_replaces_unsafe_characters_and_trims(self):
        self.assertEqual(_safe_name("a/b:c", set()), "a_b_c")
        self.assertEqual(_safe_name("..hidden.", set()), "hidden")
        self.assertEqual(_safe_name(" Xmas ", set()), "Xmas")
        self.assertEqual(_safe_name("Data", set()), "Data")
        self.assertEqual(_safe_name("../..", set()), "sheet")

    def test_safe_name_deduplicates_case_insensitively(self):
        taken = set()
        names = [_safe_name(n, taken) for n in ["Data", "data", "DATA", "data_2"]]
        self.assertEqual(names, ["Data", "data_2", "DATA_3", "data_2_2"])
        self.assertEqual(taken, {"data", "data_2", "data_3", "data_2_2"})

    def test_single_sheet_xlsx_still_gets_a_sheets_dir_and_a_read_file(self):
        from tests.xlsx_fixture import write_xlsx
        book = self.root / "book.xlsx"
        write_xlsx(book, {"Sheet1": [["a"], ["1"]]})
        result = self.apply(path=book)
        self.assertEqual(result["outputs"], [".deid/out/book.xlsx.sheets/Sheet1.csv"])
        self.assertEqual(result["read_path"], str(self.root / ".deid" / "out" / "book.xlsx.md"))
        self.assertEqual(read_csv(self.root / result["outputs"][0]), (["a"], [["1"]]))

    def test_read_file_lists_one_csv_per_table(self):
        result = self.apply(path=self.two_tables())
        read = Path(result["read_path"])
        self.assertEqual(read, self.root / ".deid" / "out" / "multi.json.md")
        self.assertEqual(
            read.read_bytes().decode("utf-8"),
            "# deid-guard: de-identified copy of `multi.json`\n\nOne CSV per sheet or table:\n\n"
            "- `.deid/out/multi.json.sheets/visits.csv`\n- `.deid/out/multi.json.sheets/staff.csv`\n",
        )

    def test_read_file_is_utf8_whatever_the_locale(self):
        p = self.root / "menu.json"
        p.write_text(json.dumps({"café": [{"x": "a1"}, {"x": "a2"}], "thé": [{"y": "b1"}, {"y": "b2"}]},
                                ensure_ascii=False), encoding="utf-8")
        with ctype_locale("en_US.ISO8859-1", "en_US.ISO-8859-1", "de_DE.ISO8859-1", "fr_FR.ISO8859-1"):
            result = self.apply(path=p)
        self.assertEqual(
            Path(result["read_path"]).read_bytes().decode("utf-8"),
            "# deid-guard: de-identified copy of `menu.json`\n\nOne CSV per sheet or table:\n\n"
            "- `.deid/out/menu.json.sheets/café.csv`\n- `.deid/out/menu.json.sheets/thé.csv`\n",
        )

    # --- forced columns and kind overrides --------------------------------------

    def test_forced_column_cannot_be_generalized_even_with_a_kind_override(self):
        result = self.apply([{"column": "주민번호", "action": "generalize", "kind": "zip"}])
        _, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual(rows[0][2], "RRN_000001")
        self.assertEqual(self.lookups("900110-1234567"), [(self.fid(), mask_key("patients", "주민번호"), "RRN_000001")])
        self.assertIn("- 주민번호: pseudonymize as RRN_######  [RRN is forced; pseudonymized instead of 'generalize']",
                      result["summary"].splitlines())

    def test_forced_keep_is_pseudonymized_and_masked(self):
        result = self.apply([{"column": "주민번호", "action": "keep"}])
        self.assertEqual(self.lookups("900110-1234567"), [(self.fid(), mask_key("patients", "주민번호"), "RRN_000001")])
        self.assertIn("- 주민번호: pseudonymize as RRN_######  [RRN is forced; pseudonymized instead of 'keep']",
                      result["summary"].splitlines())

    def test_kind_override_picks_the_entity_of_the_new_kind(self):
        p = self.csv_file("dx.csv", "dx\nflu\ncold\n")
        result = self.apply([{"column": "dx", "action": "pseudonymize", "kind": "name"}], path=p)
        self.assertEqual(read_csv(self.root / result["outputs"][0]), (["dx"], [["NAME_000001"], ["NAME_000002"]]))
        self.assertIn("- dx: pseudonymize as NAME_######", result["summary"].splitlines())

    def test_column_entity_is_used_without_a_kind_override(self):
        p = self.csv_file("dx.csv", "dx\nflu\ncold\n")
        result = self.apply([{"column": "dx", "action": "pseudonymize"}], path=p)
        self.assertEqual(read_csv(self.root / result["outputs"][0]), (["dx"], [["DX_000001"], ["DX_000002"]]))

    def test_generalize_error_names_the_column_kind_and_supported_kinds(self):
        p = self.csv_file("dx.csv", "dx\nflu\ncold\n")
        with self.assertRaises(ApplyError) as cm:
            self.apply([{"column": "dx", "action": "generalize"}], path=p)
        self.assertEqual(str(cm.exception),
                         "column 'dx' (unknown kind) cannot be generalized; "
                         "pass \"kind\" as one of birthdate, date, address, zip, age or choose another action")
        with self.assertRaises(ApplyError) as cm:
            self.apply([{"column": "성명", "action": "generalize"}])
        self.assertEqual(str(cm.exception),
                         "column '성명' (name) cannot be generalized; "
                         "pass \"kind\" as one of birthdate, date, address, zip, age or choose another action")

    # --- masks registered or forgotten per action -------------------------------

    def test_masks_are_registered_under_the_column_key(self):
        self.apply()
        fid = self.fid()
        self.assertEqual(self.lookups("김민준"), [(fid, mask_key("patients", "성명"), "NAME_000001")])
        self.assertEqual(self.lookups("06200"), [(fid, mask_key("patients", "우편번호"), "062**")])

    def test_keep_on_a_pending_column_forgets_masks_from_profiling(self):
        guard(self.store, self.root, self.path)
        fid = self.fid()
        self.assertEqual(self.lookups("P-100101"), [(fid, mask_key("patients", "patient_no"), "PATIENT_NO_000001")])
        self.assertEqual(self.lookups("김민준"), [(fid, mask_key("patients", "성명"), "NAME_000001")])
        self.apply([{"column": "patient_no", "action": "keep"}])
        self.assertEqual(self.lookups("P-100101"), [])
        self.assertEqual(self.lookups("김민준"), [(fid, mask_key("patients", "성명"), "NAME_000001")])

    def test_drop_redacts_forced_and_pending_values_only(self):
        self.apply([{"column": "주민번호", "action": "drop"}, {"column": "진단", "action": "drop"}])
        self.assertEqual(self.lookups("900110-1234567"), [(self.fid(), mask_key("patients", "주민번호"), "[REDACTED]")])
        self.assertEqual(self.lookups("고혈압"), [])

    def test_pseudonymize_skips_empty_cells_and_repeats_and_masks_short_names(self):
        p = self.csv_file("short.csv", "성명,메모\n김민준,a1\n,a2\n김민준,a3\n이수,이수 보호자\n")
        result = self.apply(path=p)
        header, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual(rows, [["NAME_000001", "a1"], ["", "a2"], ["NAME_000001", "a3"],
                                ["NAME_000002", "NAME_000002 보호자"]])
        self.assertEqual(self.lookups("이수"), [(self.fid(p), mask_key("short", "성명"), "NAME_000002")])

    def test_drop_skips_empty_cells_and_repeats_and_masks_short_names(self):
        p = self.csv_file("short.csv", "성명,메모\n김민준,a1\n,a2\n김민준,a3\n이수,a4\n")
        self.apply([{"column": "성명", "action": "drop"}], path=p)
        key = mask_key("short", "성명")
        self.assertEqual(self.lookups("김민준"), [(self.fid(p), key, "[REDACTED]")])
        self.assertEqual(self.lookups("이수"), [(self.fid(p), key, "[REDACTED]")])

    def test_formats_of_one_phone_number_share_a_token_and_reveal_the_raw_value(self):
        p = self.csv_file("ph.csv", "연락처,n\n010-1234-5678,1\n01012345678,2\n010-2222-3333,3\n")
        result = self.apply(path=p)
        _, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual([r[0] for r in rows], ["PHONE_000001", "PHONE_000001", "PHONE_000002"])
        self.assertEqual(self.store.reveal("PHONE_000001"), "010-1234-5678")
        self.assertEqual(self.store.reveal("PHONE_000002"), "010-2222-3333")

    def test_kept_numbers_and_dates_are_copied_verbatim(self):
        p = self.csv_file("typed.csv", "생년월일,방문일,우편번호,금액,점수\n"
                                       "1990-03-04,1990-03-04,12345,12345,12345.0\n"
                                       "1985-07-08,2024-01-02,54321,54321,1.5\n")
        result = self.apply(path=p)
        self.assertEqual(self.store.lookup("12345"), "123**")
        self.assertEqual(self.store.lookup("1990-03-04"), "1990")
        self.assertEqual(read_csv(self.root / result["outputs"][0]), (
            ["생년월일", "방문일", "우편번호", "금액", "점수"],
            [["1990", "1990-03-04", "123**", "12345", "12345.0"],
             ["1985", "2024-01-02", "543**", "54321", "1.5"]],
        ))

    # --- summary -------------------------------------------------------------------

    def test_summary_lists_each_column_with_its_action(self):
        lines = self.apply()["summary"].splitlines()
        self.assertEqual(lines[:2], ["deid-guard applied decisions to `data/patients.csv`.", ""])
        self.assertEqual(lines[2:14], PATIENT_COLUMN_LINES)
        self.assertEqual(lines[14:16], ["", "De-identified copy: `.deid/out/data/patients.csv`"])
        self.assertFalse([l for l in lines if l.startswith("Sheet ")])

    def test_summary_shows_drop_and_generalize(self):
        lines = self.apply([{"column": "성명", "action": "drop"}, {"column": "나이", "action": "generalize"}])["summary"].splitlines()
        self.assertIn("- 성명: drop", lines)
        self.assertIn("- 나이: generalize (age)", lines)

    def test_summary_groups_columns_by_sheet(self):
        p = self.two_tables()
        lines = self.apply([{"column": "성명", "table": "staff", "action": "drop"}], path=p)["summary"].splitlines()
        start = lines.index("Sheet visits:")
        self.assertEqual(lines[start:start + 6], ["Sheet visits:", "- 성명: pseudonymize as NAME_######", "- n: keep",
                                                  "Sheet staff:", "- 성명: drop", "- n: keep"])
        self.assertIn("De-identified copy: `.deid/out/multi.json.sheets/visits.csv`, "
                      "`.deid/out/multi.json.sheets/staff.csv`", lines)
        for raw in ("윤서하", "한지우", "강민호", "서지안"):
            self.assertNotIn(raw, "\n".join(lines))


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

    def test_keeping_a_column_cannot_drop_json_field_masks(self):
        import json as _json
        names = ["김도윤", "이하준", "박서준", "최지호", "정유준", "강은우"]
        p = self.root / "collide.json"
        p.write_text(_json.dumps({"": [{"json": n} for n in names], "owner": {"성명": "윤서하"}}, ensure_ascii=False))
        guard(self.store, self.root, p)
        self.assertIsNotNone(self.store.lookup("윤서하"))
        self.store.close()
        self.store = Store(self.root)
        from deidlib.apply import apply_decisions as _apply
        _apply(self.store, self.root, p, [{"column": "json", "action": "keep"}])
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



class ApplyRobustnessTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_blank_cells_in_a_pseudonymized_column_are_left_blank(self):
        p = self.root / "ids.csv"
        p.write_text("patient_no,memo\nP-1001,a\n   ,b\nP-1002,c\n")
        result = apply_decisions(self.store, self.root, p, [{"column": "patient_no", "action": "pseudonymize"}])
        _, rows = read_csv(self.root / result["outputs"][0])
        self.assertEqual([r[0] for r in rows], ["PATIENT_NO_000001", "   ", "PATIENT_NO_000002"])

    def test_state_is_utf8_whatever_the_locale(self):
        import locale
        p = self.root / "환자.csv"
        write_patients(p, n=12)
        old = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, "C")
            apply_decisions(self.store, self.root, p, [{"column": "나이", "action": "drop"}])
            state = load_state(self.root, file_id(self.root, p))
        finally:
            locale.setlocale(locale.LC_CTYPE, old)
        self.assertEqual(state["file"], "환자.csv")
        self.assertEqual(state["decisions"], [{"column": "나이", "action": "drop"}])

if __name__ == "__main__":
    unittest.main()
