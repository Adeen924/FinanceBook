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
