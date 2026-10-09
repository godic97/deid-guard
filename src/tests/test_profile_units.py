"""Unit tests for column profiling and mask registration. Every value is made up."""
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from deidlib.profile import (JSON_FIELDS_KEY, _header_is_data, _is_date, _names_in_header, _status,
                             _value_type, default_entity, fingerprint, mask_key, maskable, name_like,
                             norm_for, profile_columns, profile_file, register_json_leaves,
                             register_masks, render_card, shape)
from deidlib.store import Store
from deidlib.tables import Table
from tests.xlsx_fixture import write_xlsx

NAMES = ["김민준", "이서연", "박도윤", "최하은", "정시우", "윤예준"]


def column(name, values):
    """The profile of a one-column table holding values."""
    return profile_columns(Table("t", [name], [[v] for v in values]))[0]


def kind_of(name, values):
    return column(name, values)["kind"]


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()


class NameLikeTest(unittest.TestCase):
    def test_korean_names_of_two_to_four_syllables(self):
        self.assertTrue(name_like("이훈"))
        self.assertTrue(name_like("정시우"))
        self.assertTrue(name_like("남궁민수"))

    def test_not_names(self):
        self.assertFalse(name_like("김"))           # one syllable
        self.assertFalse(name_like("김수한무거"))    # five syllables
        self.assertFalse(name_like("가나다"))        # no surname first
        self.assertFalse(name_like("김과장"))        # title ending
        self.assertFalse(name_like("Kim"))


class ShapeTest(unittest.TestCase):
    def test_character_classes(self):
        self.assertEqual(shape("AZaz가힣 9"), "AAaa가가 #")
        self.assertEqual(shape("P-0012"), "A-####")

    def test_other_characters_are_kept(self):
        self.assertEqual(shape("_[{é"), "_[{é")

    def test_long_values_are_cut_at_24(self):
        self.assertEqual(shape("b" * 24), "a" * 24)
        self.assertEqual(shape("b" * 25), "a" * 24 + "…")


class IsDateTest(unittest.TestCase):
    def test_month_and_day_bounds(self):
        for v in ("2024-01-01", "2024-12-31", "2024/3/5", "2024.03.15", "2024-03-15T10:00"):
            self.assertTrue(_is_date(v), v)
        for v in ("2024-13-01", "2024-00-10", "2024-12-32", "2024-01-00", "24-03-15"):
            self.assertFalse(_is_date(v), v)

    def test_compact_dates(self):
        self.assertTrue(_is_date("20240131"))
        self.assertTrue(_is_date("20241201"))
        self.assertFalse(_is_date("20241305"))
        self.assertFalse(_is_date("20240100"))


class ValueTypeTest(unittest.TestCase):
    def test_types(self):
        self.assertEqual(_value_type([]), "empty")
        self.assertEqual(_value_type(["1", "-22"]), "int")
        self.assertEqual(_value_type(["12345678", "87654321"]), "int")
        self.assertEqual(_value_type(["202403150"]), "int")
        self.assertEqual(_value_type(["20240315", "19991231"]), "date")
        self.assertEqual(_value_type(["1.5", "2"]), "float")
        self.assertEqual(_value_type(["2024-03-15", "2024/3/5"]), "date")
        self.assertEqual(_value_type(["2024-03-15", "5"]), "str")


class MandatoryKindTest(unittest.TestCase):
    def test_half_the_values_make_a_mandatory_column(self):
        self.assertEqual(kind_of("x", ["010-1234-5678", "010-2345-6789", "foo", "bar"]), "PHONE")

    def test_a_minority_does_not(self):
        self.assertIsNone(kind_of("x", ["010-1234-5678", "foo", "bar"]))
        # Contact data together is 60% of the values, so the column is forced,
        # named after its most common kind.
        self.assertEqual(kind_of("x", ["010-1234-5678", "010-2345-6789", "a@example.com", "foo", "bar"]), "PHONE")
        self.assertIsNone(kind_of("x", ["010-1234-5678", "a@example.com", "foo", "bar", "baz"]))


