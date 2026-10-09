import unittest

from deidlib.detectors import find_all


def kinds(text):
    return [(m.kind, m.value) for m in find_all(text)]


class KoreanDetectorTest(unittest.TestCase):
    def test_rrn_with_hyphen(self):
        self.assertEqual(kinds("주민번호 900101-1234567 입니다"), [("RRN", "900101-1234567")])

    def test_foreigner_registration_number(self):
        self.assertEqual(kinds("외국인 850315-5123456"), [("RRN", "850315-5123456")])

    def test_rrn_rejects_invalid_month(self):
        self.assertEqual(kinds("주문번호 901301-1234567"), [])

    def test_rrn_without_hyphen(self):
        self.assertEqual(kinds("9001011234567"), [("RRN", "9001011234567")])

    def test_mobile_phone_formats(self):
        self.assertEqual(
            kinds("010-1234-5678, 01098765432, +82 10-2222-3333"),
            [("PHONE", "010-1234-5678"), ("PHONE", "01098765432"), ("PHONE", "+82 10-2222-3333")],
        )

    def test_landline_requires_separator(self):
        self.assertEqual(kinds("02-345-6789"), [("PHONE", "02-345-6789")])
        self.assertEqual(kinds("023456789"), [])

    def test_driver_license(self):
        self.assertEqual(kinds("면허 11-22-333333-44"), [("DRIVER", "11-22-333333-44")])

    def test_passport_needs_context(self):
        self.assertEqual(kinds("여권번호: M12345678"), [("PASSPORT", "M12345678")])
        self.assertEqual(kinds("code M12345678"), [])

    def test_account_needs_context(self):
        self.assertEqual(kinds("계좌 110-123-456789"), [("ACCOUNT", "110-123-456789")])
        self.assertEqual(kinds("ref 110-123-456789"), [])


class EnglishDetectorTest(unittest.TestCase):
    def test_email(self):
        self.assertEqual(kinds("mail Hong.Gil@Example.com now"), [("EMAIL", "Hong.Gil@Example.com")])

    def test_ssn(self):
        self.assertEqual(kinds("SSN 123-45-6789"), [("SSN", "123-45-6789")])

    def test_ssn_rejects_invalid_area(self):
        self.assertEqual(kinds("000-12-3456 666-12-3456 900-12-3456"), [])

    def test_us_phone(self):
        self.assertEqual(kinds("call (415) 555-0134"), [("PHONE", "(415) 555-0134")])

    def test_card_requires_luhn(self):
        self.assertEqual(kinds("card 4111 1111 1111 1111"), [("CARD", "4111 1111 1111 1111")])
        self.assertEqual(kinds("card 4111 1111 1111 1112"), [])


class CardBoundaryTest(unittest.TestCase):
    def test_shortest_card_is_thirteen_digits(self):
        self.assertEqual(kinds("4222000000006"), [("CARD", "4222000000006")])

    def test_longest_card_is_nineteen_digits(self):
        self.assertEqual(kinds("6011 0000 0000 0000001"), [("CARD", "6011 0000 0000 0000001")])

    def test_a_number_of_one_repeated_digit_is_not_a_card(self):
        self.assertEqual(kinds("0000 0000 0000 0000"), [])


class RrnDateBoundaryTest(unittest.TestCase):
    def test_rrn_accepts_december_and_the_thirty_first(self):
        self.assertEqual(kinds("901231-1234567"), [("RRN", "901231-1234567")])

    def test_rrn_accepts_days_that_end_in_zero(self):
        self.assertEqual(
            kinds("900110-1234567, 900220-2234567, 900330-1234567"),
            [("RRN", "900110-1234567"), ("RRN", "900220-2234567"), ("RRN", "900330-1234567")],
        )

    def test_rrn_rejects_day_thirty_two(self):
        self.assertEqual(kinds("주문번호 900132-1234567"), [])


class LuhnTest(unittest.TestCase):
    def test_published_test_card_numbers_are_cards(self):
        for number in ("5105 1051 0510 5100", "378282246310005", "6011 1111 1111 1117",
                       "4012 8888 8888 1881", "5555 5555 5555 4444"):
            with self.subTest(number=number):
                self.assertEqual(kinds(number), [("CARD", number)])

    def test_one_wrong_digit_fails_the_checksum(self):
        for number in ("5105 1051 0510 5106", "378282246310006", "6011 1111 1111 1118"):
            with self.subTest(number=number):
                self.assertEqual(kinds(number), [])


class PhoneNormalizationTest(unittest.TestCase):
    def test_us_country_code_is_dropped(self):
        a, b = find_all("+1 415-555-0134 or (415) 555-0134")
        self.assertEqual((a.value, a.norm), ("+1 415-555-0134", "4155550134"))
        self.assertEqual((b.value, b.norm), ("(415) 555-0134", "4155550134"))

    def test_us_area_code_starting_with_82_is_not_treated_as_korea(self):
        a, b = find_all("823-555-0134 / +1 823-555-0134")
        self.assertEqual((a.kind, a.norm), ("PHONE", "8235550134"))
        self.assertEqual((b.kind, b.norm), ("PHONE", "8235550134"))

    def test_korean_country_code_becomes_leading_zero(self):
        (m,) = find_all("+82 10-1234-5678")
        self.assertEqual(m.norm, "01012345678")


class ContextNormalizationTest(unittest.TestCase):
    def test_passport_value_stops_at_the_number_and_normalizes_to_upper_case(self):
        (m,) = find_all("여권번호: m12345678 발급")
        self.assertEqual((m.kind, m.value, m.norm), ("PASSPORT", "m12345678", "M12345678"))
        self.assertEqual((m.start, m.end), (6, 15))

    def test_account_normalizes_to_digits(self):
        (m,) = find_all("계좌 110-123-456789 입금")
        self.assertEqual((m.kind, m.value, m.norm), ("ACCOUNT", "110-123-456789", "110123456789"))


class NormalizationTest(unittest.TestCase):
    def test_same_phone_in_two_formats_normalizes_equal(self):
        a, b = find_all("010-1234-5678 01012345678")
        self.assertEqual(a.norm, b.norm)

    def test_country_code_normalizes_to_domestic(self):
        a, b = find_all("+82 10-1234-5678 010-1234-5678")
        self.assertEqual(a.norm, b.norm)

    def test_email_normalizes_case(self):
        (m,) = find_all("A@B.COM")
        self.assertEqual(m.norm, "a@b.com")


if __name__ == "__main__":
    unittest.main()
