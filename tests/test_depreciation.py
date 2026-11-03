import unittest
from decimal import Decimal

import tests  # noqa: F401
from utils import depreciation as dep
from utils.ledger import balance_sheet


def asset(**kw):
    a = {"id": "A1", "cost": "12000", "salvage": "0", "in_service_date": "2026-01-15",
         "purchase_date": "2026-01-10", "life_months": "60", "method": "sl",
         "status": "active", "expense_category_id": "dep"}
    a.update(kw)
    return a


class DepreciationTests(unittest.TestCase):
    def test_straight_line(self):
        a = asset()
        self.assertEqual(dep.depreciation_between(a, "2026-01-01", "2026-12-31"), Decimal("2400.00"))
        self.assertEqual(dep.accumulated(a, "2030-12-31"), Decimal("12000.00"))
        self.assertEqual(dep.accumulated(a, "2035-01-01"), Decimal("12000.00"))
        self.assertEqual(dep.net_book_value(a, "2026-06-30"), Decimal("10800.00"))

    def test_straight_line_with_salvage_and_odd_cents(self):
        a = asset(cost="1000", salvage="100", life_months="36")
        total = dep.accumulated(a, "2099-12-31")
        self.assertEqual(total, Decimal("900.00"))
        months = dep.monthly_schedule(a)
        self.assertEqual(len(months), 36)

    def test_double_declining_switches_to_sl_and_respects_salvage(self):
        a = asset(cost="10000", salvage="1000", life_months="60", method="db",
                  in_service_date="2026-01-01")
        years = dep.annual_schedule(a)
        self.assertEqual(years[0]["depreciation"], Decimal("4000.00"))   # 40% of 10,000
        self.assertEqual(years[1]["depreciation"], Decimal("2400.00"))   # 40% of 6,000
        self.assertEqual(years[-1]["book_value"], Decimal("1000.00"))
        self.assertTrue(all(y["depreciation"] >= 0 for y in years))

    def test_declining_balance_partial_first_year(self):
        a = asset(cost="10000", life_months="60", method="db", in_service_date="2026-07-01")
        y1 = dep.annual_schedule(a)[0]
        self.assertEqual(y1["depreciation"], Decimal("2000.00"))         # 40% × 6/12

    def test_units_of_production(self):
        a = asset(cost="50000", salvage="5000", method="units", total_units="10000")
        usage = [{"date": "2026-02-10", "units": "1000"}, {"date": "2026-03-05", "units": "500"},
                 {"date": "2027-01-01", "units": "20000"}]
        self.assertEqual(dep.depreciation_between(a, "2026-01-01", "2026-12-31", usage),
                         Decimal("6750.00"))
        self.assertEqual(dep.accumulated(a, "2030-01-01", usage), Decimal("45000.00"))

    def test_expense_all_section_179(self):
        a = asset(method="expense")
        self.assertEqual(dep.depreciation_between(a, "2026-01-01", "2026-01-31"), Decimal("12000.00"))
        self.assertEqual(dep.net_book_value(a, "2026-02-01"), Decimal("0.00"))

    def test_macrs_tables_sum_to_100(self):
        for cls, pcts in dep.MACRS_HALF_YEAR.items():
            total = sum(Decimal(p) for p in pcts)
            self.assertAlmostEqual(float(total), 100.0, delta=0.011, msg=cls)

    def test_macrs_5_year(self):
        a = asset(cost="10000", method="macrs", macrs_class="5", in_service_date="2026-09-01")
        years = dep.annual_schedule(a)
        self.assertEqual([y["year"] for y in years], list(range(2026, 2032)))
        self.assertEqual(years[0]["depreciation"], Decimal("2000.00"))
        self.assertEqual(years[1]["depreciation"], Decimal("3200.00"))
        self.assertEqual(years[-1]["accumulated"], Decimal("10000.00"))

    def test_macrs_disposal_half_year(self):
        a = asset(cost="10000", method="macrs", macrs_class="5", in_service_date="2026-01-01",
                  status="sold", disposal_date="2028-06-30", disposal_amount="4000")
        years = dep.annual_schedule(a)
        self.assertEqual(years[-1]["year"], 2028)
        self.assertEqual(years[-1]["depreciation"], Decimal("960.00"))   # half of 19.20%

    def test_disposal_gain_and_loss(self):
        a = asset(status="sold", disposal_date="2027-01-20", disposal_amount="10000")
        # Book SL: 2026 = 12 months × 200 + Jan 2027 = 2,600 → NBV 9,400
        self.assertEqual(dep.gain_on_disposal(a), Decimal("600.00"))
        self.assertEqual(dep.net_book_value(a, "2027-02-01"), Decimal("0"))
        b = asset(status="disposed", disposal_date="2027-01-20", disposal_amount="0")
        self.assertEqual(dep.gain_on_disposal(b), Decimal("-9400.00"))

    def test_prepaid_amortization(self):
        p = asset(cost="1200", life_months="12", in_service_date="2026-04-01", method="sl")
        self.assertEqual(dep.depreciation_between(p, "2026-04-01", "2026-12-31"), Decimal("900.00"))
        self.assertEqual(dep.net_book_value(p, "2026-12-31"), Decimal("300.00"))

    def test_balance_sheet_balances_through_purchase_dep_and_sale(self):
        cats = {"fa": {"id": "fa", "type": "asset"}, "inc": {"id": "inc", "type": "income"}}
        accts = [{"id": "chk", "name": "Checking", "type": "checking", "opening_balance": "20000"}]
        txns = [{"date": "2026-01-10", "account_id": "chk", "amount": "-12000", "category_id": "fa"},
                {"date": "2027-01-20", "account_id": "chk", "amount": "10000", "category_id": "fa"}]
        a = asset(status="sold", disposal_date="2027-01-20", disposal_amount="10000")
        for as_of in ("2026-06-30", "2026-12-31", "2027-03-31"):
            fy = f"{as_of[:4]}-01-01"
            p = dep.portfolio([a], as_of, fy)
            bs = balance_sheet(accts, txns, cats, as_of, fy,
                               extra_assets=[("Fixed assets", p["nbv"])],
                               noncash_pnl_before_fy=p["gain_before"] - p["dep_before"],
                               noncash_pnl_in_fy=p["gain_in_range"] - p["dep_in_range"])
            self.assertTrue(bs["balanced"])
            self.assertEqual(bs["other_adjustments"], Decimal("0"), as_of)


if __name__ == "__main__":
    unittest.main()
