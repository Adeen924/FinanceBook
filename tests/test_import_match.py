"""v1.17 matching engine (utils/matching.py) and how an import applies it
(Database.import_transactions): mirror / manual / transfer / rule-transfer
kinds, the ±3-day window, signs, one-to-one assignment, tie-breaks, and the
things that are never matched (reconciled, locked, split, duplicates). Also the
Transfers page's detection agrees with the engine, and an import is all or
nothing."""
import os
import shutil
import tempfile
import unittest

import tests  # noqa: F401
from sheets.client import Database
from utils import matching
from utils.matching import find_matches, find_transfer_pairs, MIRROR, MANUAL, TRANSFER, RULE


class EngineBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.dir, "m.db"))
        self.chk = self.db.save_account({"name": "WF Checking", "type": "checking"})["id"]
        self.sav = self.db.save_account({"name": "Savings", "type": "savings"})["id"]
        self.card = self.db.save_account({"name": "Visa 4821", "type": "credit card"})["id"]
        self.food = self.db.save_category({"name": "Food", "type": "expense"})["id"]
        self._n = 0

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def add(self, account, date, amount, payee="", **kw):
        d = {"date": date, "account_id": account, "payee": payee, "amount": str(amount)}
        d.update(kw)
        return self.db.save_transaction(d)

    def row(self, date, amount, payee="BANK ROW", account=None, **kw):
        self._n += 1
        r = {"date": date, "account_id": account or self.chk, "payee": payee, "memo": "",
             "amount": str(amount), "import_hash": f"h{self._n}-{date}-{amount}"}
        r.update(kw)
        return r

    def match(self, rows, account=None):
        return find_matches(self.db, rows, account or self.chk)


class MatchKindTests(EngineBase):
    def test_mirror_same_account_same_sign(self):
        self.db.save_transfer_pair({"date": "2026-05-01", "account_id": self.sav,
                                    "amount": "-75"}, self.chk, keep_sign=True)
        mirror = [t for t in self.db.get_transactions(account_id=self.chk)][0]
        m = self.match([self.row("2026-05-02", 75)])[0]
        self.assertEqual(m.kind, MIRROR)
        self.assertEqual(m.chosen.id, mirror["id"])

    def test_manual_same_account_same_sign(self):
        typed = self.add(self.chk, "2026-11-04", -565, "Rent", category_id=self.food)
        m = self.match([self.row("2026-11-05", -565)])[0]
        self.assertEqual((m.kind, m.chosen.id), (MANUAL, typed["id"]))
        # The opposite sign in the same account is not the same entry.
        self.assertEqual(self.match([self.row("2026-11-05", 565)])[0].kind, "")

    def test_imported_entry_in_same_account_is_not_a_manual_match(self):
        self.add(self.chk, "2026-11-04", -565, "X", import_hash="old")
        self.assertEqual(self.match([self.row("2026-11-05", -565)])[0].kind, "")

    def test_transfer_other_account_opposite_sign(self):
        other = self.add(self.card, "2026-11-04", 400, "ONLINE PAYMENT THANK YOU",
                         import_hash="c1")
        m = self.match([self.row("2026-11-05", -400, "ONLINE TRANSFER TO VISA")])[0]
        self.assertEqual((m.kind, m.chosen.id), (TRANSFER, other["id"]))
        # Same sign in another account is not a transfer.
        self.assertEqual(self.match([self.row("2026-11-05", 400)])[0].kind, "")

    def test_rule_transfer_only_searches_the_rules_account(self):
        on_card = self.add(self.card, "2026-11-04", 50, "PAYMENT")
        r = self.row("2026-11-04", -50, _transfer_to=self.sav)
        m = self.match([r])[0]
        self.assertEqual((m.kind, m.candidates, m.rule_account), (RULE, [], self.sav))
        on_sav = self.add(self.sav, "2026-11-05", 50, "XFER IN")
        m = self.match([r])[0]
        self.assertEqual((m.kind, m.chosen.id), (RULE, on_sav["id"]))
        self.assertNotEqual(m.chosen.id, on_card["id"])

    def test_mirror_beats_manual(self):
        self.add(self.chk, "2026-05-02", 75, "typed")
        self.db.save_transfer_pair({"date": "2026-05-04", "account_id": self.sav,
                                    "amount": "-75"}, self.chk, keep_sign=True)
        m = self.match([self.row("2026-05-02", 75)])[0]
        self.assertEqual(m.kind, MIRROR)

    def test_window_three_days_in_four_out(self):
        self.add(self.card, "2026-11-01", 400)
        self.assertEqual(self.match([self.row("2026-11-04", -400)])[0].kind, TRANSFER)
        self.assertEqual(self.match([self.row("2026-11-05", -400)])[0].kind, "")
        self.assertEqual(self.match([self.row("2026-10-29", -400)])[0].kind, TRANSFER)
        self.assertEqual(self.match([self.row("2026-10-28", -400)])[0].kind, "")

    def test_exact_amount_required(self):
        self.add(self.card, "2026-11-04", 400.01)
        self.assertEqual(self.match([self.row("2026-11-04", -400)])[0].kind, "")