class ColumnNameKindTest(unittest.TestCase):
    def test_header_spelling_is_normalized(self):
        self.assertEqual(kind_of("First Name", ["a1", "b2"]), "name")
        self.assertEqual(kind_of("first-name", ["a1", "b2"]), "name")
        self.assertEqual(kind_of("AGE", ["31", "32"]), "age")

    def test_identifier_columns(self):
        self.assertEqual(kind_of("patient_id", ["A1", "B2"]), "identifier")
        self.assertEqual(kind_of("patientId", ["A1", "B2"]), "identifier")
        self.assertEqual(kind_of("PATIENT_ID", ["A1", "B2"]), "identifier")
        self.assertEqual(kind_of("환자번호", ["A1", "B2"]), "identifier")
        self.assertEqual(kind_of("member_no", ["101", "102", "103"]), "identifier")

    def test_identifier_needs_half_unique_values(self):
        self.assertEqual(kind_of("patient_id", ["A1", "A1", "B2", "B2"]), "identifier")
        self.assertIsNone(kind_of("patient_id", ["A1", "A1", "A1", "B2", "B2"]))

    def test_identifier_needs_text_or_integers(self):
        self.assertEqual(kind_of("visit_no", ["2024-01-02", "2024-01-03"]), "date")
        self.assertIsNone(kind_of("score_no", ["1.5", "2.5"]))


class ValueKindTest(unittest.TestCase):
    def test_names_need_five_distinct_values(self):
        self.assertEqual(kind_of("담당", NAMES[:5]), "name")
        self.assertIsNone(kind_of("담당", NAMES[:4] * 2))

    def test_names_need_sixty_percent(self):
        self.assertEqual(kind_of("담당", NAMES + ["abc", "def", "ghi", "jkl"]), "name")
        self.assertIsNone(kind_of("담당", NAMES[:5] + ["abc", "def", "ghi", "jkl", "mno"]))

    def test_addresses_need_half_the_values(self):
        a, b = "서울특별시 강남구 테헤란로 1", "부산광역시 해운대구 우동 2"
        self.assertEqual(kind_of("loc", [a, b, "foo", "bar"]), "address")
        self.assertIsNone(kind_of("loc", [a, "foo", "bar", "baz"]))

    def test_long_text_is_free_text(self):
        self.assertEqual(kind_of("x", ["a" * 30, "b" * 30]), "free_text")
        self.assertIsNone(kind_of("x", ["a" * 30, "b" * 29]))
        self.assertIsNone(kind_of("x", ["1" * 30, "2" * 30]))


class ShapeIdentifierTest(unittest.TestCase):
    """Columns without an ID name that look like codes are identifiers."""

    def test_ten_unique_codes(self):
        self.assertEqual(kind_of("ref", [f"R{1000 + i}" for i in range(10)]), "identifier")
        self.assertIsNone(kind_of("ref", [f"R{1000 + i}" for i in range(9)]))
        self.assertEqual(kind_of("ref", [str(100000 + i) for i in range(10)]), "identifier")

    def test_uniqueness_threshold(self):
        codes = [f"R{1000 + i}" for i in range(19)]
        self.assertEqual(kind_of("ref", codes + ["R1000"]), "identifier")              # 95% unique
        self.assertIsNone(kind_of("ref", codes[:18] + ["R1000", "R1001"]))             # 90% unique

    def test_shape_threshold(self):
        codes = [f"R{1000 + i}" for i in range(8)]
        self.assertEqual(kind_of("ref", codes + ["RR1000", "RR1001"]), "identifier")    # 80% one shape
        self.assertIsNone(kind_of("ref", codes[:7] + ["RR1000", "RR1001", "RR1002"]))  # 70%

    def test_codes_need_four_characters_with_a_digit(self):
        self.assertEqual(kind_of("ref", [f"A{i:03d}" for i in range(10)]), "identifier")
        self.assertIsNone(kind_of("ref", [f"A{i:02d}" for i in range(10)]))
        self.assertIsNone(kind_of("ref", [chr(97 + i) * 4 for i in range(10)]))

    def test_only_the_first_fifty_values_are_checked_for_digits(self):
        words = [a + b + "xy" for a, b in itertools.product("abcdefgh", "abcdefg")][:50]
        self.assertIsNone(kind_of("ref", words + ["R1234"]))


