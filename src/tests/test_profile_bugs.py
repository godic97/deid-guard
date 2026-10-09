import unittest

from deidlib.profile import _fmt_shapes, default_entity, profile_columns, render_card
from deidlib.tables import Table


def kind_of(values, name="x"):
    (col,) = profile_columns(Table("t", [name], [[v] for v in values]))
    return col


class MandatoryKindTest(unittest.TestCase):
    def test_half_phone_numbers_is_phone_in_any_row_order(self):
        phones, others = ["010-1111-2222", "010-3333-4444"], ["foo", "bar"]
        self.assertEqual(kind_of(phones + others)["kind"], "PHONE")
        self.assertEqual(kind_of(others + phones)["kind"], "PHONE")

    def test_a_column_mixing_contact_kinds_is_forced(self):
        col = kind_of(["010-1111-2222", "a@example.com", "010-3333-4444", "b@example.com", "memo"])
        self.assertEqual(col["status"], "forced")
        self.assertIn(col["kind"], ("PHONE", "EMAIL"))

    def test_a_mixed_column_is_named_after_its_most_common_kind(self):
        col = kind_of(["a@example.com", "b@example.com", "c@example.com", "010-1111-2222"])
        self.assertEqual(col["kind"], "EMAIL")

    def test_mostly_plain_text_is_not_forced(self):
        col = kind_of(["010-1111-2222", "a", "b", "c", "d"])
        self.assertNotEqual(col["status"], "forced")


class EntityAndDisplayTest(unittest.TestCase):
    def test_korean_id_columns_use_their_entity_names(self):
        self.assertEqual(default_entity("환자ID", "identifier"), "PATIENT")
        self.assertEqual(default_entity("회원id", "identifier"), "MEMBER")
        self.assertEqual(default_entity("환자번호", "identifier"), "PATIENT")
        self.assertEqual(default_entity("patient_no", "identifier"), "PATIENT_NO")

    def test_percentages_are_rounded(self):
        self.assertEqual(_fmt_shapes([("###", 0.29), ("##", 0.71)]), "`###` 29%, `##` 71%")
        col = dict(kind_of(["a"] * 71 + [str(i) for i in range(29)]), name="x")
        col["unique"] = 0.29
        card = render_card("f.csv", [{"name": "t", "rows": 100, "columns": [col], "header_masked": False, "masked_headers": []}])
        self.assertIn("| 29% |", card)


if __name__ == "__main__":
    unittest.main()
