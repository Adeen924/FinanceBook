"""
Inventory items and stock movements — mixed into sheets.client.Database.

Purchases of tracked stock are filed under the "Inventory Purchases" category
(a balance-sheet type, not an expense). When stock goes out (sale, shrinkage
adjustment, return to vendor) its cost — FIFO or weighted average, per item —
becomes Cost of Goods Sold on the P&L, and what's left is the Inventory asset
on the Balance Sheet. Both are computed, never stored as transactions.
"""
from decimal import Decimal

INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id                 TEXT PRIMARY KEY,
    sku                TEXT DEFAULT '',
    name               TEXT NOT NULL,
    description        TEXT DEFAULT '',
    unit               TEXT DEFAULT 'each',
    kind               TEXT DEFAULT 'finished',
    reorder_point      REAL DEFAULT 0,
    default_vendor_id  TEXT DEFAULT '',
    sale_price         REAL DEFAULT 0,
    cost_method        TEXT DEFAULT 'fifo',
    tracked            INTEGER DEFAULT 1,
    income_category_id TEXT DEFAULT '',
    active             INTEGER DEFAULT 1,
    created_at         TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS stock_moves (
    id         TEXT PRIMARY KEY,
    item_id    TEXT NOT NULL,
    date       TEXT DEFAULT '',
    kind       TEXT DEFAULT 'purchase',
    qty        REAL DEFAULT 0,
    unit_cost  REAL DEFAULT 0,
    unit_price REAL DEFAULT 0,
    txn_id     TEXT DEFAULT '',
    receipt_id TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_moves_item ON stock_moves(item_id);
"""

MOVE_KINDS = {"opening": "Opening stock", "purchase": "Purchase", "sale": "Sale",
              "adjust": "Count adjustment", "return_in": "Customer return",
              "return_out": "Return to vendor"}
ITEM_KINDS = [("finished", "Finished good / for resale"), ("material", "Raw material / supply")]

_ITEM_COLS = ("sku", "name", "description", "unit", "kind", "pack_size", "reorder_point",
              "default_vendor_id", "sale_price", "cost_method", "tracked", "income_category_id",
              "active")


def pack_size_value(v) -> int:
    """A stored pack size: a whole number ≥ 1 (anything else → 1)."""
    try:
        n = int(str(v).strip()) if str(v or "").strip() else 1
    except ValueError:
        return 1
    return n if n >= 1 else 1


class InventoryMixin:
    def get_items(self, active_only: bool = True) -> list[dict]:
        q = "SELECT * FROM items" + (" WHERE active=1" if active_only else "")
        q += " ORDER BY name COLLATE NOCASE"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q).fetchall()]

    def get_item(self, iid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        return dict(r) if r else None

    def save_item(self, data: dict) -> dict:
        existing = self.get_item(data["id"]) if data.get("id") else None
        vals = []
        for k in _ITEM_COLS:
            v = data.get(k)
            if k == "pack_size" and k not in data and existing:
                v = existing.get("pack_size")      # callers that predate pack sizes
            if k in ("reorder_point", "sale_price"):
                v = float(v or 0)
            elif k == "pack_size":
                v = pack_size_value(v)
            elif k in ("tracked", "active"):
                v = 0 if str(v if v is not None else 1) in ("0", "False") else 1
            else:
                v = v or ("fifo" if k == "cost_method" else "each" if k == "unit"
                          else "finished" if k == "kind" else "")
            vals.append(v)
        with self._conn() as c:
            if existing:
                c.execute(f"UPDATE items SET {','.join(k + '=?' for k in _ITEM_COLS)} WHERE id=?",
                          vals + [data["id"]])
            else:
                data["id"] = self._new_id()
                c.execute(f"INSERT INTO items (id,{','.join(_ITEM_COLS)},created_at) VALUES "
                          f"(?,{','.join('?' * len(_ITEM_COLS))},?)", [data["id"]] + vals + [self._now()])
        return data

    def set_item_pack_size(self, item_id: str, pack_size) -> None:
        """Change only an item's default units per pack."""
        with self._conn() as c:
            c.execute("UPDATE items SET pack_size=? WHERE id=?", (pack_size_value(pack_size), item_id))

    def get_moves(self, item_id: str = "") -> list[dict]:
        with self._conn() as c:
            if item_id:
                rows = c.execute("SELECT * FROM stock_moves WHERE item_id=? ORDER BY date, created_at",
                                 (item_id,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM stock_moves ORDER BY date, created_at").fetchall()
        return [dict(r) for r in rows]

    def add_move(self, item_id: str, date: str, kind: str, qty, unit_cost=0, unit_price=0,
                 txn_id: str = "", receipt_id: str = "", notes: str = "") -> dict:
        """qty is a magnitude; the kind decides the direction."""
        q = abs(float(qty or 0))
        if kind in ("sale", "return_out") or (kind == "adjust" and float(qty or 0) < 0):
            q = -q
        mid = self._new_id()
        with self._conn() as c:
            c.execute("INSERT INTO stock_moves (id,item_id,date,kind,qty,unit_cost,unit_price,txn_id,"
                      "receipt_id,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (mid, item_id, date, kind, q, float(unit_cost or 0), float(unit_price or 0),
                       txn_id, receipt_id, notes, self._now()))
        return {"id": mid, "qty": q}

    def delete_move(self, mid: str):
        with self._conn() as c:
            c.execute("DELETE FROM stock_moves WHERE id=?", (mid,))

    def inventory_category_id(self) -> str:
        for c in self.get_categories():
            if c.get("name") == "Inventory Purchases" and not c.get("parent_id"):
                return c["id"]
        return self.save_category({"name": "Inventory Purchases", "type": "inventory",
                                   "tax_line": ""})["id"]

    def item_status(self, item: dict, as_of: str = "") -> dict:
        from utils.inventory import run_costing, needs_reorder
        r = run_costing(self.get_moves(item["id"]), item.get("cost_method") or "fifo", as_of)
        return {"qty": r["qty"], "value": r["value"], "unit_cost": r["unit_cost"],
                "short": r["short"], "low": needs_reorder(r["qty"], item.get("reorder_point"))}

    def inventory_totals(self, as_of: str = "", start: str = "") -> dict:
        """Inventory value, COGS for [start, as_of], COGS before start, opening stock value."""
        from utils.inventory import run_costing, cogs_between, opening_value
        from utils.money import cents
        value = cogs_in = cogs_before = opening = Decimal(0)
        low = []
        for it in self.get_items(active_only=False):
            if str(it.get("tracked", 1)) in ("0",):
                continue
            moves = self.get_moves(it["id"])
            if not moves:
                continue
            method = it.get("cost_method") or "fifo"
            r = run_costing(moves, method, as_of)
            value += r["value"]
            cogs_in += cogs_between(moves, method, start, as_of)
            if start:
                cogs_before += cogs_between(moves, method, "", _day_before(start))
            opening += opening_value(moves, method, as_of)
            if str(it.get("active", 1)) == "1" and r["qty"] <= Decimal(str(it.get("reorder_point") or 0)) \
                    and float(it.get("reorder_point") or 0) > 0:
                low.append(it["name"])
        return {"value": cents(value), "cogs": cents(cogs_in), "cogs_before": cents(cogs_before),
                "opening": cents(opening), "low_stock": low}

    def receive_stock(self, item_id: str, date: str, qty, unit_cost, account_id: str = "",
                      receipt_id: str = "", notes: str = "", paid_by: str = "") -> dict:
        """Stock in. If paid from an account, record the purchase transaction too."""
        txn_id = ""
        if account_id:
            item = self.get_item(item_id) or {}
            vendor = self.get_contact(item.get("default_vendor_id", "")) or {}
            t = self.save_transaction({
                "date": date, "account_id": account_id, "payee": vendor.get("name", ""),
                "memo": f"Stock: {float(qty):g} × {item.get('name', '')}",
                "amount": str(-round(float(qty) * float(unit_cost), 2)),
                "category_id": self.inventory_category_id(),
                "contact_id": vendor.get("id", ""), "paid_by": paid_by})
            txn_id = t["id"]
        return self.add_move(item_id, date, "purchase", qty, unit_cost, txn_id=txn_id,
                             receipt_id=receipt_id, notes=notes)

    def sell_stock(self, item_id: str, date: str, qty, unit_price, account_id: str = "",
                   notes: str = "") -> dict:
        """Stock out at a sale. If paid into an account, record the income too."""
        txn_id = ""
        if account_id and float(unit_price or 0):
            item = self.get_item(item_id) or {}
            cat = item.get("income_category_id") or next(
                (c["id"] for c in self.get_categories() if c["name"] == "Product Sales"), "")
            t = self.save_transaction({
                "date": date, "account_id": account_id, "payee": notes or "Sale",
                "memo": f"Sold {float(qty):g} × {item.get('name', '')}",
                "amount": str(round(float(qty) * float(unit_price), 2)), "category_id": cat})
            txn_id = t["id"]
        return self.add_move(item_id, date, "sale", qty, unit_price=unit_price, txn_id=txn_id,
                             notes=notes)

    def count_stock(self, item_id: str, date: str, counted, notes: str = "") -> dict | None:
        """Stock count: record the difference as an adjustment (None if no change)."""
        st = self.item_status(self.get_item(item_id), date)
        diff = float(counted) - float(st["qty"])
        if abs(diff) < 1e-9:
            return None
        return self.add_move(item_id, date, "adjust", diff,
                             unit_cost=float(st["unit_cost"]) if diff > 0 else 0,
                             notes=notes or "Stock count")


def _day_before(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return date.fromordinal(d.toordinal() - 1).isoformat()
