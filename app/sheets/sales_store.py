"""
Contacts (vendors/customers), invoices (AR) and bills (AP) — mixed into
sheets.client.Database.

Cash basis: invoices and bills are tracked documents. Only *payments* create
transactions — an invoice payment is income in the bank account (split by the
invoice's income categories) with any sales tax moved to the Sales Tax Payable
liability; a bill payment is an expense split by the bill's categories.
"""
from datetime import date

SALES_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    kind                TEXT DEFAULT 'vendor',
    company             TEXT DEFAULT '',
    email               TEXT DEFAULT '',
    phone               TEXT DEFAULT '',
    address             TEXT DEFAULT '',
    tax_id              TEXT DEFAULT '',
    default_category_id TEXT DEFAULT '',
    terms_days          INTEGER DEFAULT 30,
    match_text          TEXT DEFAULT '',
    is_1099             INTEGER DEFAULT 0,
    notes               TEXT DEFAULT '',
    active              INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS invoices (
    id          TEXT PRIMARY KEY,
    number      INTEGER DEFAULT 0,
    contact_id  TEXT DEFAULT '',
    issue_date  TEXT DEFAULT '',
    due_date    TEXT DEFAULT '',
    status      TEXT DEFAULT 'draft',
    terms       TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    class_id    TEXT DEFAULT '',
    created_at  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS invoice_lines (
    id          TEXT PRIMARY KEY,
    invoice_id  TEXT NOT NULL,
    line_no     INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    qty         REAL DEFAULT 1,
    unit_price  REAL DEFAULT 0,
    tax_rate    REAL DEFAULT 0,
    category_id TEXT DEFAULT '',
    class_id    TEXT DEFAULT '',
    item_id     TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bills (
    id          TEXT PRIMARY KEY,
    contact_id  TEXT DEFAULT '',
    reference   TEXT DEFAULT '',
    bill_date   TEXT DEFAULT '',
    due_date    TEXT DEFAULT '',
    status      TEXT DEFAULT 'open',
    notes       TEXT DEFAULT '',
    created_at  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bill_lines (
    id          TEXT PRIMARY KEY,
    bill_id     TEXT NOT NULL,
    line_no     INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    qty         REAL DEFAULT 1,
    unit_price  REAL DEFAULT 0,
    category_id TEXT DEFAULT '',
    class_id    TEXT DEFAULT '',
    item_id     TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS doc_payments (
    id             TEXT PRIMARY KEY,
    doc_type       TEXT NOT NULL,
    doc_id         TEXT NOT NULL,
    date           TEXT DEFAULT '',
    amount         REAL DEFAULT 0,
    account_id     TEXT DEFAULT '',
    txn_ids        TEXT DEFAULT '',
    created_at     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_invline ON invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS idx_billline ON bill_lines(bill_id);
CREATE INDEX IF NOT EXISTS idx_docpay ON doc_payments(doc_type, doc_id);
"""

_CONTACT_COLS = ("name", "kind", "company", "email", "phone", "address", "tax_id",
                 "default_category_id", "terms_days", "match_text", "is_1099", "notes", "active")
_LINE_COLS = {"invoice": ("description", "qty", "unit_price", "tax_rate", "category_id",
                          "class_id", "item_id"),
              "bill": ("description", "qty", "unit_price", "category_id", "class_id", "item_id")}


def _d(r):
    return dict(r) if r is not None else None


class SalesMixin:
    # ── contacts ─────────────────────────────────────────────────────────────

    def get_contacts(self, kind: str = "", active_only: bool = True) -> list[dict]:
        q = "SELECT * FROM contacts WHERE 1=1"
        p: list = []
        if active_only:
            q += " AND active=1"
        if kind in ("vendor", "customer"):
            q += " AND kind IN (?, 'both')"
            p.append(kind)
        q += " ORDER BY name COLLATE NOCASE"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    def get_contact(self, cid: str) -> dict | None:
        if not cid:
            return None
        with self._conn() as c:
            return _d(c.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone())

    def save_contact(self, data: dict) -> dict:
        vals = []
        for k in _CONTACT_COLS:
            v = data.get(k, "" if k not in ("terms_days", "is_1099", "active") else None)
            if k == "terms_days":
                v = int(float(v if v not in (None, "") else 30))
            elif k in ("is_1099", "active"):
                v = 1 if str(v if v is not None else (1 if k == "active" else 0)) in ("1", "True") else 0
            vals.append(v if v is not None else "")
        with self._conn() as c:
            if data.get("id") and self.get_contact(data["id"]):
                c.execute(f"UPDATE contacts SET {','.join(k + '=?' for k in _CONTACT_COLS)} WHERE id=?",
                          vals + [data["id"]])
            else:
                data["id"] = self._new_id()
                c.execute(f"INSERT INTO contacts (id,{','.join(_CONTACT_COLS)},created_at) VALUES "
                          f"(?,{','.join('?' * len(_CONTACT_COLS))},?)",
                          [data["id"]] + vals + [self._now()])
        return data

    def match_contact(self, payee: str, kind: str = "") -> dict | None:
        """The contact whose match text (or name) appears in a payee string."""
        text = (payee or "").lower()
        if not text:
            return None
        best = None
        for c in self.get_contacts(kind):
            for needle in (c.get("match_text"), c.get("name")):
                n = (needle or "").strip().lower()
                if len(n) >= 3 and n in text and (best is None or len(n) > best[0]):
                    best = (len(n), c)
        return best[1] if best else None

    def apply_contacts(self, txns: list[dict]) -> list[dict]:
        """Fill contact_id (and the contact's default category) from payee text."""
        if not txns:
            return txns
        with self._conn() as c:
            if not c.execute("SELECT 1 FROM contacts LIMIT 1").fetchone():
                return txns
        for t in txns:
            if t.get("contact_id"):
                continue
            ct = self.match_contact(t.get("payee") or t.get("memo") or "")
            if ct:
                t["contact_id"] = ct["id"]
                if not t.get("category_id") and ct.get("default_category_id"):
                    t["category_id"] = ct["default_category_id"]
        return txns

    # ── payees (v1.19, personal and business) ────────────────────────────────
    # A payee is a contact: the same table and transactions.contact_id in both
    # book types. Personal books show them on the Payees page, business books
    # on Contacts → Payees.

    def payee_summary(self, include_inactive: bool = False) -> list[dict]:
        """Contacts with their activity: "n_txns" (a split counts once),
        "spent" (money out, positive), "received" (money in), "last_used"
        (latest date). Transfers between the user's own accounts don't count."""
        with self._conn() as c:
            stats = {r[0]: r for r in c.execute(
                "SELECT contact_id, COUNT(DISTINCT CASE WHEN COALESCE(split_group_id,'')='' "
                "THEN id ELSE split_group_id END), "
                "COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0), MAX(date) "
                "FROM transactions WHERE COALESCE(contact_id,'')!='' AND is_transfer=0 "
                "GROUP BY contact_id").fetchall()}
        out = []
        for ct in self.get_contacts(active_only=not include_inactive):
            r = stats.get(ct["id"])
            ct = dict(ct)
            ct["n_txns"] = int(r[1]) if r else 0
            ct["spent"] = round(float(r[2]), 2) if r else 0.0
            ct["received"] = round(float(r[3]), 2) if r else 0.0
            ct["last_used"] = (r[4] or "") if r else ""
            out.append(ct)
        return out

    @staticmethod
    def normalize_payee(text: str) -> str:
        """Matching key for a bank payee text: case, spacing, punctuation and
        store / reference numbers ignored — "WALMART #1234", "Walmart  Store
        5678" and "walmart" all give "walmart"."""
        import re
        t = (text or "").lower()
        t = re.sub(r"#\s*\d+", " ", t)
        t = re.sub(r"\b(store|str|sto|no|nbr|num)\.?\s*\d+\b", " ", t)
        t = re.sub(r"\b[a-z]*\d[\w]*\b", " ", t)      # numbers and codes like t0123, 800-555
        t = re.sub(r"[^a-z&' ]+", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    @classmethod
    def payee_display_name(cls, text: str) -> str:
        key = cls.normalize_payee(text)
        return " ".join(w if w in ("&",) else w[:1].upper() + w[1:] for w in key.split())

    def unlinked_payee_names(self) -> list[dict]:
        """Payee texts on transactions with no payee linked, grouped by their
        normalized key: [{"key", "name" (suggested), "variants" (the texts, most
        used first), "count" (transactions; a split once), "ids" (every row
        id, all split lines included)}], most used first. Transfers between
        the user's own accounts are left out."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, payee, COALESCE(split_group_id,'') FROM transactions "
                "WHERE COALESCE(contact_id,'')='' AND COALESCE(payee,'')!='' "
                "AND is_transfer=0").fetchall()
        groups: dict = {}
        for rid, payee, gid in rows:
            key = self.normalize_payee(payee)
            if not key:
                continue
            g = groups.setdefault(key, {"key": key, "variants": {}, "units": set(), "ids": []})
            g["variants"][payee] = g["variants"].get(payee, 0) + 1
            g["units"].add(gid or rid)
            g["ids"].append(rid)
        out = []
        for g in groups.values():
            out.append({"key": g["key"], "name": self.payee_display_name(g["key"]),
                        "variants": sorted(g["variants"], key=lambda v: -g["variants"][v]),
                        "count": len(g["units"]), "ids": g["ids"]})
        out.sort(key=lambda g: (-g["count"], g["key"]))
        return out

    def transactions_for_payee_key(self, key: str) -> list[dict]:
        """Unlinked transactions whose payee normalizes to key (the preview
        before "Create payee & link")."""
        ids = next((g["ids"] for g in self.unlinked_payee_names() if g["key"] == key), [])
        if not ids:
            return []
        with self._conn() as c:
            rows = []
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                rows += [dict(r) for r in c.execute(
                    f"SELECT * FROM transactions WHERE id IN ({','.join('?' * len(chunk))}) "
                    "ORDER BY date", chunk).fetchall()]
        return rows

    def link_payee(self, contact_id: str, txn_ids) -> int:
        """Link transactions to a payee (a split's every line). Returns how many
        transactions (a split once) were linked."""
        ids = list(dict.fromkeys(txn_ids or ()))
        if not ids or not contact_id:
            return 0
        units = set()
        with self._conn() as c:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = f"({','.join('?' * len(chunk))})"
                for r in c.execute(f"SELECT id, COALESCE(split_group_id,'') FROM transactions "
                                   f"WHERE id IN {q}", chunk).fetchall():
                    units.add(r[1] or r[0])
                c.execute(f"UPDATE transactions SET contact_id=? WHERE id IN {q}",
                          [contact_id] + chunk)
                c.execute(f"UPDATE transactions SET contact_id=? WHERE COALESCE(split_group_id,'')"
                          f"!='' AND split_group_id IN (SELECT split_group_id FROM transactions "
                          f"WHERE id IN {q})", [contact_id] + chunk)
        return len(units)

    def create_payee_and_link(self, name: str, key: str) -> tuple[dict, int]:
        """Create a payee named name (matching bank texts that normalize to key
        on future imports) and link the unlinked transactions with that key.
        Returns (payee, number of transactions linked)."""
        txns = self.transactions_for_payee_key(key)
        out_money = sum(float(t.get("amount") or 0) for t in txns)
        payee = self.save_contact({"name": name.strip() or self.payee_display_name(key),
                                   "kind": "customer" if out_money > 0 else "vendor",
                                   "match_text": key if len(key) >= 3 else "",
                                   "terms_days": 30, "active": "1"})
        return payee, self.link_payee(payee["id"], [t["id"] for t in txns])

    def add_payee(self, name: str, kind: str = "vendor") -> dict:
        """A new payee named name (or the existing active one with that name)."""
        name = (name or "").strip()
        existing = self.find_payee_by_name(name)
        if existing:
            return existing
        return self.save_contact({"name": name, "kind": kind, "terms_days": 30,
                                  "active": "1"})

    def find_payee_by_name(self, name: str) -> dict | None:
        n = (name or "").strip().lower()
        if not n:
            return None
        return next((c for c in self.get_contacts() if c["name"].strip().lower() == n), None)

    def merge_payees(self, keep_id: str, other_ids) -> int:
        """Merge payees into keep_id: their transactions, invoices, bills,
        inventory vendors and people move to it; they become inactive.
        Returns the number of transactions moved."""
        others = [o for o in dict.fromkeys(other_ids or ()) if o and o != keep_id]
        if not others or not self.get_contact(keep_id):
            return 0
        q = f"({','.join('?' * len(others))})"
        with self.atomic():
            with self._conn() as c:
                moved = c.execute(f"SELECT COUNT(*) FROM transactions WHERE contact_id IN {q}",
                                  others).fetchone()[0]
                c.execute(f"UPDATE transactions SET contact_id=? WHERE contact_id IN {q}",
                          [keep_id] + others)
                for table, col in (("invoices", "contact_id"), ("bills", "contact_id"),
                                   ("items", "default_vendor_id"), ("people", "contact_id")):
                    try:
                        c.execute(f"UPDATE {table} SET {col}=? WHERE {col} IN {q}",
                                  [keep_id] + others)
                    except Exception:
                        pass        # a table this book doesn't have
                for o in others:
                    c.execute("UPDATE contacts SET active=0 WHERE id=?", (o,))
        return int(moved or 0)

    def last_payee_transaction(self, contact_id: str = "", payee: str = "",
                               exclude_id: str = "") -> dict | None:
        """The payee's most recent transaction as a split header (with
        "lines"), by contact, else by payee text (case-insensitive) — what a
        new transaction for that payee recalls (P2)."""
        with self._conn() as c:
            if contact_id:
                r = c.execute("SELECT id FROM transactions WHERE contact_id=? AND id!=? "
                              "ORDER BY date DESC, created_at DESC, rowid DESC LIMIT 1",
                              (contact_id, exclude_id or "")).fetchone()
            else:
                r = None
            if r is None and (payee or "").strip():
                r = c.execute("SELECT id FROM transactions WHERE LOWER(TRIM(payee))=? AND id!=? "
                              "ORDER BY date DESC, created_at DESC, rowid DESC LIMIT 1",
                              (payee.strip().lower(), exclude_id or "")).fetchone()
        if r is None:
            return None
        full = self.get_transaction_with_lines(r[0])
        return full["header"] if full else None

    def contact_activity(self, cid: str, start: str = "", end: str = "") -> list[dict]:
        q = "SELECT * FROM transactions WHERE contact_id=?"
        p: list = [cid]
        if start:
            q += " AND date>=?"; p.append(start)
        if end:
            q += " AND date<=?"; p.append(end)
        q += " ORDER BY date"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    # ── documents (invoices / bills) ─────────────────────────────────────────

    def next_invoice_number(self) -> int:
        with self._conn() as c:
            mx = c.execute("SELECT COALESCE(MAX(number),0) FROM invoices").fetchone()[0] or 0
        start = int(self.get_setting("invoice_start_number", "1001") or 1001)
        return max(int(mx) + 1, start)

    def get_invoices(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM invoices ORDER BY number DESC").fetchall()]

    def get_bills(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM bills ORDER BY due_date DESC, created_at DESC").fetchall()]

    def get_doc(self, doc_type: str, did: str) -> dict | None:
        table = "invoices" if doc_type == "invoice" else "bills"
        with self._conn() as c:
            return _d(c.execute(f"SELECT * FROM {table} WHERE id=?", (did,)).fetchone())

    def get_doc_lines(self, doc_type: str, did: str) -> list[dict]:
        table, fk = ("invoice_lines", "invoice_id") if doc_type == "invoice" else ("bill_lines", "bill_id")
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                f"SELECT * FROM {table} WHERE {fk}=? ORDER BY line_no", (did,)).fetchall()]

    def save_doc(self, doc_type: str, data: dict, lines: list[dict]) -> dict:
        if doc_type == "invoice":
            table, cols = "invoices", ("number", "contact_id", "issue_date", "due_date", "status",
                                       "terms", "notes", "class_id")
            if not data.get("number"):
                data["number"] = self.next_invoice_number()
        else:
            table, cols = "bills", ("contact_id", "reference", "bill_date", "due_date", "status",
                                    "notes")
        vals = [data.get(k) or ("" if k != "number" else 0) for k in cols]
        with self._conn() as c:
            if data.get("id") and self.get_doc(doc_type, data["id"]):
                c.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in cols)} WHERE id=?",
                          vals + [data["id"]])
            else:
                data["id"] = self._new_id()
                c.execute(f"INSERT INTO {table} (id,{','.join(cols)},created_at) VALUES "
                          f"(?,{','.join('?' * len(cols))},?)", [data["id"]] + vals + [self._now()])
            ltable, fk = ("invoice_lines", "invoice_id") if doc_type == "invoice" else ("bill_lines", "bill_id")
            lcols = _LINE_COLS[doc_type]
            c.execute(f"DELETE FROM {ltable} WHERE {fk}=?", (data["id"],))
            for n, ln in enumerate(lines):
                lv = [float(ln.get(k) or (1 if k == "qty" else 0)) if k in ("qty", "unit_price", "tax_rate")
                      else (ln.get(k) or "") for k in lcols]
                c.execute(f"INSERT INTO {ltable} (id,{fk},line_no,{','.join(lcols)}) VALUES "
                          f"(?,?,?,{','.join('?' * len(lcols))})",
                          [self._new_id(), data["id"], n] + lv)
        return data

    def delete_doc(self, doc_type: str, did: str):
        """Delete a document and undo its payments' transactions."""
        for p in self.get_doc_payments(doc_type, did):
            self.delete_doc_payment(p["id"])
        table, ltable, fk = (("invoices", "invoice_lines", "invoice_id") if doc_type == "invoice"
                             else ("bills", "bill_lines", "bill_id"))
        with self._conn() as c:
            c.execute(f"DELETE FROM {ltable} WHERE {fk}=?", (did,))
            c.execute(f"DELETE FROM {table} WHERE id=?", (did,))

    def get_doc_payments(self, doc_type: str, did: str = "") -> list[dict]:
        with self._conn() as c:
            if did:
                rows = c.execute("SELECT * FROM doc_payments WHERE doc_type=? AND doc_id=? "
                                 "ORDER BY date", (doc_type, did)).fetchall()
            else:
                rows = c.execute("SELECT * FROM doc_payments WHERE doc_type=? ORDER BY date",
                                 (doc_type,)).fetchall()
        return [dict(r) for r in rows]

    def doc_summary(self, doc_type: str, doc: dict, as_of: str = "") -> dict:
        """Totals, amount paid, balance and live status for one document."""
        from utils.invoicing import doc_totals, status_for
        from utils.money import money_sum, cents
        as_of = as_of or date.today().isoformat()
        lines = self.get_doc_lines(doc_type, doc["id"])
        if doc_type == "bill":
            lines = [dict(l, tax_rate=0) for l in lines]
        t = doc_totals(lines)
        paid = money_sum(p["amount"] for p in self.get_doc_payments(doc_type, doc["id"])
                         if p["date"] <= as_of)
        return dict(t, paid=paid, balance=cents(t["total"] - paid),
                    status=status_for(doc, t["total"], paid, as_of), lines=lines)

    def record_doc_payment(self, doc_type: str, did: str, pay_date: str, account_id: str,
                           amount) -> dict:
        """
        Record money received for an invoice (or paid for a bill) and create the
        matching transactions. Returns the payment row.
        """
        from utils.invoicing import payment_split
        from utils.money import D, cents
        doc = self.get_doc(doc_type, did)
        if not doc:
            raise ValueError("Document not found")
        s = self.doc_summary(doc_type, doc, "9999-12-31")
        amount = cents(amount)
        if amount <= 0:
            raise ValueError("Enter an amount greater than zero.")
        contact = self.get_contact(doc.get("contact_id", "")) or {}
        label = (f"Invoice #{doc.get('number')}" if doc_type == "invoice"
                 else f"Bill {doc.get('reference') or ''}".strip())
        parts = payment_split(s["lines"], amount, s["total"])
        base = {"date": pay_date, "account_id": account_id, "payee": contact.get("name", ""),
                "memo": label, "contact_id": doc.get("contact_id", "")}
        txn_ids = []
        main = [p for p in parts if not p["is_tax"]]
        tax = sum((p["amount"] for p in parts if p["is_tax"]), D(0))
        if main:
            lines = [{"category_id": p["category_id"], "class_id": p["class_id"] or doc.get("class_id", ""),
                      "amount": str(p["amount"]), "memo": f"{label}: {p['memo']}"[:120]} for p in main]
            if len(lines) == 1:
                sign = 1 if doc_type == "invoice" else -1
                t = self.save_transaction(dict(base, category_id=lines[0]["category_id"],
                                               class_id=lines[0]["class_id"],
                                               memo=lines[0]["memo"],
                                               amount=str(sign * float(lines[0]["amount"]))))
                txn_ids.append(t["id"])
            else:
                for t in self.split_transaction(dict(base), lines):
                    txn_ids.append(t["id"])
                if doc_type == "invoice":
                    self._force_sign(txn_ids, +1)
                else:
                    self._force_sign(txn_ids, -1)
        if tax:
            stp = self.get_or_create_role_account("sales_tax_payable", "Sales Tax Payable",
                                                  "liability")
            src = self.save_transfer_pair({"date": pay_date, "account_id": stp["id"],
                                           "payee": contact.get("name", ""),
                                           "memo": f"{label}: sales tax collected",
                                           "amount": str(tax),
                                           "contact_id": doc.get("contact_id", "")}, account_id)
            txn_ids.append(src["id"])
        pid = self._new_id()
        with self._conn() as c:
            c.execute("INSERT INTO doc_payments (id,doc_type,doc_id,date,amount,account_id,txn_ids,"
                      "created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (pid, doc_type, did, pay_date, float(amount), account_id,
                       ",".join(txn_ids), self._now()))
        if doc.get("status") == "draft":
            doc["status"] = "sent" if doc_type == "invoice" else "open"
            self.save_doc(doc_type, doc, self.get_doc_lines(doc_type, did))
        return {"id": pid, "txn_ids": txn_ids}

    def _force_sign(self, txn_ids, sign):
        """Invoice payments are money in even if a line uses an uncategorized/odd category."""
        with self._conn() as c:
            for tid in txn_ids:
                c.execute("UPDATE transactions SET amount=? * ABS(amount) WHERE id=?", (sign, tid))

    def delete_doc_payment(self, pid: str):
        with self._conn() as c:
            row = c.execute("SELECT * FROM doc_payments WHERE id=?", (pid,)).fetchone()
        if not row:
            return
        for tid in (row["txn_ids"] or "").split(","):
            t = self.get_transaction(tid) if tid else None
            if t:
                self.delete_transaction_full(t)
        with self._conn() as c:
            c.execute("DELETE FROM doc_payments WHERE id=?", (pid,))

    def open_documents(self, doc_type: str, as_of: str) -> list[dict]:
        """Open (unpaid, not draft/void) documents with balances, for aging."""
        out = []
        docs = self.get_invoices() if doc_type == "invoice" else self.get_bills()
        contacts = {c["id"]: c for c in self.get_contacts(active_only=False)}
        for d in docs:
            if d.get("status") in ("draft", "void"):
                continue
            issued = d.get("issue_date") if doc_type == "invoice" else d.get("bill_date")
            if issued and issued > as_of:
                continue
            s = self.doc_summary(doc_type, d, as_of)
            if s["balance"] > 0:
                out.append(dict(d, balance=s["balance"], total=s["total"], live_status=s["status"],
                                contact=contacts.get(d.get("contact_id"), {}).get("name", "")))
        return out

    def ar_ap_totals(self, as_of: str = "") -> dict:
        from utils.money import money_sum
        as_of = as_of or date.today().isoformat()
        ar = self.open_documents("invoice", as_of)
        ap = self.open_documents("bill", as_of)
        return {"ar": money_sum(d["balance"] for d in ar),
                "ap": money_sum(d["balance"] for d in ap),
                "ar_overdue": sum(1 for d in ar if d.get("due_date") and d["due_date"] < as_of),
                "ap_overdue": sum(1 for d in ap if d.get("due_date") and d["due_date"] < as_of),
                "ar_overdue_amt": money_sum(d["balance"] for d in ar
                                            if d.get("due_date") and d["due_date"] < as_of),
                "ap_overdue_amt": money_sum(d["balance"] for d in ap
                                            if d.get("due_date") and d["due_date"] < as_of)}
