"""
Receipts, receipt line items, attachments and vendor memory — mixed into
sheets.client.Database (uses its _conn/_new_id/_now helpers).

Receipt files are copied into "FinanceBook Receipts/<database name>/" next to
the database file, so they sync (OneDrive / Google Drive) and back up alongside
it, and never mix with the user's own files (or another database's copies).
Paths are stored relative to the database folder, so receipts copied by older
versions into a plain "receipts" folder keep working; move_legacy_receipts()
moves them over on request.
"""
import hashlib
import os
import re
import shutil
from collections import Counter
from datetime import datetime

from sheets.inventory_store import pack_size_value

RECEIPTS_DIRNAME = "FinanceBook Receipts"
LEGACY_RECEIPTS_DIRNAME = "receipts"     # used up to v1.10.0

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

_RECEIPT_MONEY = ("subtotal", "shipping", "discount", "duties", "tax", "tip", "total")
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


def receipt_txn_account_locked(txn: dict | None) -> bool:
    """A transaction from a bank import, or reconciled, can't move to another account."""
    return bool(txn) and (bool(txn.get("import_hash")) or str(txn.get("reconciled", "0")) == "1")


def paid_from_key(rid: str) -> str:
    """Settings key remembering a draft receipt's "Paid from" account (no schema change)."""
    return f"receipt_paid_from:{rid}"


RECEIPT_DEFAULT_PAID_FROM = "receipt_default_paid_from"


