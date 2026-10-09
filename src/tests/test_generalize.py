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

    def test_unsupported_kind_error_names_the_kind_and_the_supported_kinds(self):
        with self.assertRaises(GeneralizeError) as ctx:
            generalize("name", "김민준")
        self.assertEqual(
            str(ctx.exception),
            "cannot generalize a 'name' column; supported: birthdate, date, address, zip, age",
        )


class DateEdgeTest(unittest.TestCase):
    def test_date_without_a_day_keeps_the_month(self):
        self.assertEqual(generalize("date", "2024-03"), "2024-03")
        self.assertEqual(generalize("date", "2024/3"), "2024-03")

    def test_date_without_a_month_is_redacted(self):
        self.assertEqual(generalize("date", "2024"), "[REDACTED]")

    def test_unparseable_date_is_redacted(self):
        self.assertEqual(generalize("date", "unknown"), "[REDACTED]")


class KoreanAddressEdgeTest(unittest.TestCase):
    def test_road_name_right_after_the_province_stops_the_walk(self):
        self.assertEqual(generalize("address", "세종특별자치시 한누리대로 2130"), "세종특별자치시")

    def test_town_after_a_city_stops_the_walk(self):
        self.assertEqual(generalize("address", "경기도 포천시 소흘읍 1"), "경기도 포천시")

    def test_keeps_at_most_two_levels_below_the_first_token(self):
        self.assertEqual(generalize("address", "경기도 가나시 다라시 마바구 1"), "경기도 가나시 다라시")


class ZipEdgeTest(unittest.TestCase):
    def test_four_characters_keep_three(self):
        self.assertEqual(generalize("zip", "1234"), "123**")

    def test_three_characters_or_fewer_are_redacted(self):
        self.assertEqual(generalize("zip", "123"), "[REDACTED]")
        self.assertEqual(generalize("zip", "12"), "[REDACTED]")


class AgeEdgeTest(unittest.TestCase):
    def test_non_finite_and_negative_ages_are_redacted(self):
        self.assertEqual(generalize("age", "inf"), "[REDACTED]")
        self.assertEqual(generalize("age", "-3"), "[REDACTED]")
        self.assertEqual(generalize("age", "0"), "0-4")

if __name__ == "__main__":
    unittest.main()