class ProfileColumnsTest(unittest.TestCase):
    def test_counts_skip_empty_cells(self):
        cols = profile_columns(Table("t", ["a", "b"], [["x", None], ["y", "  "], ["x", None]]))
        self.assertEqual(cols[0]["non_null"], 3)
        self.assertEqual(cols[0]["unique"], 0.667)
        self.assertEqual(cols[0]["shapes"], [("a", 1.0)])
        self.assertEqual(cols[1]["non_null"], 0)
        self.assertEqual(cols[1]["unique"], 0.0)
        self.assertEqual(cols[1]["shapes"], [])
        self.assertEqual(cols[1]["type"], "empty")
        self.assertEqual(cols[1]["status"], "kept")

    def test_shape_ratios(self):
        col = column("code", ["A1", "B2", "xy"])
        self.assertEqual(col["shapes"], [("A#", 0.67), ("aa", 0.33)])

    def test_at_most_three_shapes(self):
        col = column("code", ["a", "1", "A", "가", "a"])
        self.assertEqual(len(col["shapes"]), 3)
        self.assertEqual(col["shapes"][0], ("a", 0.4))

    def test_free_text_status(self):
        col = column("메모", ["note one", "note two"])
        self.assertEqual(col["kind"], "free_text")
        self.assertEqual(col["status"], "free_text")
        self.assertEqual(col["default_action"], "keep")

    def test_status_of_each_kind(self):
        self.assertEqual(_status("RRN"), "forced")
        self.assertEqual(_status("birthdate"), "pending")
        self.assertEqual(_status("date"), "quasi")
        self.assertEqual(_status("free_text"), "free_text")
        self.assertEqual(_status(None), "kept")


class DefaultEntityTest(unittest.TestCase):
    def test_entities(self):
        self.assertEqual(default_entity("주민번호", "RRN"), "RRN")
        self.assertEqual(default_entity("성명", "name"), "NAME")
        self.assertEqual(default_entity("환자번호", "identifier"), "PATIENT")
        self.assertEqual(default_entity("보험증번호", "identifier"), "ID")
        self.assertEqual(default_entity("Patient No", "identifier"), "PATIENT_NO")


class MaskableTest(unittest.TestCase):
    def test_korean_names_from_two_syllables(self):
        self.assertTrue(maskable("이훈", "name"))
        self.assertFalse(maskable("이", "name"))
        self.assertFalse(maskable("이훈", None))
        self.assertFalse(maskable("이훈", "identifier"))
        self.assertFalse(maskable("Al", "name"))

    def test_lengths(self):
        self.assertTrue(maskable(" abc ", None))
        self.assertFalse(maskable("ab", None))
        self.assertTrue(maskable("12345", None))
        self.assertFalse(maskable("1234", None))


class NormForTest(unittest.TestCase):
    def test_mandatory_values_are_normalized(self):
        self.assertEqual(norm_for("PHONE", "010-1234-5678"), "01012345678")
        self.assertEqual(norm_for("PHONE", " 010.1234.5678 "), "01012345678")

    def test_other_values_are_stripped(self):
        self.assertEqual(norm_for(None, " P-1 "), "P-1")
        self.assertEqual(norm_for("PHONE", "010-1234-5678, 010-2345-6789"), "010-1234-5678, 010-2345-6789")


class MaskKeyTest(unittest.TestCase):
    def test_keys_are_distinct_and_stable(self):
        # The key is persisted in the store; apply looks masks up by it.
        self.assertEqual(mask_key("patients", "성명"), '["patients", "성명"]')
        self.assertNotEqual(mask_key("a", "b"), mask_key("a", "c"))
        self.assertNotEqual(mask_key("a", "b"), JSON_FIELDS_KEY)


