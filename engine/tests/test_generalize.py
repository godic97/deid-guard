import unittest

from deidlib.generalize import GeneralizeError, generalize


class GeneralizeTest(unittest.TestCase):
    def test_birthdate_to_year(self):
        self.assertEqual(generalize("birthdate", "1990-03-15"), "1990")
        self.assertEqual(generalize("birthdate", "19900315"), "1990")
        self.assertEqual(generalize("birthdate", "1990.3.15"), "1990")

    def test_unparseable_birthdate_is_redacted(self):
        self.assertEqual(generalize("birthdate", "unknown"), "[REDACTED]")

    def test_date_to_month(self):
        self.assertEqual(generalize("date", "2024-03-07 10:00:00"), "2024-03")

    def test_korean_address_to_district(self):
        self.assertEqual(generalize("address", "서울특별시 강남구 테헤란로 1"), "서울특별시 강남구")
        self.assertEqual(generalize("address", "경기도 성남시 분당구 판교역로 2"), "경기도 성남시 분당구")
        self.assertEqual(generalize("address", "경기도 양평군 양평읍 3"), "경기도 양평군")

    def test_english_address_drops_street_and_numbers(self):
        self.assertEqual(generalize("address", "12 Main St, Springfield, IL 62704"), "Springfield, IL")

    def test_zip_keeps_three_digits(self):
        self.assertEqual(generalize("zip", "06236"), "062**")

    def test_age_band(self):
        self.assertEqual(generalize("age", "37"), "35-39")

    def test_unsupported_kind(self):
        with self.assertRaises(GeneralizeError):
            generalize("name", "김민준")


if __name__ == "__main__":
    unittest.main()
