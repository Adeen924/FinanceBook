import unittest
from decimal import Decimal

import tests  # noqa: F401
from utils.ledger import (balance_sheet, owner_investment, net_income,
                          unexplained_breakdown, account_class)

CATS = {
    "inc": {"id": "inc", "type": "income"},
    "sup": {"id": "sup", "type": "expense"},
    "dr":  {"id": "dr", "type": "debt_repayment"},
    "fa":  {"id": "fa", "type": "asset"},
}


def acct(i, name, typ, role="", ob=0, active="1"):
    return {"id": i, "name": name, "type": typ, "role": role,
            "opening_balance": str(ob), "active": active}


def txn(i, d, a, amt, cat="", tr="0", pair="", paid_by=""):
    return {"id": i, "date": d, "account_id": a, "amount": str(amt),
            "category_id": cat, "is_transfer": tr, "transfer_pair_id": pair,
            "paid_by": paid_by, "created_at": d}


ACCOUNTS = [
    acct("cap", "Capital", "equity", "owner_capital"),
    acct("drw", "Draws", "equity", "owner_draws"),
    acct("due", "Due to Owner", "liability", "due_to_owner"),
    acct("chk", "Checking", "checking"),
    acct("cc", "Card", "credit card", ob=-100),
]


class BalanceSheetTests(unittest.TestCase):
    def check(self, bs):
        self.assertTrue(bs["balanced"])
        self.assertEqual(bs["total_assets"] - bs["total_liabilities"], bs["total_equity"])
        self.assertEqual(sum(x[1] for x in bs["equity"]), bs["total_equity"])

    def test_owner_paid_capital_no_bank(self):
        # Owner pays $500 of supplies personally, as a capital contribution.
        txns = [txn("1", "2026-03-01", "cap", -500, "sup")]
        bs = balance_sheet(ACCOUNTS, txns, CATS, "2026-12-31")
        self.check(bs)
        eq = dict((k, v) for k, v, _ in bs["equity"])
        self.assertEqual(eq["Owner contributions"], Decimal("500.00"))
        self.assertEqual(eq["Net income (this year)"], Decimal("-500.00"))
        self.assertEqual(bs["other_adjustments"], Decimal("0"))

    def test_owner_paid_reimbursable_then_reimbursed(self):
        txns = [
            txn("1", "2026-02-01", "due", -300, "sup"),            # company owes me
            txn("2", "2026-02-02", "cap", -1000, tr="1", pair="p1"),  # capital to bank
            txn("3", "2026-02-02", "chk", 1000, tr="1", pair="p1"),
            txn("4", "2026-02-03", "chk", 2000, "inc"),
        ]
        bs = balance_sheet(ACCOUNTS, txns, CATS, "2026-02-28")
        self.check(bs)
        liab = dict((k, v) for k, v, _ in bs["liabilities"])
        self.assertEqual(liab["Due to Owner"], Decimal("300.00"))
        # Reimburse: checking -> Due to Owner
        txns += [txn("5", "2026-02-10", "chk", -300, tr="1", pair="p2"),
                 txn("6", "2026-02-10", "due", 300, tr="1", pair="p2")]
        bs = balance_sheet(ACCOUNTS, txns, CATS, "2026-02-28")
        self.check(bs)
        liab = dict((k, v) for k, v, _ in bs["liabilities"])
        self.assertEqual(liab["Due to Owner"], Decimal("0.00"))

    def test_draws_and_retained_earnings(self):
        txns = [
            txn("1", "2025-06-01", "chk", 5000, "inc"),
            txn("2", "2026-01-15", "chk", 1200, "inc"),
            txn("3", "2026-01-20", "chk", -700, tr="1", pair="d"),
            txn("4", "2026-01-20", "drw", 700, tr="1", pair="d"),
        ]
        bs = balance_sheet(ACCOUNTS, txns, CATS, "2026-06-30")
        self.check(bs)
        eq = dict((k, v) for k, v, _ in bs["equity"])
        self.assertEqual(eq["Retained earnings (prior years)"], Decimal("5000.00"))
        self.assertEqual(eq["Net income (this year)"], Decimal("1200.00"))
        self.assertEqual(eq["Owner draws"], Decimal("-700.00"))
        self.assertEqual(eq["Opening balance equity"], Decimal("-100.00"))

    def test_other_adjustments_equals_unexplained(self):
        txns = [
            txn("1", "2026-01-01", "chk", 1000, "inc"),
            txn("2", "2026-01-05", "chk", -250, "dr"),         # principal, no liability side
            txn("3", "2026-01-06", "chk", -40, tr="1", pair="lost"),  # orphan transfer
        ]
        bs = balance_sheet(ACCOUNTS, txns, CATS, "2026-12-31")
        self.check(bs)
        br = unexplained_breakdown(txns, CATS, "2026-12-31")
        self.assertEqual(bs["other_adjustments"], br["non_pnl"] + br["unmatched_transfers"])
        self.assertEqual(bs["other_adjustments"], Decimal("-290.00"))

    def test_computed_fixed_asset_balances_with_depreciation(self):
        # Buy a $10,000 machine (capitalised), $2,000 depreciation this year.
        txns = [txn("1", "2026-01-02", "cap", -10000, tr="1", pair="c"),
                txn("2", "2026-01-02", "chk", 10000, tr="1", pair="c"),
                txn("3", "2026-01-03", "chk", -10000, "fa")]
        bs = balance_sheet(ACCOUNTS, txns, CATS, "2026-12-31",
                           extra_assets=[("Fixed assets (net)", Decimal("8000"))],
                           noncash_pnl_in_fy=Decimal("-2000"))
        self.check(bs)
        self.assertEqual(bs["other_adjustments"], Decimal("0"))

    def test_inactive_account_with_balance_still_counted(self):
        accts = ACCOUNTS + [acct("old", "Old Savings", "savings", ob=50, active="0")]
        bs = balance_sheet(accts, [], CATS, "2026-12-31")
        self.check(bs)
        self.assertIn("Old Savings (inactive)", [a[0] for a in bs["assets"]])

    def test_account_class(self):
        self.assertEqual(account_class({"type": "equity"}), "equity")
        self.assertEqual(account_class({"type": "credit card"}), "liability")
        self.assertEqual(account_class({"type": "cash"}), "asset")