class HeaderIsDataTest(unittest.TestCase):
    @staticmethod
    def table(header, rows):
        return Table("t", header, [list(r) for r in rows])

    def test_empty_header(self):
        self.assertFalse(_header_is_data(Table("t", [], [])))

    def test_personal_value_in_header(self):
        self.assertTrue(_header_is_data(self.table(["010-1234-5678", "x"], [["010-2345-6789", "y"]])))

    def test_numeric_headers(self):
        letters = [["x", "yy", "zzz", "w"], ["yy", "x", "w", "zzz"], ["zzz", "w", "x", "yy"]]
        three = [r[:3] for r in letters]
        self.assertTrue(_header_is_data(self.table(["2023", "item", "note"], three)))
        self.assertTrue(_header_is_data(self.table(["2023", "2024", "item", "note"], letters)))
        self.assertFalse(_header_is_data(self.table(["2023", "item", "note", "memo"], letters)))

    def test_header_with_the_shape_of_its_values(self):
        self.assertTrue(_header_is_data(self.table(["P-1001"], [["P-1002"], [None], ["P-1003"], ["P-1004"]])))
        self.assertFalse(_header_is_data(self.table(["P-1001"], [["P-1002"], ["P-1003"]])))
        self.assertFalse(_header_is_data(self.table(["abc"], [["xyz"], ["def"], ["ghi"]])))

    def test_shape_needs_eighty_percent_of_values(self):
        four = [["P-1002"], ["P-1003"], ["P-1004"], ["P-1005"]]
        self.assertTrue(_header_is_data(self.table(["P-1001"], four + [["xy"]])))
        self.assertFalse(_header_is_data(self.table(["P-1001"], four[:3] + [["xy"], ["zw"]])))

    def test_shape_uses_the_first_200_rows(self):
        rows = [[f"P-{1000 + i}"] for i in range(160)] + [["xy"]] * 41
        self.assertTrue(_header_is_data(self.table(["P-0999"], rows)))

    def test_half_the_headers_must_match(self):
        mixed = [["x", "yy", "zzz"], ["yy", "zzz", "x"], ["zzz", "x", "yy"]]
        coded = [f"P-{i}000" for i in range(1, 4)]
        one_of_four = [[c] + m for c, m in zip(coded, mixed)]
        self.assertFalse(_header_is_data(self.table(["P-9000", "item", "note", "memo"], one_of_four)))
        one_of_three = [[c] + m[:2] for c, m in zip(coded, mixed)]
        self.assertTrue(_header_is_data(self.table(["P-9000", "item", "note"], one_of_three)))
        two_of_four = [[c, "Q-" + c[2:]] + m[:2] for c, m in zip(coded, mixed)]
        self.assertTrue(_header_is_data(self.table(["P-9000", "Q-9000", "item", "note"], two_of_four)))


class NamesInHeaderTest(unittest.TestCase):
    def test_two_names_are_needed(self):
        self.assertEqual(_names_in_header(["구분", "김민준", "이서연"]), [1, 2])
        self.assertEqual(_names_in_header(["구분", "김민준_2024", "합계"]), [])


class RegisterMasksTest(StoreCase):
    def register(self, table):
        cols = profile_columns(table)
        for c in cols:
            register_masks(self.store, "f1", table, c)
        return cols

    def test_blank_and_short_values_are_skipped(self):
        self.register(Table("people", ["성명"], [[None], ["  "], ["이훈"], ["A"], ["김민준"]]))
        self.assertEqual(self.store.lookup("이훈"), "NAME_000001")
        self.assertEqual(self.store.lookup("김민준"), "NAME_000002")
        self.assertIsNone(self.store.lookup("A"))

    def test_mandatory_values_share_a_token_per_person(self):
        self.register(Table("people", ["연락처"], [["010-1234-5678"], ["01012345678"]]))
        self.assertEqual(self.store.lookup("010-1234-5678"), "PHONE_000001")
        self.assertEqual(self.store.lookup("01012345678"), "PHONE_000001")
        self.assertEqual(self.store.reveal("PHONE_000001"), "010-1234-5678")

    def test_masks_are_kept_per_column(self):
        rows = [["김민준", "P-77123"], ["이서연", "P-77124"]]
        self.register(Table("people", ["성명", "patient_id"], rows))
        self.store.forget_column("f1", mask_key("people", "성명"))
        self.assertIsNone(self.store.lookup("김민준"))
        self.assertEqual(self.store.lookup("P-77123"), "PATIENT_ID_000001")


