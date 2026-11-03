"""
Receipts, receipt line items, attachments and vendor memory — mixed into
sheets.client.Database (uses its _conn/_new_id/_now helpers).

Receipt files are copied into a "receipts" folder next to the database file, so
they sync (OneDrive / Google Drive) and back up alongside it. Paths are stored
relative to the database folder.
"""
import hashlib
import os
import re
import shutil
from collections import Counter
from datetime import datetime

RECEIPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    id             TEXT PRIMARY KEY,
    file_path      TEXT DEFAULT '',
    original_name  TEXT DEFAULT '',
    sha256         TEXT DEFAULT '',
    uploaded_at    TEXT DEFAULT '',
    vendor         TEXT DEFAULT '',
    receipt_date   TEXT DEFAULT '',
    currency       TEXT DEFAULT 'USD',
    subtotal       REAL DEFAULT 0,
    tax            REAL DEFAULT 0,
    tip            REAL DEFAULT 0,
    total          REAL DEFAULT 0,
    status         TEXT DEFAULT 'new',
    parse_method   TEXT DEFAULT '',
    raw_text       TEXT DEFAULT '',
    transaction_id TEXT DEFAULT '',
    split_group_id TEXT DEFAULT '',
    notes          TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_receipt_sha ON receipts(sha256);