class ReceiptsMixin:
    # ── files ────────────────────────────────────────────────────────────────

    def db_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.db_path))

    def receipts_dir(self) -> str:
        """Where this database's receipt copies go (one subfolder per database)."""
        stem = os.path.splitext(os.path.basename(self.db_path))[0] or "receipts"
        return os.path.join(self.db_dir(), RECEIPTS_DIRNAME, stem)

    def legacy_receipts_dir(self) -> str:
        return os.path.join(self.db_dir(), LEGACY_RECEIPTS_DIRNAME)

    def _is_managed_copy(self, path: str) -> bool:
        """True for a file inside a folder FinanceBook copies receipts into."""
        path = os.path.abspath(path)
        for root in (self.receipts_dir(), self.legacy_receipts_dir()):
            try:
                if os.path.commonpath([path, root]) == root:
                    return True
            except ValueError:          # different drives
                continue
        return False

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
                      (rid, os.path.relpath(dest, self.db_dir()), base, sha, self._now()))
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

    def receipt_ids_with_items(self) -> set:
        """Receipts that have line items saved (a draft or a booked receipt)."""
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT DISTINCT receipt_id FROM receipt_items")}

    def save_receipt(self, data: dict) -> dict:
        cols = (("vendor", "receipt_date", "currency") + _RECEIPT_MONEY +
                ("status", "parse_method", "raw_text", "transaction_id", "split_group_id", "notes"))
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
            c.execute("DELETE FROM settings WHERE key=?", (paid_from_key(rid),))
        if r and delete_file:
            path = self.receipt_abs_path(r)
            # Only a copy FinanceBook made — never a file elsewhere on disk.
            if path and os.path.isfile(path) and self._is_managed_copy(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def legacy_receipt_copies(self) -> list[dict]:
        """Receipts whose copy is still in the old shared "receipts" folder."""
        out = []
        for r in self.get_receipts():
            parts = re.split(r"[\\/]", r.get("file_path") or "")
            if len(parts) == 2 and parts[0].lower() == LEGACY_RECEIPTS_DIRNAME:
                out.append(r)
        return out

    def move_legacy_receipts(self) -> dict:
        """
        Move this database's receipt copies out of the old "receipts" folder
        into receipts_dir(). Each file is copied, checked against its recorded
        SHA-256, re-pointed in the database, and only then removed from the old
        folder — files there that FinanceBook didn't record are never touched.
        Returns {"moved", "missing", "failed"} counts.
        """
        out = {"moved": 0, "missing": 0, "failed": 0}
        todo = self.legacy_receipt_copies()
        if todo:
            os.makedirs(self.receipts_dir(), exist_ok=True)
        for r in todo:
            src = self.receipt_abs_path(r)
            if not os.path.isfile(src):
                out["missing"] += 1
                continue
            name = os.path.basename(src)
            dest = os.path.join(self.receipts_dir(), name)
            n = 1
            while os.path.exists(dest) and file_sha256(dest) != r.get("sha256"):
                root, ext = os.path.splitext(name)
                dest = os.path.join(self.receipts_dir(), f"{root} ({n}){ext}")
                n += 1
            try:
                if not os.path.exists(dest):
                    shutil.copy2(src, dest)
                if r.get("sha256") and file_sha256(dest) != r["sha256"]:
                    raise OSError("copy doesn't match the original")
                with self._conn() as c:
                    c.execute("UPDATE receipts SET file_path=? WHERE id=?",
                              (os.path.relpath(dest, self.db_dir()), r["id"]))
                os.remove(src)
                out["moved"] += 1
            except OSError:
                out["failed"] += 1
        try:
            if todo and not os.listdir(self.legacy_receipts_dir()):
                os.rmdir(self.legacy_receipts_dir())
        except OSError:
            pass
        return out

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
                    "line_total,category_id,class_id,charged_to,is_business,kind,transaction_id,"
                    "item_id,sku,units_per_pack) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (iid, rid, n, it.get("description") or "", float(it.get("qty") or 1),
                     float(it.get("unit_price") or 0), float(it.get("line_total") or 0),
                     it.get("category_id") or "", it.get("class_id") or "",
                     it.get("charged_to") or "",
                     0 if str(it.get("business", it.get("is_business", "1"))) in ("0", "False") else 1,
                     it.get("kind") or "expense", it.get("transaction_id") or "",
                     it.get("item_id") or "", it.get("sku") or "",
                     pack_size_value(it.get("units_per_pack"))))
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
                     shipping_category_id: str = "", duties_category_id: str = "",
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

        rc = self.get_receipt(rid) or {}
        if base_txn is None and rc.get("status") == "booked" and rc.get("transaction_id"):
            # Booking a receipt again replaces its transaction, never adds a second.
            base_txn = self.get_transaction(rc["transaction_id"])
        own = bool(base_txn) and rc.get("status") == "booked" and \
            base_txn.get("id") == rc.get("transaction_id")
        if base_txn and base_txn.get("account_id"):
            if own and not receipt_txn_account_locked(base_txn):
                # Re-booking the receipt's own transaction: "Paid from" may change.
                base_txn = dict(base_txn, account_id=account_id)
            else:
                # An attached / imported bank line stays in its bank account.
                account_id = base_txn["account_id"]
        # Fixed-asset lines are capitalised: filed under the balance-sheet
        # category, then depreciated from the asset register.
        if any(i.get("kind") == "asset" for i in items):
            fa = self.fixed_asset_category_id()
            items = [dict(i, category_id=fa) if i.get("kind") == "asset" else i for i in items]
        if any(i.get("kind") == "inventory" for i in items):
            inv = self.inventory_category_id()
            items = [dict(i, category_id=inv) if i.get("kind") == "inventory" else i
                     for i in items]

        business, personal = plan_receipt_lines(
            items, header.get("tax"), header.get("tip"), tax_mode,
            tax_category_id, tip_category_id, shipping=header.get("shipping"),
            discount=header.get("discount"), duties=header.get("duties"),
            shipping_category_id=shipping_category_id, duties_category_id=duties_category_id)
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
        note = (header.get("notes") or "").strip()       # e.g. "Order # 111-…"
        if note and note not in (base.get("notes") or ""):
            base["notes"] = " · ".join(x for x in (base.get("notes") or "", note) if x)
        if not base_txn:
            base.update({"date": date, "account_id": account_id})

        if len(lines) == 1 and not transfer_to:
            ln = lines[0]
            ctype = self._category_type(ln["category_id"])
            amt = float(ln["amount"])
            signed = amt if ctype in ("income", "other_income") else -amt
            # An existing bank entry keeps its direction (a receipt for a
            # return attached to an imported deposit stays money in).
            stored = self._stored_sign(base_txn) if base_txn else 0
            if stored and abs(signed) > 0.005 and (signed > 0) != (stored > 0):
                signed = -signed
            txn = dict(base, category_id=ln["category_id"], class_id=ln["class_id"],
                       charged_to=ln["charged_to"], memo=ln["memo"] or base["memo"],
                       amount=str(signed))
            if base_txn and base_txn.get("split_group_id"):
                self.delete_split_group(base_txn["split_group_id"])
                txn.pop("id", None)
                txn["split_group_id"] = ""
            saved = [self.save_transaction(txn)]
        else:
            # Plan amounts are "money out" (positive). split_transaction only
            # flips the sign for expense categories, so pass outflows as negative
            # — otherwise Inventory, Fixed-asset and uncategorized lines were
            # booked as money in.
            saved = self.split_transaction(
                base, [ln if ln["transfer"] else dict(ln, amount=str(-float(ln["amount"])))
                       for ln in lines], transfer_to=transfer_to)

        first = saved[0]
        rec = {"id": rid, "vendor": vendor, "receipt_date": date,
               "currency": header.get("currency") or "USD"}
        rec.update({k: header.get(k) for k in _RECEIPT_MONEY})
        if "notes" in header:
            rec["notes"] = header.get("notes") or ""
        rec.update({"status": "booked", "transaction_id": first["id"],
                    "split_group_id": first.get("split_group_id", "")})
        self.save_receipt(rec)
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

    # ── inventory received from a receipt ────────────────────────────────────

    def receive_receipt_inventory(self, rid: str, lines: list[dict], date: str,
                                  vendor: str = "") -> list[dict]:
        """
        Stock in the receipt's Inventory lines — [{item_id, qty, units_per_pack,
        landed_total, description}]. Units = qty × units per pack; the unit cost
        is the line's landed total ÷ units (6 decimals, see per_unit_cost). The
        money is already in the books (book_receipt), so no transaction is made.

        Receiving the same receipt again (re-booked, or its units corrected)
        updates this receipt's existing purchase moves in place — same move ids,
        so history and ordering are kept — removes ones no longer needed and
        adds any new ones. Stock is never received twice.
        """
        from utils.money import D
        from utils.receipt_booking import per_unit_cost, total_units
        rc = self.get_receipt(rid) or {}
        with self._conn() as c:
            rows = c.execute("SELECT id, item_id FROM stock_moves WHERE receipt_id=? AND "
                             "kind='purchase' ORDER BY created_at, id", (rid,)).fetchall()
        pool: dict = {}
        for r in rows:
            pool.setdefault(r["item_id"], []).append(r["id"])
        out = []
        for ln in lines:
            upp = pack_size_value(ln.get("units_per_pack"))
            units = total_units(ln.get("qty"), upp)
            if not ln.get("item_id") or units <= 0:
                continue
            landed = ln.get("landed_total")
            if landed is None:                   # older callers pass a per-unit cost
                landed = D(ln.get("unit_cost") or 0) * units
            uc = per_unit_cost(landed, units)
            qty = D(ln.get("qty") or 1)
            count = (f"{qty:g} pack{'s' if qty != 1 else ''} × {upp} = {units:g} units"
                     if upp != 1 else f"{units:g} unit{'s' if units != 1 else ''}")
            notes = f"Receipt: {vendor} — {ln.get('description', '')} ({count})".strip()
            reuse = pool.get(ln["item_id"]) or []
            if reuse:
                mid = reuse.pop(0)
                with self._conn() as c:
                    c.execute("UPDATE stock_moves SET date=?, qty=?, unit_cost=?, txn_id=?, notes=? "
                              "WHERE id=?", (date, float(units), float(uc),
                                             rc.get("transaction_id", ""), notes, mid))
                out.append({"id": mid, "qty": float(units)})
            else:
                out.append(self.add_move(ln["item_id"], date, "purchase", float(units), float(uc),
                                         txn_id=rc.get("transaction_id", ""), receipt_id=rid,
                                         notes=notes))
        leftover = [m for ids in pool.values() for m in ids]
        if leftover:
            with self._conn() as c:
                c.executemany("DELETE FROM stock_moves WHERE id=?", [(m,) for m in leftover])
        return out