class RegisterJsonLeavesTest(StoreCase):
    def leaves(self, data, name="doc.json"):
        path = self.root / name
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode())
        return register_json_leaves(self.store, "f1", path)

    def test_generic_key_under_a_personal_one(self):
        self.leaves({"patient": {"id": "P-77123"}, "item": {"id": "SKU-555"}})
        self.assertEqual(self.store.lookup("P-77123"), "ID_000001")
        self.assertIsNone(self.store.lookup("SKU-555"))

    def test_personal_key_carries_into_lists(self):
        self.leaves({"phone": ["010-1234-5678", "010-2345-6789"], "patients": [{"id": "P-77123"}]})
        self.assertEqual(self.store.lookup("010-1234-5678"), "PHONE_000001")
        self.assertEqual(self.store.lookup("010-2345-6789"), "PHONE_000002")
        self.assertEqual(self.store.lookup("P-77123"), "ID_000001")

    def test_null_and_booleans_are_ignored(self):
        self.assertEqual(self.leaves({"phone": None, "email": True, "성명": False}), 0)

    def test_korean_name_under_a_name_key(self):
        self.assertEqual(self.leaves({"name": "홍길동", "title": {"name": "Widget"}, "nick": {"name": "이훈"}}), 2)
        self.assertEqual(self.store.lookup("홍길동"), "NAME_000001")
        self.assertEqual(self.store.lookup("이훈"), "NAME_000002")
        self.assertIsNone(self.store.lookup("Widget"))

    def test_name_keys_make_name_entities(self):
        self.leaves({"CustomerName": "Alex Kim"})
        self.assertEqual(self.store.lookup("Alex Kim"), "NAME_000001")

    def test_other_personal_keys_use_the_key_as_entity(self):
        self.leaves({"연락처": "카톡 abc123"})
        self.assertEqual(self.store.lookup("카톡 abc123"), "ID_000001")

    def test_detected_values_use_their_kind(self):
        self.leaves({"mobile": "010-1234-5678", "phone": "01012345678"})
        self.assertEqual(self.store.lookup("010-1234-5678"), "PHONE_000001")
        self.assertEqual(self.store.lookup("01012345678"), "PHONE_000001")
        self.assertEqual(self.store.reveal("PHONE_000001"), "010-1234-5678")

    def test_jsonl_skips_bad_lines(self):
        lines = ['{"성명": "홍길동"}', "not json", '{"성명": "김민준"}']
        self.assertEqual(self.leaves("\n".join(lines).encode(), "doc.jsonl"), 2)
        self.assertEqual(self.store.lookup("김민준"), "NAME_000002")

    def test_bom_and_bad_bytes(self):
        data = b"\xef\xbb\xbf" + '{"성명": "홍길동", "memo": "x'.encode() + b"\xff" + b'"}'
        self.assertEqual(self.leaves(data), 1)
        self.assertEqual(self.store.lookup("홍길동"), "NAME_000001")

    def test_top_level_values_have_no_key(self):
        self.assertEqual(self.leaves(["홍길동", "x"]), 0)


class ProfileFileTest(StoreCase):
    def test_file_outside_root(self):
        with tempfile.TemporaryDirectory() as other:
            path = Path(other) / "x.csv"
            path.write_text("a,b\n1,2\n")
            result = profile_file(self.store, self.root, path)
            self.assertEqual(result["file"], "_abs" + str(path.resolve()))

    def test_ids_and_fingerprint(self):
        path = self.root / "sub" / "x.csv"
        path.parent.mkdir()
        path.write_text("a,b\n1,2\n")
        result = profile_file(self.store, self.root, path)
        self.assertEqual(result["file"], "sub/x.csv")
        self.assertRegex(result["file_id"], r"^[0-9a-f]{12}$")
        self.assertEqual(profile_file(self.store, self.root, path)["file_id"], result["file_id"])
        self.assertEqual(result["fingerprint"], fingerprint(path))
        self.assertIn("`sub/x.csv`", result["card"])

    def test_empty_file(self):
        path = self.root / "empty.csv"
        path.write_text("")
        result = profile_file(self.store, self.root, path)
        self.assertEqual(result["tables"][0]["rows"], 0)
        self.assertEqual(result["tables"][0]["columns"], [])

    def test_every_sheet_is_profiled(self):
        path = self.root / "book.xlsx"
        write_xlsx(path, {
            "raw": [["010-1234-5678", "x"], ["010-2345-6789", "y"]],
            "named": [["성명", "비고"], ["김민준", "a"], ["이서연", "b"]],
        })
        result = profile_file(self.store, self.root, path)
        tables = result["tables"]
        self.assertEqual([t["name"] for t in tables], ["raw", "named"])
        self.assertEqual([t["header_masked"] for t in tables], [True, False])
        self.assertEqual(self.store.lookup("010-1234-5678"), "PHONE_000001")
        self.assertEqual(self.store.lookup("이서연"), "NAME_000002")

    def test_jsonl_fields_outside_columns_are_masked(self):
        path = self.root / "rows.jsonl"
        lines = [{"n": 1, "owner": {"성명": "홍길동"}}, {"n": 2, "owner": {"성명": "김민준"}}]
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in lines))
        profile_file(self.store, self.root, path)
        self.assertEqual(self.store.lookup("홍길동"), "NAME_000001")