CREATE TABLE IF NOT EXISTS receipt_items (
    id             TEXT PRIMARY KEY,
    receipt_id     TEXT NOT NULL,
    line_no        INTEGER DEFAULT 0,
    description    TEXT DEFAULT '',
    qty            REAL DEFAULT 1,
    unit_price     REAL DEFAULT 0,
    line_total     REAL DEFAULT 0,
    category_id    TEXT DEFAULT '',
    class_id       TEXT DEFAULT '',
    charged_to     TEXT DEFAULT '',
    is_business    INTEGER DEFAULT 1,
    kind           TEXT DEFAULT 'expense',
    transaction_id TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ritem_receipt ON receipt_items(receipt_id);

CREATE TABLE IF NOT EXISTS attachments (
    id             TEXT PRIMARY KEY,
    receipt_id     TEXT NOT NULL,
    transaction_id TEXT DEFAULT '',
    split_group_id TEXT DEFAULT '',
    created_at     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_attach_txn ON attachments(transaction_id);

CREATE TABLE IF NOT EXISTS vendor_memory (
    vendor_key  TEXT PRIMARY KEY,
    vendor_name TEXT DEFAULT '',
    category_id TEXT DEFAULT '',
    class_id    TEXT DEFAULT '',
    uses        INTEGER DEFAULT 0,
    updated_at  TEXT DEFAULT ''
);
"""

_RECEIPT_MONEY = ("subtotal", "tax", "tip", "total")
_ITEM_MONEY = ("qty", "unit_price", "line_total")


def vendor_key(name: str) -> str:
    """Normalise a vendor/payee for matching: 'TRADER JOE'S #123' → 'trader joes'."""
    s = re.sub(r"[#*]\s*\d+|\d{3,}", " ", (name or "").lower())
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _row(r) -> dict:
    d = dict(r)
    for k in _RECEIPT_MONEY + _ITEM_MONEY:
        if k in d:
            d[k] = float(d[k] or 0)
    if "is_business" in d:
        d["is_business"] = str(int(d["is_business"] or 0))
    return d


class ReceiptsMixin:
    # ── files ────────────────────────────────────────────────────────────────

    def db_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.db_path))

    def receipts_dir(self) -> str:
        return os.path.join(self.db_dir(), "receipts")

    def receipt_abs_path(self, receipt: dict) -> str:
        rel = (receipt or {}).get("file_path") or ""
        return os.path.join(self.db_dir(), rel) if rel else ""

    def add_receipt_file(self, src_path: str) -> tuple[dict, bool]:
        """
        Copy a receipt file next to the database and record it. Returns
        (receipt, is_duplicate); a file whose SHA-256 is already on record is
        not copied again — the existing receipt is returned instead.
        """
        sha = file_sha256(src_path)
        with self._conn() as c:
            row = c.execute("SELECT * FROM receipts WHERE sha256=?", (sha,)).fetchone()
        if row:
            return _row(row), True
        os.makedirs(self.receipts_dir(), exist_ok=True)
        base = os.path.basename(src_path)
        safe = re.sub(r"[^\w.\- ]+", "_", base).strip() or "receipt"
        name = f"{datetime.now():%Y%m%d}-{sha[:10]}-{safe}"
        dest = os.path.join(self.receipts_dir(), name)
        shutil.copy2(src_path, dest)
        rid = self._new_id()
        with self._conn() as c:
            c.execute("INSERT INTO receipts (id,file_path,original_name,sha256,uploaded_at,status) "
                      "VALUES (?,?,?,?,?,'new')",
                      (rid, os.path.join("receipts", name), base, sha, self._now()))
        return self.get_receipt(rid), False

    def add_manual_receipt(self) -> dict:
        """A receipt with no file, for typing in a paper receipt by hand."""
        rid = self._new_id()
        with self._conn() as c:
            c.execute("INSERT INTO receipts (id,uploaded_at,status,parse_method) "
                      "VALUES (?,?,'new','manual')", (rid, self._now()))
        return self.get_receipt(rid)

    # ── receipts ─────────────────────────────────────────────────────────────

    def get_receipts(self, search: str = "") -> list[dict]:
        q = "SELECT * FROM receipts"
        p: list = []
        if search:
            like = f"%{search}%"
            q += (" WHERE vendor LIKE ? OR original_name LIKE ? OR notes LIKE ? OR id IN "
                  "(SELECT receipt_id FROM receipt_items WHERE description LIKE ?)")
            p = [like, like, like, like]
        q += " ORDER BY COALESCE(NULLIF(receipt_date,''), substr(uploaded_at,1,10)) DESC, uploaded_at DESC"
        with self._conn() as c:
            return [_row(r) for r in c.execute(q, p).fetchall()]

    def get_receipt(self, rid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
        return _row(r) if r else None

    def save_receipt(self, data: dict) -> dict:
        cols = ("vendor", "receipt_date", "currency", "subtotal", "tax", "tip", "total",
                "status", "parse_method", "raw_text", "transaction_id", "split_group_id", "notes")
        present = [k for k in cols if k in data]
        if not present:
            return data
        vals = [float(data[k] or 0) if k in _RECEIPT_MONEY else (data[k] or "") for k in present]
        with self._conn() as c:
            c.execute(f"UPDATE receipts SET {','.join(k + '=?' for k in present)} WHERE id=?",
                      vals + [data["id"]])
        return data

    def delete_receipt(self, rid: str, delete_file: bool = True):
        """Remove a receipt record (and its copied file). Booked transactions stay."""
        r = self.get_receipt(rid)
        with self._conn() as c:
            c.execute("DELETE FROM receipt_items WHERE receipt_id=?", (rid,))
            c.execute("DELETE FROM attachments WHERE receipt_id=?", (rid,))
            c.execute("DELETE FROM receipts WHERE id=?", (rid,))
        if r and delete_file:
            path = self.receipt_abs_path(r)
            if path and os.path.isfile(path) and os.path.commonpath(
                    [os.path.abspath(path), self.receipts_dir()]) == self.receipts_dir():
                try:
                    os.remove(path)
                except OSError:
                    pass

    # ── line items ───────────────────────────────────────────────────────────

    def get_receipt_items(self, rid: str) -> list[dict]:
        with self._conn() as c:
            return [_row(r) for r in c.execute(
                "SELECT * FROM receipt_items WHERE receipt_id=? ORDER BY line_no", (rid,)).fetchall()]

    def replace_receipt_items(self, rid: str, items: list[dict]) -> list[dict]:
        out = []
        with self._conn() as c:
            c.execute("DELETE FROM receipt_items WHERE receipt_id=?", (rid,))
            for n, it in enumerate(items):
                iid = self._new_id()
                c.execute(
                    "INSERT INTO receipt_items (id,receipt_id,line_no,description,qty,unit_price,"
                    "line_total,category_id,class_id,charged_to,is_business,kind,transaction_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (iid, rid, n, it.get("description") or "", float(it.get("qty") or 1),
                     float(it.get("unit_price") or 0), float(it.get("line_total") or 0),
                     it.get("category_id") or "", it.get("class_id") or "",
                     it.get("charged_to") or "",
                     0 if str(it.get("business", it.get("is_business", "1"))) in ("0", "False") else 1,
                     it.get("kind") or "expense", it.get("transaction_id") or ""))
                out.append(dict(it, id=iid, receipt_id=rid, line_no=n))
        return out

    def search_receipt_items(self, text: str, start: str = "", end: str = "") -> list[dict]:
        """Line items whose description matches, with their receipt's vendor/date."""
        q = ("SELECT i.*, r.vendor AS vendor, r.receipt_date AS receipt_date "
             "FROM receipt_items i JOIN receipts r ON r.id=i.receipt_id "
             "WHERE i.description LIKE ?")
        p: list = [f"%{text}%"]
        if start:
            q += " AND r.receipt_date>=?"; p.append(start)
        if end:
            q += " AND r.receipt_date<=?"; p.append(end)
        q += " ORDER BY r.receipt_date DESC"
        with self._conn() as c:
            return [_row(r) for r in c.execute(q, p).fetchall()]

    # ── attachments ──────────────────────────────────────────────────────────

    def attach_receipt(self, rid: str, txn: dict):
        """Attach a receipt to a transaction (the whole split, if it is one)."""
        gid = txn.get("split_group_id") or ""
        with self._conn() as c:
            if gid:
                exists = c.execute("SELECT 1 FROM attachments WHERE receipt_id=? AND split_group_id=?",
                                   (rid, gid)).fetchone()
            else:
                exists = c.execute("SELECT 1 FROM attachments WHERE receipt_id=? AND transaction_id=?",
                                   (rid, txn.get("id", ""))).fetchone()
            if not exists:
                c.execute("INSERT INTO attachments (id,receipt_id,transaction_id,split_group_id,"
                          "created_at) VALUES (?,?,?,?,?)",
                          (self._new_id(), rid, txn.get("id", ""), gid, self._now()))
            c.execute("UPDATE receipts SET transaction_id=CASE WHEN transaction_id='' THEN ? "
                      "ELSE transaction_id END, status=CASE WHEN status='new' THEN 'attached' "
                      "ELSE status END WHERE id=?", (txn.get("id", ""), rid))

    def detach_receipt(self, rid: str, txn: dict):
        with self._conn() as c:
            c.execute("DELETE FROM attachments WHERE receipt_id=? AND (transaction_id=? OR "
                      "(split_group_id!='' AND split_group_id=?))",
                      (rid, txn.get("id", ""), txn.get("split_group_id") or "-"))

    def get_transaction_receipts(self, txn: dict) -> list[dict]:
        gid = txn.get("split_group_id") or "-"
        with self._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT r.* FROM receipts r JOIN attachments a ON a.receipt_id=r.id "
                "WHERE a.transaction_id=? OR (a.split_group_id!='' AND a.split_group_id=?)",
                (txn.get("id", ""), gid)).fetchall()
        return [_row(r) for r in rows]

    def attachment_index(self) -> tuple[set, set]:
        """(transaction ids, split group ids) that have at least one attachment."""
        with self._conn() as c:
            rows = c.execute("SELECT transaction_id, split_group_id FROM attachments").fetchall()
        return ({r[0] for r in rows if r[0]}, {r[1] for r in rows if r[1]})

    # ── suggestions & vendor memory ──────────────────────────────────────────

    def remember_vendor(self, vendor: str, category_id: str, class_id: str = ""):
        key = vendor_key(vendor)
        if not key or not category_id:
            return
        with self._conn() as c:
            c.execute(
                "INSERT INTO vendor_memory (vendor_key,vendor_name,category_id,class_id,uses,updated_at) "
                "VALUES (?,?,?,?,1,?) ON CONFLICT(vendor_key) DO UPDATE SET "
                "vendor_name=excluded.vendor_name, category_id=excluded.category_id, "
                "class_id=excluded.class_id, uses=uses+1, updated_at=excluded.updated_at",
                (key, vendor, category_id, class_id, self._now()))

    def vendor_default(self, vendor: str) -> dict | None:
        key = vendor_key(vendor)
        if not key:
            return None
        with self._conn() as c:
            r = c.execute("SELECT * FROM vendor_memory WHERE vendor_key=?", (key,)).fetchone()
        return dict(r) if r else None

    def suggest_category(self, vendor: str = "", description: str = "") -> str:
        """
        Best guess for a line item: what you chose for the same item before, then
        the vendor's remembered default, then your Rules (matched on the vendor as
        payee and the item as memo). '' if nothing matches.
        """
        desc = (description or "").strip()
        if desc:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT category_id FROM receipt_items WHERE lower(description)=lower(?) "
                    "AND category_id!=''", (desc,)).fetchall()
            if rows:
                return Counter(r[0] for r in rows).most_common(1)[0][0]
        mem = self.vendor_default(vendor)
        if mem and mem.get("category_id"):
            return mem["category_id"]
        probe = [{"payee": vendor or "", "memo": desc, "amount": "0", "date": ""}]
        self.apply_rules(probe)
        return probe[0].get("category_id", "") or ""

    # ── booking ──────────────────────────────────────────────────────────────

    def book_receipt(self, rid: str, header: dict, items: list[dict], *,
                     account_id: str, date: str, tax_mode: str = "proportional",
                     tax_category_id: str = "", tip_category_id: str = "",
                     paid_by: str = "", base_txn: dict | None = None) -> list[dict]:
        """
        Save a reviewed receipt to the books as ONE transaction split by
        category (a single transaction if everything lands in one line).

        Personal items: paid from a company account → booked as an Owner's Draw
        (transfer portion); paid with the owner's own money → simply left out.
        base_txn: an existing (e.g. imported) transaction to split instead of
        creating a new one — it keeps its date, account, reconciled flag and
        import hash.
        Returns the saved source-side transactions.
        """
        from utils.receipt_booking import plan_receipt_lines
        from sheets.client import BUSINESS_ACCOUNTS

        # Fixed-asset lines are capitalised: filed under the balance-sheet
        # category, then depreciated from the asset register.
        if any(i.get("kind") == "asset" for i in items):
            fa = self.fixed_asset_category_id()
            items = [dict(i, category_id=fa) if i.get("kind") == "asset" else i for i in items]

        business, personal = plan_receipt_lines(
            items, header.get("tax"), header.get("tip"), tax_mode,
            tax_category_id, tip_category_id)
        acct = self.get_account(account_id) or {}
        owner_funded = (acct.get("role") or "") in ("owner_capital", "due_to_owner", "owner_paid")
        reimbursable = "1" if (acct.get("role") in ("due_to_owner", "reimb_payable") or (
            acct.get("role") == "owner_paid" and acct.get("type") == "liability")) else "0"

        lines = [{"category_id": b["category_id"], "class_id": b["class_id"],
                  "charged_to": b["charged_to"], "amount": str(b["amount"]),
                  "memo": b["memo"], "transfer": False} for b in business]
        transfer_to = ""
        if personal and not owner_funded:
            name, atype = BUSINESS_ACCOUNTS["owner_draws"]
            transfer_to = self.get_or_create_role_account("owner_draws", name, atype)["id"]
            for p in personal:
                lines.append({"category_id": "", "class_id": "", "amount": str(p["amount"]),
                              "memo": "Personal: " + p["memo"], "transfer": True})
        if not lines:
            raise ValueError("Nothing to book — every item is personal or zero.")

        vendor = header.get("vendor") or ""
        base = dict(base_txn or {})
        base.update({"date": base.get("date") or date, "account_id": base.get("account_id") or account_id,
                     "payee": base.get("payee") or vendor,
                     "memo": base.get("memo") or f"Receipt: {vendor}".strip(": "),
                     "paid_by": paid_by, "reimbursable": reimbursable})
        if not base_txn:
            base.update({"date": date, "account_id": account_id})

        if len(lines) == 1 and not transfer_to:
            ln = lines[0]
            ctype = self._category_type(ln["category_id"])
            amt = float(ln["amount"])
            txn = dict(base, category_id=ln["category_id"], class_id=ln["class_id"],
                       charged_to=ln["charged_to"], memo=ln["memo"] or base["memo"],
                       amount=str(amt if ctype == "income" else -amt))
            if base_txn and base_txn.get("split_group_id"):
                self.delete_split_group(base_txn["split_group_id"])
                txn.pop("id", None)
                txn["split_group_id"] = ""
            saved = [self.save_transaction(txn)]
        else:
            saved = self.split_transaction(base, lines, transfer_to=transfer_to)

        first = saved[0]
        self.save_receipt({"id": rid, "vendor": vendor, "receipt_date": date,
                           "subtotal": header.get("subtotal"), "tax": header.get("tax"),
                           "tip": header.get("tip"), "total": header.get("total"),
                           "currency": header.get("currency") or "USD",
                           "status": "booked", "transaction_id": first["id"],
                           "split_group_id": first.get("split_group_id", "")})
        with self._conn() as c:
            c.execute("DELETE FROM attachments WHERE receipt_id=?", (rid,))
        self.attach_receipt(rid, first)
        # The vendor's default is the category that took most of the money.
        spend: Counter = Counter()
        for i in items:
            if i.get("category_id") and i.get("business", True):
                spend[i["category_id"]] += abs(float(i.get("line_total") or 0))
        if spend:
            self.remember_vendor(vendor, spend.most_common(1)[0][0])
        return saved
