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
