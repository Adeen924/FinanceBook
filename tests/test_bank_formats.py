"""v1.16 bank CSV formats: every known bank export in tests/fixtures/bank_csv
parses with the right count, dates, payee, memo and sign (money out negative,
money in positive); the registry, its fixtures and docs/BANK_FORMATS.md agree;
the generic fallback reads type columns; duplicate keys stay compatible with
files imported by v1.15."""
import csv
import html
import io
import os
import re
import unittest

import tests  # noqa: F401  (puts app/ on sys.path)
from parsers import bank_formats
from parsers.spreadsheet import analyze_spreadsheet, parse_spreadsheet

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "bank_csv")
DOC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "BANK_FORMATS.md")


def fixture_bytes(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def kind_of(fmt_id):
    return bank_formats.BY_ID[fmt_id].kind


def _chase_credit_expected():
    """Independent oracle for the 79-row Chase card file: Transaction Date,
    unescaped / space-collapsed Description, every "Sale" is money out."""
    rows = list(csv.DictReader(io.StringIO(fixture_bytes("chase_credit.csv").decode())))
    out = []
    for r in rows:
        m, d, y = r["Transaction Date"].split("/")
        sign = -1 if r["Type"] == "Sale" else 1
        out.append((f"{y}-{m}-{d}", sign * abs(float(r["Amount"])),
                    re.sub(r"\s+", " ", html.unescape(r["Description"])).strip(), ""))
    return out


# fixture -> (registry id, [(date, amount, payee, memo), ...] for every row)
FIXTURE_EXPECTATIONS = {
    "bmo_checking.csv": ("bmo_checking", [
        ('2026-11-10', -500.0, 'ZELLE TO ALEX SAMPLE', ''),
        ('2026-11-10', 500.0, 'PPD JORDAN SAMPLE CO PAYROLL', ''),
        ('2026-11-04', -550.0, 'ZELLE TO ALEX SAMPLE', ''),
        ('2026-11-03', 550.0, 'PPD JORDAN SAMPLE CO PAYROLL', ''),
        ('2026-11-02', -3000.0, 'ZELLE TO ALEX SAMPLE', ''),
        ('2026-11-02', -500.0, 'ZELLE TO JORDAN SAMPLE', ''),
        ('2026-11-02', -370.0, 'ZELLE TO ALEX SAMPLE', ''),
        ('2026-10-30', -650.0, 'ZELLE TO ALEX SAMPLE', ''),
        ('2026-10-30', -550.0, 'ZELLE TO JORDAN SAMPLE', ''),
        ('2026-10-30', -300.0, 'ZELLE TO ALEX SAMPLE', ''),
        ('2026-10-29', -250.0, 'ZELLE TO CASEY EXAMPLE', ''),
        ('2026-10-16', 5600.0, 'DDA DEPOSIT', ''),
        ('2026-10-13', 25.0, 'PC TRANSFER CREDIT', ''),
    ]),
    "applecard_credit.csv": ("applecard_credit", [
        ('2026-10-30', -42.18, "Trader Joe's", "TRADER JOE'S #021 8657 VILLA LA JOLLA DR SAN DIEGO 92037 CA USA"),
        ('2026-10-29', 500.0, 'Ach Deposit Internet Transfer From Account Ending In 9921', 'ACH DEPOSIT INTERNET TRANSFER FROM ACCOUNT ENDING IN 9921'),
        ('2026-10-27', 18.99, 'Amazon Marketplace', 'AMAZON MKTPL*ZX81Q2 Amzn.com/bill WA'),
        ('2026-10-25', -83.25, 'Apple', 'APPLE ONLINE STORE CUPERTINO CA'),
        ('2026-10-24', -23.41, 'Uber', 'UBER *TRIP HELP.UBER.COM CA USA'),
        ('2026-10-31', -6.12, 'Apple Card', 'MONTHLY INTEREST CHARGED'),
    ]),
    "bankofamerica_checking.csv": ("bankofamerica_checking", [
        ('2026-10-02', -60.0, 'Zelle payment to MORGAN LEE Conf# k3j2h1q8x', ''),
        ('2026-10-03', 3250.0, 'ACME CORP DES:PAYROLL ID:XXXXX44120 INDN:JORDAN PATEL CO ID:XXXXX12345 PPD', ''),
        ('2026-10-07', -84.37, 'CHECKCARD 1006 RALPHS #0108 SAN DIEGO CA 24431066279100012345678', ''),
        ('2026-10-15', -200.0, 'BKOFAMERICA ATM 10/15 #000004512 WITHDRWL CARMEL MOUNTAIN', ''),
        ('2026-10-20', -840.0, 'Check 2051', ''),
    ]),
    "bankofamerica_credit.csv": ("bankofamerica_credit", [
        ('2026-10-28', -42.18, 'TRADER JOE S #021 SAN DIEGO CA', ''),
        ('2026-10-26', -15.49, 'NETFLIX.COM 866-579-7172 CA', ''),
        ('2026-10-25', 500.0, 'PAYMENT - THANK YOU', ''),
        ('2026-10-22', 23.99, 'AMAZON MKTPLACE PMTS AMZN.COM/BILL WA', ''),
        ('2026-10-21', -48.2, 'SHELL OIL 12477461003 SAN DIEGO CA', ''),
    ]),
    "capitalone_checking.csv": ("capitalone_checking", [
        ('2026-11-15', 10.0, 'Zelle money received from CASEY NGUYEN', ''),
        ('2026-11-15', 100.0, 'Zelle money received from SAM RIVERA', ''),
        ('2026-11-09', -99.87, 'Withdrawal from Kalshi Kalshi Acc', ''),
        ('2026-11-08', 1500.0, 'Zelle money received from SAM RIVERA', ''),
        ('2026-11-08', -3000.0, 'Zelle money sent to SAM RIVERA', ''),
        ('2026-11-08', 2250.0, 'Zelle money received from DREW HOLLOWAY', ''),
        ('2026-11-06', 110.0, 'Zelle money received from SAM RIVERA', ''),
        ('2026-10-31', 0.1, 'Monthly Interest Paid', ''),
        ('2026-10-26', -15.0, 'Withdrawal from PLANET FITNESS P RETURN FEE', ''),
        ('2026-10-26', -25.05, 'Withdrawal from PLANET FITNESS P RETRY PYMT', ''),
        ('2026-10-26', -75.0, 'Withdrawal from VERIZON WIRELESS PAYMENTS', ''),
        ('2026-10-25', -105.0, 'Digital Card Purchase - KALSHI 2014640952 NY', ''),
        ('2026-10-24', -350.0, 'Zelle money sent to JAMIE ORTIZ', ''),
        ('2026-10-24', -800.0, 'Zelle money sent to ALEX RIVERA', ''),
        ('2026-10-24', 2250.0, 'Zelle money received from DREW HOLLOWAY', ''),
        ('2026-10-24', 100.0, 'Zelle money received from SAM RIVERA', ''),
        ('2026-10-24', 11.0, 'Zelle money received from SAM RIVERA', ''),
        ('2026-10-21', -5.59, 'Digital Card Purchase - CHIPOTLE 2034 SAN DIEGO CA', ''),
        ('2026-10-17', -4.3, 'Digital Card Purchase - SHELL12477461003 LA JOLLA CA', ''),
        ('2026-10-17', -20.02, 'Debit Card Purchase - EXXONMOBIL SAN DIEGO CA', ''),
        ('2026-10-16', -15.0, 'Digital Card Purchase - SPORTSPLEX USA POWAY POWAY CA', ''),
        ('2026-10-16', -31.22, 'Debit Card Purchase - PAR ONO 098 SAN DIEGO SAN DIEGO CA', ''),
        ('2026-10-16', -15.67, 'Debit Card Purchase - EXXONMOBIL SAN DIEGO CA', ''),
    ]),
    "capitalone_credit.csv": ("capitalone_credit", [
        ('2026-11-11', -19.9, 'CHICK-FIL-A #02015', ''),
        ('2026-11-12', -10.0, 'APPLE.COM/BILL', ''),
        ('2026-11-10', -9.65, '85C BAKERY CAFE USA', ''),
        ('2026-11-10', -67.0, 'Capital One purchase (Other Services)', ''),
        ('2026-11-08', -9.69, 'RALPHS #0108', ''),
        ('2026-11-05', -6.99, 'APPLE.COM/BILL', ''),
        ('2026-11-05', 230.04, 'CAPITAL ONE MOBILE PYMT', ''),
        ('2026-11-03', -19.14, 'Capital One purchase (Dining)', ''),
        ('2026-11-02', -7.75, 'Capital One purchase (Dining)', ''),
        ('2026-11-01', -21.77, 'NICKTHEGREEK', ''),
        ('2026-11-01', -22.63, 'TARGET.COM', ''),
        ('2026-10-31', -20.12, 'CHICK-FIL-A #03778', ''),
        ('2026-11-01', -10.23, 'TARGET.COM', ''),
        ('2026-11-01', -7.3, 'Capital One purchase (Dining)', ''),
        ('2026-10-31', -4.99, 'BALBOA INTERNATIONAL M', ''),
        ('2026-10-31', -18.08, 'Capital One purchase (Dining)', ''),
    ]),
    "chase_checking.csv": ("chase_checking", [
        ('2026-11-12', -7500.0, 'Online Transfer to CHK ...8842 transaction#: 40000007919 11/12', ''),
        ('2026-11-12', -7500.0, 'Online Transfer to CHK ...8842 transaction#: 40000015838 11/12', ''),
        ('2026-11-12', 20.0, 'WELLS FARGO IFI DDA TO DDA K719PLRZWA WEB ID: INTFITQXLM', ''),
        ('2026-11-10', -550.0, 'Online Transfer to CHK ...8842 transaction#: 40000023757 11/10', ''),
        ('2026-11-10', -250.0, 'Online Transfer to CHK ...8842 transaction#: 40000031676 11/10', ''),
        ('2026-11-10', -7500.0, 'Online Transfer to CHK ...8842 transaction#: 40000039595 11/10', ''),
        ('2026-11-10', 750.0, 'Online Transfer from CHK ...8842 transaction#: 40000047514', ''),
        ('2026-11-09', -7500.0, 'Online Transfer to CHK ...8842 transaction#: 40000055433 11/09', ''),
        ('2026-11-09', -100.0, 'Online Realtime Transfer to USB SAV 5512 transaction#: 40000063352 reference#: 9418827305RX 11/09', ''),
        ('2026-11-05', 1510.0, 'MSPBNA BANK TRANSFER PPD ID: 9123450001', ''),
        ('2026-11-04', 1000.0, 'Online Transfer from CHK ...8842 transaction#: 40000071271', ''),
        ('2026-11-04', 3000.0, 'CAL BANK & TRUST EXTRL XFER RIVERA, WEB ID: 4KQT871203', ''),
        ('2026-11-03', -4510.0, 'Online Transfer to CHK ...8842 transaction#: 40000079190 11/03', ''),
        ('2026-11-03', 550.0, 'Online Transfer from CHK ...8842 transaction#: 40000087109', ''),
        ('2026-11-03', 3982.0, 'Online Transfer from CHK ...8842 transaction#: 40000095028', ''),
        ('2026-11-02', -6200.0, 'Online Transfer 40000110866 to WF Biz ######6170 transaction #: 40000102947 11/02', ''),
        ('2026-11-02', 2500.0, 'Mission Federal P2P SAM RIVERA WEB ID: MFCU ACH', ''),
    ]),
    "usbank_checking.csv": ("usbank_checking", [
        ('2026-11-03', 24900.0, 'ELECTRONIC DEPOSIT CIT BANK DIV OF', ''),
        ('2026-11-05', 250.0, 'ZELLE INSTANT PMT FROM JORDAN PATEL BACq7m2xr4tz', ''),
        ('2026-11-05', -50.0, 'BILL PAY FOR BUSINESS TO Jordan Patel BILL P 512800K447QD', ''),
        ('2026-11-05', -50.0, 'BILL PAY FOR BUSINESS TO Morgan Lee BILL P 512800M19T2C', ''),
    ]),
    "usbank_credit.csv": ("usbank_credit", [
        ('2026-04-16', 82.0, 'PAYMENT THANK YOU', 'WEB AUTOMTC'),
        ('2026-04-13', -7.98, 'AMAZON MKTPL*BC3YY6SV0 Amzn.com/bill WA', ''),
        ('2026-02-19', -173.64, 'CNTY SAN DIEGO 877-8294732 IN', ''),
        ('2026-02-19', -7928.79, 'SAN DIEGO COUNTY TREAS 877-8294732 CA', ''),
    ]),
    "wellsfargo_checking.csv": ("wellsfargo_checking", [
        ('2026-11-05', -400.0, 'ONLINE TRANSFER REF #IB07QK2LP8 TO VISA SIGNATURE CARD XXXXXXXXXXXX4821 ON 11/04/26', ''),
        ('2026-11-05', -565.0, 'ZELLE TO RIVERA DANA ON 11/04 REF # WFCT48KP2M61 RENT', ''),
        ('2026-11-03', 957.0, 'SSA TREAS 310 XXSOC SEC 110326 XXXXX3159B SSA TAYLOR RIVERA', ''),
    ]),
    "wellsfargo_checking_noheader.csv": ("wellsfargo_checking_noheader", [
        ('2026-11-05', -400.0, 'ONLINE TRANSFER REF #IB07QK2LP8 TO VISA SIGNATURE CARD XXXXXXXXXXXX4821 ON 11/04/26', ''),
        ('2026-11-05', -565.0, 'ZELLE TO RIVERA DANA ON 11/04 REF # WFCT48KP2M61 RENT', ''),
        ('2026-11-04', -120.0, 'CHECK # 1047', 'Check #1047'),
        ('2026-11-03', 957.0, 'SSA TREAS 310 XXSOC SEC 110326 XXXXX3159B SSA TAYLOR RIVERA', ''),
        ('2026-11-02', -12.0, 'MONTHLY SERVICE FEE', ''),
    ]),
    "wellsfargo_credit.csv": ("wellsfargo_credit", [
        ('2026-11-13', -74.37, 'INTEREST CHARGE ON PURCHASES', ''),
        ('2026-11-04', 400.0, 'ONLINE PAYMENT THANK YOU', ''),
    ]),
    "wellsfargo_credit_noheader.csv": ("wellsfargo_credit_noheader", [
        ('2026-11-13', -74.37, 'INTEREST CHARGE ON PURCHASES', ''),
        ('2026-11-06', -42.18, 'TRADER JOE S #021 SAN DIEGO CA', ''),
        ('2026-11-05', 18.99, 'AMAZON MKTPLACE PMTS RETURN', ''),
        ('2026-11-04', 400.0, 'ONLINE PAYMENT THANK YOU', ''),
    ]),
    "chase_credit.csv": ("chase_credit", _chase_credit_expected()),
}


class FixtureTests(unittest.TestCase):
    """Each fixture parses exactly as expected when imported into an account of
    its kind."""

    def check(self, name):
        fmt_id, expected = FIXTURE_EXPECTATIONS[name]
        res = analyze_spreadsheet(fixture_bytes(name), name, "ACC", account_kind=kind_of(fmt_id))
        self.assertEqual(res.format_id, fmt_id)
        self.assertEqual(res.warnings, [])
        self.assertEqual(res.type_mismatches, 0)
        self.assertFalse(res.looks_reversed)
        self.assertEqual(res.kind_mismatch, "")
        got = [(t["date"], float(t["amount"]), t["payee"], t["memo"]) for t in res.rows]
        self.assertEqual(len(got), len(expected), name)
        for i, (g, e) in enumerate(zip(got, expected)):
            self.assertEqual(g, e, f"{name} row {i + 1}")
        return res

    def test_every_fixture(self):
        for name in FIXTURE_EXPECTATIONS:
            with self.subTest(fixture=name):
                self.check(name)

    def test_capital_one_checking_signs(self):
        res = self.check("capitalone_checking.csv")
        self.assertEqual(res.money_in, (9, 6331.1))
        self.assertEqual(res.money_out, (14, -4561.72))

    def test_capital_one_card_blank_descriptions_get_a_payee(self):
        res = self.check("capitalone_credit.csv")
        self.assertTrue(all(t["payee"] for t in res.rows))
        self.assertEqual(sum(t["payee"].startswith("Capital One purchase (") for t in res.rows), 5)

    def test_chase_card_unescapes_entities(self):
        res = self.check("chase_credit.csv")
        self.assertEqual(len(res.rows), 79)
        self.assertIn("BEST PIZZA & BREW - CARME", [t["payee"] for t in res.rows])
        self.assertFalse(any("&amp;" in t["payee"] for t in res.rows))

    def test_us_bank_drops_junk_memos_and_zero_rows(self):
        res = self.check("usbank_credit.csv")
        self.assertEqual(res.skipped_zero, 1)
        self.assertTrue(res.notes and "$0.00" in res.notes[0])
        self.check("usbank_checking.csv")

    def test_parse_spreadsheet_wrapper(self):
        rows, warnings = parse_spreadsheet(fixture_bytes("capitalone_checking.csv"),
                                           "x.csv", "ACC")
        self.assertEqual(warnings, [])
        self.assertEqual(sum(float(r["amount"]) < 0 for r in rows), 14)


class RegistryTests(unittest.TestCase):
    def test_every_entry_has_a_fixture_and_an_expectation(self):
        ids = {fmt_id for fmt_id, _ in FIXTURE_EXPECTATIONS.values()}
        for f in bank_formats.REGISTRY:
            with self.subTest(fmt=f.id):
                self.assertTrue(f.fixture, f.id)
                self.assertTrue(os.path.exists(os.path.join(FIXTURES, f.fixture)), f.fixture)
                self.assertIn(f.id, ids)
                self.assertEqual(FIXTURE_EXPECTATIONS[f.fixture][0], f.id)
                self.assertIn(f.kind, ("checking", "credit"))
                self.assertIn(f.sign, bank_formats.SIGN_STYLE_TEXT)
                self.assertTrue(f.quirks)

    def test_every_fixture_is_registered(self):
        on_disk = {n for n in os.listdir(FIXTURES) if n.endswith(".csv")}
        self.assertEqual(on_disk, set(FIXTURE_EXPECTATIONS))

    def test_ids_unique_and_shared_layouts_differ_by_kind(self):
        ids = [f.id for f in bank_formats.REGISTRY]
        self.assertEqual(len(ids), len(set(ids)))
        seen = {}
        for f in bank_formats.REGISTRY:
            key = (f.headers or f.headerless, f.kind)
            self.assertNotIn(key, seen, f"{f.id} duplicates {seen.get(key)}")
            seen[key] = f.id

    def test_account_kind_picks_between_identical_layouts(self):
        data = fixture_bytes("usbank_checking.csv")
        self.assertEqual(analyze_spreadsheet(data, "u.csv", "A", account_kind="checking")
                         .format_id, "usbank_checking")
        self.assertEqual(analyze_spreadsheet(data, "u.csv", "A", account_kind="credit")
                         .format_id, "usbank_credit")

    def test_kind_mismatch_is_reported(self):
        res = analyze_spreadsheet(fixture_bytes("chase_credit.csv"), "c.csv", "A",
                                  account_kind="checking")
        self.assertEqual(res.format_id, "chase_credit")
        self.assertIn("credit-card export", res.kind_mismatch)

    def test_doc_is_up_to_date(self):
        with open(DOC, encoding="utf-8") as f:
            self.assertEqual(f.read(), bank_formats.render_markdown(),
                             "docs/BANK_FORMATS.md is stale — run "
                             "python app/parsers/bank_formats.py --write")

    def test_doc_lists_every_entry(self):
        doc = bank_formats.render_markdown()
        for f in bank_formats.REGISTRY:
            self.assertIn(f.fixture, doc)


class V115RegressionTests(unittest.TestCase):
    """What v1.15.1 did with these files (frozen copy of its parser), next to
    what 1.16 does."""

    def legacy(self, name, **kw):
        from tests.legacy_spreadsheet_v115 import parse_spreadsheet as old
        rows, _w = old(fixture_bytes(name), name, "ACC", **kw)
        return rows

    def test_capital_one_checking_was_all_money_in(self):
        old = self.legacy("capitalone_checking.csv")
        self.assertEqual(len(old), 23)
        self.assertTrue(all(float(r["amount"]) > 0 for r in old))
        new, _w = parse_spreadsheet(fixture_bytes("capitalone_checking.csv"), "c.csv", "ACC")
        self.assertEqual(sum(float(r["amount"]) < 0 for r in new), 14)

    def test_old_quirks_now_fixed(self):
        old = {r["payee"] for r in self.legacy("chase_credit.csv")}
        self.assertIn("BEST PIZZA &amp; BREW - CARME", old)
        old = self.legacy("usbank_checking.csv")
        self.assertEqual(old[0]["memo"], "Download from usbank.com.")
        old = self.legacy("capitalone_credit.csv")
        self.assertEqual(sum(1 for r in old if not r["payee"]), 5)
        old = self.legacy("usbank_credit.csv")
        self.assertEqual(len(old), 5)                       # the $0.00 row too


class DuplicateKeyTests(unittest.TestCase):
    """A file imported by v1.15 must not import a second time: rows v1.15 read
    correctly keep v1.15's import_hash; rows it got the sign wrong on carry
    v1.15's hash as _legacy_hash so the import page can fix them."""

    def test_keys_match_v115(self):
        from tests.legacy_spreadsheet_v115 import parse_spreadsheet as old
        for name, (fmt_id, _e) in FIXTURE_EXPECTATIONS.items():
            if "noheader" in name:
                continue            # v1.15 couldn't read header-less files
            with self.subTest(fixture=name):
                data = fixture_bytes(name)
                legacy, _w = old(data, name, "ACC")
                if not legacy:
                    continue        # v1.15 couldn't read it (Apple's "Amount (USD)")
                legacy = {(r["date"], r["import_hash"]): r for r in legacy}
                new = analyze_spreadsheet(data, name, "ACC", account_kind=kind_of(fmt_id)).rows
                for t in new:
                    lh = t.get("_legacy_hash")
                    if lh:
                        old_row = legacy[(t["date"], lh)]
                        self.assertEqual(float(old_row["amount"]), -float(t["amount"]))
                        self.assertNotIn((t["date"], t["import_hash"]), legacy)
                    else:
                        old_row = legacy[(t["date"], t["import_hash"])]
                        self.assertEqual(float(old_row["amount"]), float(t["amount"]))

    def test_keys_stable_between_runs_and_unique_per_line(self):
        for name, (fmt_id, _e) in FIXTURE_EXPECTATIONS.items():
            with self.subTest(fixture=name):
                a = analyze_spreadsheet(fixture_bytes(name), name, "ACC",
                                        account_kind=kind_of(fmt_id)).rows
                b = analyze_spreadsheet(fixture_bytes(name), name, "ACC",
                                        account_kind=kind_of(fmt_id)).rows
                self.assertEqual([t["import_hash"] for t in a], [t["import_hash"] for t in b])
                self.assertEqual(len({t["import_hash"] for t in a}), len(a))

    def test_flip_changes_keys_consistently(self):
        data = fixture_bytes("wellsfargo_checking.csv")
        a = analyze_spreadsheet(data, "w.csv", "ACC", reverse_signs=True).rows
        b = analyze_spreadsheet(data, "w.csv", "ACC", reverse_signs=True).rows
        self.assertEqual([t["import_hash"] for t in a], [t["import_hash"] for t in b])
        self.assertEqual([float(t["amount"]) for t in a], [400.0, 565.0, -957.0])


class GenericFallbackTests(unittest.TestCase):
    def parse(self, text, **kw):
        return analyze_spreadsheet(text.encode(), "bank.csv", "ACC", **kw)

    def amounts(self, res):
        return [float(t["amount"]) for t in res.rows]

    def test_unsigned_amount_with_type_column(self):
        res = self.parse("Date,Description,Type,Amount\n"
                         "01/05/2026,COFFEE,Withdrawal,4.50\n"
                         "01/06/2026,PAYROLL,Deposit,1000\n"
                         "01/07/2026,ATM,DEBIT,40\n")
        self.assertIsNone(res.format)
        self.assertEqual(res.format_label, "Unknown — generic")
        self.assertEqual(self.amounts(res), [-4.5, 1000, -40])

    def test_card_words_sale_payment_return(self):
        text = ("Date,Description,Transaction Type,Amount\n"
                "01/05/2026,STORE,Sale,30\n01/06/2026,AUTOPAY,Payment,100\n"
                "01/07/2026,STORE,Return,30\n01/08/2026,FEE,Purchase,2\n")
        self.assertEqual(self.amounts(self.parse(text, account_kind="credit")),
                         [-30, 100, 30, -2])

    def test_signed_amount_type_column_is_only_a_check(self):
        res = self.parse("Date,Description,Details,Amount\n"
                         "01/05/2026,COFFEE,DEBIT,-4.50\n"
                         "01/06/2026,PAYROLL,CREDIT,1000\n"
                         "01/07/2026,ODD,CREDIT,-3\n")
        self.assertEqual(self.amounts(res), [-4.5, 1000, -3])
        self.assertEqual(res.type_mismatches, 1)

    def test_non_type_column_ignored(self):
        res = self.parse("Date,Description,Type,Amount\n"
                         "01/05/2026,COFFEE,ACH_DEBIT,4.50\n")
        self.assertEqual(self.amounts(res), [4.5])

    def test_html_entities_and_spaces(self):
        res = self.parse("Date,Description,Amount\n"
                         "01/05/2026,\"AT&amp;T   WIRELESS\",-80\n")
        self.assertEqual(res.rows[0]["payee"], "AT&T WIRELESS")

    def test_blank_description_never_blank(self):
        res = self.parse("Date,Description,Category,Amount\n"
                         "01/05/2026,,Dining,-8\n01/06/2026,,,-9\n")
        self.assertEqual([t["payee"] for t in res.rows], ["Dining", "(no description)"])

    def test_memo_junk(self):
        res = self.parse("Date,Description,Memo,Amount\n"
                         "01/05/2026,STORE,123456789012345678,-8\n"
                         "01/06/2026,STORE,\"WEB PMT; 00300; ; ;\",-9\n"
                         "01/07/2026,STORE,birthday gift,-10\n")
        self.assertEqual([t["memo"] for t in res.rows], ["", "WEB PMT", "birthday gift"])

    def test_zero_rows_skipped(self):
        res = self.parse("Date,Description,Amount\n01/05/2026,FEE,0.00\n"
                         "01/06/2026,FEE,-0.00\n01/07/2026,X,-1\n")
        self.assertEqual(self.amounts(res), [-1])
        self.assertEqual(res.skipped_zero, 2)

    def test_chase_card_type_disagreement_is_counted(self):
        text = ("Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
                "11/01/2026,11/02/2026,STORE,Shopping,Sale,-30.00,\n"
                "11/03/2026,11/03/2026,Payment Thank You-Mobile,,Payment,500.00,\n"
                "11/04/2026,11/05/2026,STORE,Shopping,Return,30.00,\n"
                "11/05/2026,11/05/2026,LATE FEE,Fees,Fee,-39.00,\n"
                "11/06/2026,11/06/2026,ODD,Shopping,Sale,12.00,\n"
                "11/07/2026,11/07/2026,FIX,,Adjustment,-5.00,\n")
        res = self.parse(text, account_kind="credit")
        self.assertEqual(res.format_id, "chase_credit")
        self.assertEqual(self.amounts(res), [-30, 500, 30, -39, 12, -5])
        self.assertEqual(res.type_mismatches, 1)

    def test_apple_card_types_disagree_when_flipped(self):
        res = analyze_spreadsheet(fixture_bytes("applecard_credit.csv"), "a.csv", "A",
                                  account_kind="credit", reverse_signs=True)
        self.assertEqual(res.type_mismatches, len(res.rows))

    def test_manual_mapping_still_works(self):
        res = self.parse("When,What,How much\n01/05/2026,STORE,-8\n",
                         col_mapping={"date": "When", "payee": "What", "amount": "How much"})
        self.assertEqual((res.rows[0]["payee"], self.amounts(res)), ("STORE", [-8]))


class CardSanityTests(unittest.TestCase):
    def test_reversed_card_file_is_flagged(self):
        text = ("Date,Description,Amount\n01/05/2026,STORE,42.10\n"
                "01/06/2026,GAS,30\n01/07/2026,AUTOPAY PAYMENT,-500\n")
        res = analyze_spreadsheet(text.encode(), "c.csv", "A", account_kind="credit")
        self.assertTrue(res.looks_reversed)
        flipped = analyze_spreadsheet(text.encode(), "c.csv", "A", account_kind="credit",
                                      reverse_signs=True)
        self.assertFalse(flipped.looks_reversed)
        self.assertEqual([float(t["amount"]) for t in flipped.rows], [-42.1, -30, 500])

    def test_only_for_card_accounts(self):
        text = "Date,Description,Amount\n01/05/2026,A,42\n01/06/2026,B,30\n"
        self.assertFalse(analyze_spreadsheet(text.encode(), "c.csv", "A",
                                             account_kind="checking").looks_reversed)

    def test_real_card_fixtures_pass(self):
        for name, (fmt_id, _e) in FIXTURE_EXPECTATIONS.items():
            if kind_of(fmt_id) == "credit":
                res = analyze_spreadsheet(fixture_bytes(name), name, "A", account_kind="credit")
                self.assertFalse(res.looks_reversed, name)

    def test_account_kind_for(self):
        from parsers.spreadsheet import account_kind_for
        self.assertEqual(account_kind_for("credit card"), "credit")
        self.assertEqual(account_kind_for("Credit"), "credit")
        self.assertEqual(account_kind_for("checking"), "checking")
        self.assertEqual(account_kind_for("savings"), "checking")


class HeaderlessTests(unittest.TestCase):
    def test_wells_fargo_shape_needs_every_row_to_match(self):
        good = fixture_bytes("wellsfargo_checking_noheader.csv").decode()
        bad = good + '"not a date","-1","*","","X"\n'
        self.assertEqual(analyze_spreadsheet(good.encode(), "w.csv", "A").format_id,
                         "wellsfargo_checking_noheader")
        self.assertIsNone(analyze_spreadsheet(bad.encode(), "w.csv", "A").format)


if __name__ == "__main__":
    unittest.main()
