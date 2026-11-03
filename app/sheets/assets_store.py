"""
Fixed assets / prepaid expenses and the "computed" report lines that come from
them — mixed into sheets.client.Database.

Nothing here writes depreciation transactions: depreciation is non-cash and is
computed per period by utils.depreciation. ComputedMixin is the single place
reports and the dashboard ask for non-transaction amounts (fixed assets now;
inventory, payroll and sales-tax liabilities in later phases).
"""
from decimal import Decimal

ASSETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT DEFAULT '',
    serial              TEXT DEFAULT '',
    asset_class         TEXT DEFAULT 'equipment',
    vendor              TEXT DEFAULT '',
    purchase_date       TEXT DEFAULT '',
    in_service_date     TEXT DEFAULT '',
    cost                REAL DEFAULT 0,
    salvage             REAL DEFAULT 0,
    life_months         INTEGER DEFAULT 60,
    method              TEXT DEFAULT 'sl',
    db_factor           REAL DEFAULT 2,
    total_units         REAL DEFAULT 0,
    macrs_class         TEXT DEFAULT '5',
    tax_method          TEXT DEFAULT 'macrs',
    tax_macrs_class     TEXT DEFAULT '5',
    status              TEXT DEFAULT 'active',
    disposal_date       TEXT DEFAULT '',
    disposal_amount     REAL DEFAULT 0,
    purchase_txn_id     TEXT DEFAULT '',
    disposal_txn_id     TEXT DEFAULT '',
    receipt_id          TEXT DEFAULT '',
    expense_category_id TEXT DEFAULT '',
    notes               TEXT DEFAULT '',
    created_at          TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS asset_usage (
    id       TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    date     TEXT DEFAULT '',
    units    REAL DEFAULT 0,
    notes    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_asset ON asset_usage(asset_id);
"""

ASSET_CLASSES = [
    ("equipment", "Equipment"), ("machinery", "Machinery"), ("vehicle", "Vehicle"),
    ("computer", "Computer / electronics"), ("furniture", "Furniture & fixtures"),
    ("software", "Software"), ("improvement", "Leasehold / land improvement"),
    ("prepaid", "Prepaid expense (insurance, license…)"), ("other", "Other"),
]

_ASSET_COLS = ("name", "description", "serial", "asset_class", "vendor", "purchase_date",
               "in_service_date", "cost", "salvage", "life_months", "method", "db_factor",
               "total_units", "macrs_class", "tax_method", "tax_macrs_class", "status",
               "disposal_date", "disposal_amount", "purchase_txn_id", "disposal_txn_id",
               "receipt_id", "expense_category_id", "notes")
_NUM = {"cost", "salvage", "db_factor", "total_units", "disposal_amount"}


class AssetsMixin:
    def get_assets(self, include_disposed: bool = True) -> list[dict]:
        q = "SELECT * FROM assets"
        if not include_disposed:
            q += " WHERE status='active'"
        q += " ORDER BY in_service_date DESC, name"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q).fetchall()]

    def get_asset(self, aid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

    def save_asset(self, data: dict) -> dict:
        vals = []
        for k in _ASSET_COLS:
            v = data.get(k, "")
            if k in _NUM:
                v = float(v or 0)
            elif k == "life_months":
                v = int(float(v or 0))
            else:
                v = v or ""
            vals.append(v)
        with self._conn() as c:
            if data.get("id") and self.get_asset(data["id"]):
                c.execute(f"UPDATE assets SET {','.join(k + '=?' for k in _ASSET_COLS)} WHERE id=?",
                          vals + [data["id"]])
            else:
                data["id"] = self._new_id()
                c.execute(f"INSERT INTO assets (id,{','.join(_ASSET_COLS)},created_at) VALUES "
                          f"(?,{','.join('?' * len(_ASSET_COLS))},?)",
                          [data["id"]] + vals + [self._now()])
        return data

    def delete_asset(self, aid: str):
        with self._conn() as c:
            c.execute("DELETE FROM asset_usage WHERE asset_id=?", (aid,))
            c.execute("DELETE FROM assets WHERE id=?", (aid,))

    def get_asset_usage(self, aid: str = "") -> list[dict]:
        with self._conn() as c:
            if aid:
                rows = c.execute("SELECT * FROM asset_usage WHERE asset_id=? ORDER BY date",
                                 (aid,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM asset_usage ORDER BY date").fetchall()
        return [dict(r) for r in rows]

    def add_asset_usage(self, aid: str, date: str, units, notes: str = ""):
        with self._conn() as c:
            c.execute("INSERT INTO asset_usage (id,asset_id,date,units,notes) VALUES (?,?,?,?,?)",
                      (self._new_id(), aid, date, float(units or 0), notes))

    def delete_asset_usage(self, usage_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM asset_usage WHERE id=?", (usage_id,))

    def usage_by_asset(self) -> dict:
        out: dict = {}
        for u in self.get_asset_usage():
            out.setdefault(u["asset_id"], []).append(u)
        return out

    def fixed_asset_category_id(self, prepaid: bool = False) -> str:
        """The balance-sheet category asset purchases (and sale proceeds) are filed under."""
        name = "Prepaid Expenses" if prepaid else "Fixed Assets (purchases & sales)"
        for c in self.get_categories():
            if c.get("name") == name and not c.get("parent_id"):
                return c["id"]
        return self.save_category({"name": name, "type": "asset", "tax_line": ""})["id"]

    def dispose_asset(self, aid: str, date: str, proceeds: float, deposit_account_id: str = "",
                      sold: bool = True) -> dict:
        """
        Mark an asset sold/disposed. If proceeds were received into an account,
        record that deposit (filed under the fixed-asset category, so the cash
        itself isn't income); the gain or loss is computed for the P&L.
        """
        a = self.get_asset(aid)
        if not a:
            raise ValueError("Asset not found")
        a.update({"status": "sold" if sold else "disposed", "disposal_date": date,
                  "disposal_amount": proceeds})
        if deposit_account_id and proceeds:
            t = self.save_transaction({
                "date": date, "account_id": deposit_account_id,
                "payee": f"Sale of {a['name']}", "memo": "Asset sale proceeds",
                "amount": str(abs(float(proceeds))),
                "category_id": self.fixed_asset_category_id(a.get("asset_class") == "prepaid")})
            a["disposal_txn_id"] = t["id"]
        return self.save_asset(a)


class ComputedMixin:
    """Non-transaction amounts for the P&L and Balance Sheet."""

    def _asset_portfolio(self, as_of: str, start: str = ""):
        from utils.depreciation import portfolio
        assets = self.get_assets()
        if not assets:
            return None
        return portfolio(assets, as_of, start, self.usage_by_asset())

    def computed_pnl_lines(self, start: str, end: str) -> list[dict]:
        """
        [{section: 'expense'|'income', group, name, amount}] for [start, end].
        Amounts are signed like transactions (expenses negative).
        """
        lines = []
        p = self._asset_portfolio(end, start)
        if p:
            cats = {c["id"]: c for c in self.get_categories()}
            for cat_id, amt in p["by_category"].items():
                cat = cats.get(cat_id, {})
                parent = cats.get(cat.get("parent_id", ""), {})
                group = parent.get("name") or cat.get("name") or "Depreciation"
                name = cat.get("name", "") if parent else ""
                lines.append({"section": "expense", "group": group,
                              "name": (name + " (computed)") if name else "",
                              "amount": -amt, "computed": "depreciation"})
            if p["gain_in_range"]:
                lines.append({"section": "income", "group": "Gain / (Loss) on Asset Sales",
                              "name": "", "amount": p["gain_in_range"], "computed": "gain"})
        return lines

    def balance_sheet_extras(self, as_of: str, fy_start: str) -> dict:
        """kwargs for utils.ledger.balance_sheet: extra assets/liabilities + non-cash P&L."""
        extra_assets, extra_liab = [], []
        before = in_fy = Decimal("0")
        p = self._asset_portfolio(as_of, fy_start)
        if p:
            if p["cost"]:
                extra_assets.append(("Fixed assets & prepaids (cost)", p["cost"]))
                extra_assets.append(("Less accumulated depreciation", -p["accumulated"]))
            before += p["gain_before"] - p["dep_before"]
            in_fy += p["gain_in_range"] - p["dep_in_range"]
        return {"extra_assets": extra_assets, "extra_liabilities": extra_liab,
                "noncash_pnl_before_fy": before, "noncash_pnl_in_fy": in_fy}
