"""
Recurring transactions/bills, the audit trail and the period lock — mixed into
sheets.client.Database.

Audit trail: SQLite triggers write every insert/update/delete on the main
tables to audit_log with the row's before/after values and the name in
app_session (set from the app at startup — the Windows user, or the name you
enter in Settings). Triggers are rebuilt on every open so new columns are
always captured.

Period lock: triggers refuse to add, change or delete a transaction dated on
or before settings.lock_date ("this period is locked").
"""
import json
import sqlite3

ADMIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS recurring (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT DEFAULT 'transaction',
    template     TEXT DEFAULT '{}',
    frequency    TEXT DEFAULT 'monthly',
    next_date    TEXT DEFAULT '',
    anchor_day   INTEGER DEFAULT 0,
    end_date     TEXT DEFAULT '',
    auto_post    INTEGER DEFAULT 0,
    remind_days  INTEGER DEFAULT 7,
    active       INTEGER DEFAULT 1,
    last_posted  TEXT DEFAULT '',
    created_at   TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT DEFAULT '',
    user       TEXT DEFAULT '',
    table_name TEXT DEFAULT '',
    op         TEXT DEFAULT '',
    row_id     TEXT DEFAULT '',
    details    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE TABLE IF NOT EXISTS app_session (
    k    INTEGER PRIMARY KEY CHECK (k = 1),
    user TEXT DEFAULT ''
);
"""

AUDITED_TABLES = ("transactions", "accounts", "categories", "people", "contacts", "invoices",
                  "bills", "doc_payments", "assets", "items", "stock_moves", "payroll_runs",
                  "receipts", "rules", "loans", "mileage")
# Bookkeeping columns whose changes alone shouldn't create an audit entry.
_QUIET = {"updated_at", "updated_by", "created_by"}


class AdminMixin:
    # ── session user / triggers ──────────────────────────────────────────────

    def set_session_user(self, name: str):
        self.session_user = (name or "").strip() or "unknown"
        if self.read_only:
            return
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO app_session (k, user) VALUES (1, ?)",
                      (self.session_user,))

    def _install_triggers(self):
        with self._conn() as c:
            try:
                c.execute("SELECT json_object('a', 1)").fetchone()
                has_json = True
            except sqlite3.OperationalError:
                has_json = False
            existing = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'")}
            for name in existing:
                if name.startswith(("audit_", "lock_")):
                    c.execute(f"DROP TRIGGER IF EXISTS {name}")
            user = "(SELECT user FROM app_session WHERE k=1)"
            for t in AUDITED_TABLES if has_json else ():
                cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
                if not cols or "id" not in cols:
                    continue

                def obj(prefix):
                    return "json_object(" + ",".join(f"'{col}',{prefix}.{col}" for col in cols) + ")"
                changed = " OR ".join(f"OLD.{col} IS NOT NEW.{col}" for col in cols if col not in _QUIET)
                ins = (f"INSERT INTO audit_log (ts,user,table_name,op,row_id,details) VALUES "
                       f"(datetime('now','localtime'),{user},'{t}',")
                c.execute(f"CREATE TRIGGER audit_{t}_ins AFTER INSERT ON {t} BEGIN "
                          f"{ins}'insert',NEW.id,json_object('new',{obj('NEW')})); END")
                c.execute(f"CREATE TRIGGER audit_{t}_upd AFTER UPDATE ON {t} WHEN {changed} BEGIN "
                          f"{ins}'update',NEW.id,json_object('old',{obj('OLD')},'new',{obj('NEW')})); END")
                c.execute(f"CREATE TRIGGER audit_{t}_del AFTER DELETE ON {t} BEGIN "
                          f"{ins}'delete',OLD.id,json_object('old',{obj('OLD')})); END")
            lock = "(SELECT value FROM settings WHERE key='lock_date' AND value!='')"
            msg = "SELECT RAISE(ABORT, 'FinanceBook: this period is locked')"
            c.execute(f"CREATE TRIGGER lock_txn_ins BEFORE INSERT ON transactions "
                      f"WHEN NEW.date <= {lock} BEGIN {msg}; END")
            c.execute(f"CREATE TRIGGER lock_txn_upd BEFORE UPDATE ON transactions "
                      f"WHEN (OLD.date <= {lock} OR NEW.date <= {lock}) AND "
                      f"({' OR '.join(f'OLD.{x} IS NOT NEW.{x}' for x in ('date', 'account_id', 'amount', 'category_id', 'payee', 'memo', 'is_transfer', 'class_id', 'split_group_id'))}) "
                      f"BEGIN {msg}; END")
            c.execute(f"CREATE TRIGGER lock_txn_del BEFORE DELETE ON transactions "
                      f"WHEN OLD.date <= {lock} BEGIN {msg}; END")

    def get_audit_log(self, table: str = "", search: str = "", limit: int = 2000) -> list[dict]:
        q = "SELECT * FROM audit_log WHERE 1=1"
        p: list = []
        if table:
            q += " AND table_name=?"; p.append(table)
        if search:
            q += " AND (details LIKE ? OR user LIKE ? OR row_id LIKE ?)"
            p += [f"%{search}%"] * 3
        q += " ORDER BY id DESC LIMIT ?"
        p.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    # ── period lock ──────────────────────────────────────────────────────────

    def lock_date(self) -> str:
        return self.get_setting("lock_date", "")

    def set_lock_date(self, iso: str):
        self.set_setting("lock_date", iso or "")

    # ── recurring ────────────────────────────────────────────────────────────

    def get_recurring(self, rid: str = ""):
        with self._conn() as c:
            if rid:
                r = c.execute("SELECT * FROM recurring WHERE id=?", (rid,)).fetchone()
                return _rec(r) if r else None
            return [_rec(r) for r in c.execute(
                "SELECT * FROM recurring ORDER BY active DESC, next_date").fetchall()]

    def save_recurring(self, data: dict) -> dict:
        tmpl = data.get("template") or {}
        if not isinstance(tmpl, str):
            tmpl = json.dumps(tmpl)
        nd = data.get("next_date") or ""
        anchor = int(data.get("anchor_day") or (int(nd[8:10]) if len(nd) >= 10 else 0))
        cols = ("name", "kind", "template", "frequency", "next_date", "anchor_day", "end_date",
                "auto_post", "remind_days", "active")
        vals = [data.get("name", ""), data.get("kind") or "transaction", tmpl,
                data.get("frequency") or "monthly", nd, anchor, data.get("end_date") or "",
                1 if str(data.get("auto_post", "0")) in ("1", "True") else 0,
                int(data.get("remind_days") or 7),
                0 if str(data.get("active", "1")) in ("0", "False") else 1]
        with self._conn() as c:
            if data.get("id") and self.get_recurring(data["id"]):
                c.execute(f"UPDATE recurring SET {','.join(k + '=?' for k in cols)} WHERE id=?",
                          vals + [data["id"]])
            else:
                data["id"] = self._new_id()
                c.execute(f"INSERT INTO recurring (id,{','.join(cols)},created_at) VALUES "
                          f"(?,{','.join('?' * len(cols))},?)", [data["id"]] + vals + [self._now()])
        return data

    def delete_recurring(self, rid: str):
        with self._conn() as c:
            c.execute("DELETE FROM recurring WHERE id=?", (rid,))

    def post_recurring(self, rec: dict, on: str = "") -> dict:
        """Create this occurrence (transaction or bill) and advance the schedule."""
        from utils.recurring import next_occurrence
        on = on or rec["next_date"]
        t = rec["template"]
        amount = abs(float(t.get("amount") or 0))
        if rec.get("kind") == "bill":
            from utils.invoicing import due_date_for
            contact = self.get_contact(t.get("contact_id", "")) or {}
            made = self.save_doc("bill", {"contact_id": t.get("contact_id", ""),
                                          "reference": rec["name"], "bill_date": on,
                                          "due_date": due_date_for(on, contact.get("terms_days") or 0)
                                          if contact else on, "status": "open"},
                                 [{"description": t.get("memo") or rec["name"], "qty": 1,
                                   "unit_price": amount, "category_id": t.get("category_id", "")}])
        else:
            ctype = self._category_type(t.get("category_id", ""))
            made = self.save_transaction({
                "date": on, "account_id": t.get("account_id", ""), "payee": t.get("payee") or rec["name"],
                "memo": t.get("memo") or f"Recurring: {rec['name']}",
                "amount": str(amount if ctype == "income" else -amount),
                "category_id": t.get("category_id", ""), "class_id": t.get("class_id", ""),
                "contact_id": t.get("contact_id", "")})
        nxt = next_occurrence(on, rec["frequency"], rec.get("anchor_day") or None)
        active = 1 if not rec.get("end_date") or nxt <= rec["end_date"] else 0
        with self._conn() as c:
            c.execute("UPDATE recurring SET next_date=?, last_posted=?, active=? WHERE id=?",
                      (nxt, on, active, rec["id"]))
        return made

    def skip_recurring(self, rec: dict):
        from utils.recurring import next_occurrence
        with self._conn() as c:
            c.execute("UPDATE recurring SET next_date=? WHERE id=?",
                      (next_occurrence(rec["next_date"], rec["frequency"], rec.get("anchor_day") or None),
                       rec["id"]))

    def process_recurring(self, today: str) -> list[dict]:
        """Post every auto-post occurrence due on or before today. Returns what was made.
        Nothing is posted read-only — the computer editing the database posts them."""
        from utils.recurring import due_occurrences
        made = []
        if self.read_only:
            return made
        for rec in self.get_recurring():
            if not rec["active"] or not rec["auto_post"]:
                continue
            for d in due_occurrences(rec["next_date"], today, rec["frequency"], rec.get("end_date", ""),
                                     rec.get("anchor_day") or None):
                try:
                    made.append(self.post_recurring(rec, d))
                except sqlite3.DatabaseError:
                    break           # e.g. period locked — leave it due
                rec = self.get_recurring(rec["id"])
        return made

    def upcoming_recurring(self, today: str, days: int | None = None) -> list[dict]:
        """Active items due by today + their reminder window (or `days`)."""
        from datetime import date, timedelta
        out = []
        for rec in self.get_recurring():
            if not rec["active"] or not rec["next_date"]:
                continue
            window = days if days is not None else rec.get("remind_days") or 7
            horizon = (date.fromisoformat(today) + timedelta(days=window)).isoformat()
            if rec["next_date"] <= horizon:
                out.append(dict(rec, overdue=rec["next_date"] < today))
        return out


def _rec(r) -> dict:
    d = dict(r)
    try:
        d["template"] = json.loads(d.get("template") or "{}")
    except ValueError:
        d["template"] = {}
    d["auto_post"] = int(d.get("auto_post") or 0)
    d["active"] = int(d.get("active") if d.get("active") is not None else 1)
    return d