class NeverMatchedTests(EngineBase):
    def test_reconciled_split_and_transfer_rows_never_matched(self):
        self.add(self.card, "2026-11-04", 400, reconciled="1")
        self.add(self.card, "2026-11-04", 400, split_group_id="G1")
        self.add(self.chk, "2026-11-04", -400, "typed", reconciled="1")
        self.assertEqual(self.match([self.row("2026-11-04", -400)])[0].kind, "")

    def test_locked_period_never_matched(self):
        self.add(self.card, "2026-10-30", 400)
        self.add(self.chk, "2026-10-31", -400, "typed")
        self.db.set_lock_date("2026-10-31")
        self.assertEqual(self.match([self.row("2026-11-01", -400)])[0].kind, "")

    def test_duplicates_not_matched(self):
        self.add(self.card, "2026-11-04", 400)
        self.add(self.chk, "2026-11-01", -12, "old", import_hash="dup")
        rows = [self.row("2026-11-04", -400, import_hash="dup")]
        self.assertEqual(self.match(rows)[0].kind, "")
        # The same hash twice in one file: only the first can match.
        rows = [self.row("2026-11-04", -400, import_hash="x"),
                self.row("2026-11-04", -400, import_hash="x")]
        ms = self.match(rows)
        self.assertEqual((ms[0].kind, ms[1].kind), (TRANSFER, ""))


class OneToOneTests(EngineBase):
    def test_repeated_equal_amounts_pair_one_to_one(self):
        ins = [self.add(self.sav, f"2026-03-0{d}", 7500, "XFER FROM CHK") for d in (2, 3, 4, 5)]
        rows = [self.row(f"2026-03-0{d}", -7500, "TRANSFER TO SAV") for d in (2, 3, 4, 5)]
        ms = self.match(rows)
        chosen = [m.chosen.id for m in ms]
        self.assertEqual(chosen, [t["id"] for t in ins])        # each same-day pair
        self.assertEqual(len(set(chosen)), 4)
        self.assertTrue(all(m.confident for m in ms))

    def test_two_rows_cannot_claim_one_candidate(self):
        only = self.add(self.card, "2026-11-04", 400)
        ms = self.match([self.row("2026-11-03", -400), self.row("2026-11-04", -400)])
        self.assertEqual(ms[0].kind, "")                      # the farther row loses
        self.assertEqual(ms[1].chosen.id, only["id"])

    def test_several_candidates_offer_a_choice(self):
        a = self.add(self.chk, "2026-11-03", -565, "Rent A")
        b = self.add(self.chk, "2026-11-05", -565, "Rent B")
        m = self.match([self.row("2026-11-05", -565)])[0]
        self.assertEqual(m.kind, MANUAL)
        self.assertEqual([c.id for c in m.candidates], [b["id"], a["id"]])   # closer first
        self.assertIsNone(m.chosen)
        self.assertFalse(m.confident)

    def test_ties_broken_by_date_then_hints(self):
        plain = self.add(self.sav, "2026-04-02", 300, "DEPOSIT")
        hinted = self.add(self.sav, "2026-04-02", 300, "TRANSFER FROM CHECKING")
        far = self.add(self.sav, "2026-04-03", 300, "ONLINE TRANSFER")
        m = self.match([self.row("2026-04-02", -300, "WITHDRAWAL")])[0]
        self.assertEqual([c.id for c in m.candidates], [hinted["id"], plain["id"], far["id"]])
        # Date beats hints: same day plain over a day off with hints.
        self.assertGreater(m.candidates[1].score, m.candidates[2].score - 25)

    def test_account_name_and_last4_are_hints(self):
        acct = {"name": "Chase Visa 4821"}
        self.assertEqual(matching.hint_count("PAYMENT TO CARD XXXX4821", acct), 2)
        self.assertEqual(matching.hint_count("SOMETHING VISA", acct), 1)
        self.assertEqual(matching.hint_count("CHECKING DEPOSIT", {"name": "My Checking"}), 0)


