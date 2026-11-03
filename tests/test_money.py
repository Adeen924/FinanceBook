import unittest
from decimal import Decimal

import tests  # noqa: F401  (puts app/ on sys.path)
from utils.money import D, cents, money_sum, allocate, fmt


class MoneyTests(unittest.TestCase):
    def test_float_drift_is_removed(self):
        self.assertEqual(money_sum([0.1, 0.2]), Decimal("0.30"))
        self.assertEqual(money_sum(["0.1"] * 10), Decimal("1.00"))

    def test_parse_messy_strings(self):
        self.assertEqual(D("$1,234.50"), Decimal("1234.50"))
        self.assertEqual(D(""), Decimal("0"))
        self.assertEqual(D(None), Decimal("0"))
        self.assertEqual(D("abc"), Decimal("0"))

    def test_half_up_rounding(self):
        self.assertEqual(cents("2.345"), Decimal("2.35"))
        self.assertEqual(cents("-2.345"), Decimal("-2.35"))

    def test_allocate_sums_exactly(self):
        parts = allocate("1.00", [1, 1, 1])
        self.assertEqual(sum(parts), Decimal("1.00"))
        self.assertEqual(sorted(parts), [Decimal("0.33"), Decimal("0.33"), Decimal("0.34")])
        parts = allocate("7.13", [10.00, 25.50, 4.49])
        self.assertEqual(sum(parts), Decimal("7.13"))

    def test_allocate_zero_weights(self):
        self.assertEqual(sum(allocate("5", [0, 0])), Decimal("5.00"))

    def test_fmt(self):
        self.assertEqual(fmt(-1234.5), "($1,234.50)")
        self.assertEqual(fmt(3), "$3.00")


if __name__ == "__main__":
    unittest.main()