class OwnerInvestmentTests(unittest.TestCase):
    def test_ledger(self):
        txns = [
            txn("1", "2026-01-10", "cap", -200, "sup"),              # contribution
            txn("2", "2026-02-10", "due", -150, "sup"),              # owed to me
            txn("3", "2026-03-01", "due", -50, "sup"),
            txn("4", "2026-03-05", "chk", -120, tr="1", pair="r"),    # reimburse
            txn("5", "2026-03-05", "due", 120, tr="1", pair="r"),
            txn("6", "2026-04-01", "chk", -80, tr="1", pair="d"),     # draw
            txn("7", "2026-04-01", "drw", 80, tr="1", pair="d"),
            txn("8", "2026-04-02", "chk", -10, "sup", paid_by="OWN"),  # flagged only
        ]
        r = owner_investment(ACCOUNTS, txns, "2026-01-01", "2026-12-31", owner_id="OWN")
        self.assertEqual(r["contributed"], Decimal("200.00"))
        self.assertEqual(r["lent"], Decimal("200.00"))
        self.assertEqual(r["reimbursed"], Decimal("120.00"))
        self.assertEqual(r["drawn"], Decimal("80.00"))
        self.assertEqual(r["owed_now"], Decimal("80.00"))
        self.assertEqual(r["net_investment"], Decimal("200.00"))   # 200 - 80 + 80
        self.assertEqual(len(r["items"]), 6)

    def test_date_range_and_owed_as_of_end(self):
        txns = [txn("1", "2026-01-10", "due", -100, "sup"),
                txn("2", "2026-05-10", "due", -100, "sup")]
        r = owner_investment(ACCOUNTS, txns, "2026-05-01", "2026-05-31")
        self.assertEqual(r["lent"], Decimal("100.00"))
        self.assertEqual(r["owed_now"], Decimal("200.00"))
        r = owner_investment(ACCOUNTS, txns, "2026-01-01", "2026-01-31")
        self.assertEqual(r["owed_now"], Decimal("100.00"))

    def test_net_income_excludes_transfers_and_non_pnl(self):
        txns = [txn("1", "2026-01-01", "chk", 100, "inc"),
                txn("2", "2026-01-02", "chk", -30, "dr"),
                txn("3", "2026-01-03", "chk", -5, tr="1")]
        self.assertEqual(net_income(txns, CATS), Decimal("100.00"))


if __name__ == "__main__":
    unittest.main()