class TransfersPageAgreesTests(EngineBase):
    def test_detect_uses_engine_one_to_one(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication([])  # noqa: F841
            from ui.pages.transfers import TransfersPage
        except Exception as e:      # pragma: no cover
            self.skipTest(f"PyQt6 not available: {e}")
        outs = [self.add(self.chk, f"2026-03-0{d}", -7500, "TRANSFER", import_hash=f"o{d}")
                for d in (2, 3)]
        ins = [self.add(self.sav, f"2026-03-0{d}", 7500, "XFER", import_hash=f"i{d}")
               for d in (2, 3)]
        self.add(self.chk, "2026-03-02", -20, "COFFEE", import_hash="cof")
        page = TransfersPage(self.db)
        detected = page._detect({})
        pairs = find_transfer_pairs(self.db)
        key = lambda ps: sorted((p["txn_a"]["id"], p["txn_b"]["id"]) for p in ps)
        self.assertEqual(key(detected), key(pairs))
        self.assertEqual(key(pairs), sorted([(outs[0]["id"], ins[0]["id"]),
                                             (outs[1]["id"], ins[1]["id"])]))
        # The import engine pairs the same way when the outs are new bank rows.
        for t in outs:
            self.db.delete_transaction(t["id"])
        rows = [self.row(t["date"], -7500, "TRANSFER") for t in outs]
        ms = self.match(rows)
        self.assertEqual([m.chosen.id for m in ms], [t["id"] for t in ins])
        page.deleteLater()


class ApplyImportTests(EngineBase):
    def _rows_with_matches(self, rows, account=None):
        ms = self.match(rows, account)
        out = []
        for r, m in zip(rows, ms):
            r = dict(r)
            if m.chosen:
                r["_match"] = {"kind": m.kind, "id": m.chosen.id}
            out.append(r)
        return out

    def test_transfer_link_files_both_as_account_transfers(self):
        other = self.add(self.card, "2026-11-04", 400, "PAYMENT", import_hash="c1",
                         category_id=self.food)
        res = self.db.import_transactions(self._rows_with_matches(
            [self.row("2026-11-05", -400, "TRANSFER TO VISA")]))
        self.assertEqual(res["linked"], 1)
        mine = res["saved"][0]
        theirs = self.db.get_transaction(other["id"])
        tcat = self.db.get_transfer_category_id()
        for t in (mine, theirs):
            self.assertEqual((t["is_transfer"], t["category_id"]), ("1", tcat))
        self.assertEqual(mine["transfer_pair_id"], theirs["transfer_pair_id"])
        # Unlink: two ordinary, uncategorized entries again.
        self.db.unlink_transfer(mine["id"])
        for tid in (mine["id"], theirs["id"]):
            t = self.db.get_transaction(tid)
            self.assertEqual((t["is_transfer"], t["transfer_pair_id"], t["category_id"]),
                             ("0", "", ""))

    def test_manual_merge_keeps_users_fields_and_logs_before_values(self):
        cls = self.db.save_class({"name": "Home"})["id"]
        typed = self.add(self.chk, "2026-11-04", -565, "Rent", category_id=self.food,
                         class_id=cls, notes="November")
        res = self.db.import_transactions(self._rows_with_matches(
            [self.row("2026-11-05", -565, "ZELLE TO LANDLORD", category_id="")]))
        self.assertEqual(res["merged"], 1)
        txns = self.db.get_transactions(account_id=self.chk)
        self.assertEqual(len(txns), 1)
        t = txns[0]
        self.assertEqual(t["id"], typed["id"])
        self.assertEqual((t["payee"], t["category_id"], t["class_id"], t["notes"]),
                         ("Rent", self.food, cls, "November"))
        self.assertEqual(t["date"], "2026-11-05")
        self.assertTrue(t["import_hash"])
        self.assertEqual(t["memo"], "ZELLE TO LANDLORD")     # bank's text kept, searchable
        log = [e for e in self.db.get_audit_log("transactions")
               if e["row_id"] == typed["id"] and e["op"] == "update"]
        self.assertTrue(log)
        self.assertIn('"date":"2026-11-04"', log[0]["details"].replace(" ", ""))

    def test_mirror_adopted(self):
        self.db.save_transfer_pair({"date": "2026-05-01", "account_id": self.sav,
                                    "amount": "-75"}, self.chk, keep_sign=True)
        res = self.db.import_transactions(self._rows_with_matches([self.row("2026-05-02", 75)]))
        self.assertEqual(res["adopted"], 1)
        self.assertEqual(len(self.db.get_transactions(account_id=self.chk)), 1)

    def test_unmatched_row_is_plain_new_even_if_a_candidate_exists(self):
        self.add(self.card, "2026-11-04", 400)
        res = self.db.import_transactions([self.row("2026-11-04", -400)])
        self.assertEqual((res["new"], res["linked"]), (1, 0))
        self.assertEqual(res["saved"][0]["is_transfer"], "0")

    def test_rule_transfer_without_match_makes_new_mirror(self):
        res = self.db.import_transactions([self.row("2026-11-04", -50, _transfer_to=self.sav)])
        self.assertEqual(res["rule_transfers"], 1)
        self.assertEqual([float(t["amount"]) for t in
                          self.db.get_transactions(account_id=self.sav)], [50])

    def test_import_is_all_or_nothing(self):
        good = self.row("2026-11-01", -10, "OK")
        bad = self.row("2026-11-02", -20, "BAD", _match={"kind": "manual", "id": "NOPE"})
        with self.assertRaises(ValueError):
            self.db.import_transactions([good, bad])
        self.assertEqual(self.db.get_transactions(account_id=self.chk), [])

    def test_stale_match_refused(self):
        other = self.add(self.card, "2026-11-04", 400)
        rows = self._rows_with_matches([self.row("2026-11-04", -400)])
        self.db.save_transaction(dict(self.db.get_transaction(other["id"]), reconciled="1"))
        with self.assertRaises(ValueError):
            self.db.import_transactions(rows)
        self.assertEqual(self.db.get_transactions(account_id=self.chk), [])

    def test_same_candidate_twice_refused(self):
        other = self.add(self.card, "2026-11-04", 400)
        m = {"kind": "transfer", "id": other["id"]}
        with self.assertRaises(ValueError):
            self.db.import_transactions([self.row("2026-11-04", -400, _match=m),
                                         self.row("2026-11-04", -400, _match=dict(m))])
        self.assertEqual(self.db.get_transactions(account_id=self.chk), [])


if __name__ == "__main__":
    unittest.main()