def card_rows(card):
    """The cells of every profile table row in a card, by table."""
    lines = card.split("\n")
    tables = []
    for i, line in enumerate(lines):
        if line.startswith("| # |"):
            header = [c.strip() for c in line.strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip("|").split(" | ")])
                j += 1
            tables.append({"header": header, "separator": lines[i + 1], "rows": rows,
                           "after": lines[j] if j < len(lines) else None, "before": lines[i - 1]})
    return tables


class RenderCardTest(unittest.TestCase):
    def table(self, **kw):
        t = {"name": "t", "rows": 4, "header_masked": False, "masked_headers": [], "columns": [
            {"index": 1, "name": "code", "type": "str", "non_null": 4, "unique": 1.0,
             "shapes": [("A#", 0.5), ("aa", 0.25), ("#", 0.25)], "kind": None, "status": "kept",
             "default_action": "keep"},
            {"index": 2, "name": "blank", "type": "empty", "non_null": 0, "unique": 0.0, "shapes": [],
             "kind": None, "status": "kept", "default_action": "keep"},
            {"index": 3, "name": "phone", "type": "str", "non_null": 4, "unique": 1.0,
             "shapes": [("###-####-####", 1.0)], "kind": "PHONE", "status": "forced",
             "default_action": "pseudonymize"},
        ]}
        t.update(kw)
        return t

    def test_columns_are_listed(self):
        card = render_card("a.csv", [self.table()])
        (t,) = card_rows(card)
        self.assertEqual(t["header"], ["#", "column", "type", "non-null", "unique", "shape",
                                       "detected", "status", "default"])
        self.assertEqual(t["separator"], "|---" * 9 + "|")
        self.assertEqual(t["before"], "")
        self.assertEqual(t["after"], "")
        self.assertEqual(t["rows"], [
            ["1", "code", "str", "4", "100%", "`A#` 50%, `aa` 25%", "-", "kept", "keep"],
            ["2", "blank", "empty", "0", "0%", "-", "-", "kept", "keep"],
            ["3", "phone", "str", "4", "100%", "`###-####-####` 100%", "PHONE", "forced", "pseudonymize"],
        ])

    def test_header_notes(self):
        plain = render_card("a.csv", [self.table()])
        self.assertNotIn("col_N", plain)
        masked = render_card("a.csv", [self.table(header_masked=True, masked_headers=[0, 1, 2])])
        self.assertIn("first row", masked)
        self.assertIn("`col_N`", masked)
        names = render_card("a.csv", [self.table(masked_headers=[1])])
        self.assertIn("personal names", names)
        self.assertIn("`col_N`", names)
        self.assertNotIn("first row", names)

    def test_legend_and_next_steps(self):
        card = render_card("a.csv", [self.table()])
        self.assertTrue(card.startswith("# deid-guard: `a.csv`"))
        for status in ("forced", "pending", "quasi", "free_text"):
            self.assertIn(f"`{status}` = ", card)  # the legend defines every status
        for phrase in ("Next steps", "AskUserQuestion", "`mcp__deid-guard__apply`", "drop", "never guess values"):
            self.assertIn(phrase, card)


if __name__ == "__main__":
    unittest.main()
