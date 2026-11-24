"""
Report math shared by the Reports page and the year-end export. Pure.

  pnl_rows()        normalised P&L rows (transactions + computed lines)
  pivot()           sum P&L rows into a {row: {column: amount}} grid
  schedule_c()      group by Schedule C tax line, applying deductible %
  cash_flow()       direct-method cash flow statement for cash accounts
  budget_vs_actual()
"""
from datetime import date, timedelta
from typing import NamedTuple

from utils.money import D, cents, ZERO
from utils.ledger import pnl_bucket, pnl_section, account_class, is_income_type

CASH_TYPES = ("checking", "savings", "cash", "gift card", "investment", "other")


def _cat_path(cat_id, cats):
    cat = cats.get(cat_id or "", {}) or {}
    parent = cats.get(cat.get("parent_id", ""), {}) or {}
    return cat, parent


def pnl_rows(txns, cats, computed_lines=()) -> list[dict]:
    """
    [{date, month, section 'income'|'expense', other, cogs, group, category_id,
      amount, class_id, contact_id, payee, paid_by, charged_to}] — transfers and
    non-P&L categories dropped. other=True for "Other Income / Other Expense"
    categories, cogs=True for Cost of Goods Sold (a "cogs" category or the
    inventory module's computed COGS); section still says which side, so totals
    and net income are unchanged. computed_lines (depreciation, COGS…) carry
    the date they belong to (their period's last day).
    """
    out = []
    for t in txns:
        if str(t.get("is_transfer", "0")) == "1":
            continue
        sec = pnl_section(t, cats)
        if sec == "excluded":
            continue
        b = pnl_bucket(t, cats)
        cat, parent = _cat_path(t.get("category_id"), cats)
        out.append({"date": t.get("date", ""), "month": (t.get("date") or "")[:7], "section": b,
                    "other": sec in ("other_income", "other_expense"),
                    "cogs": sec == "cogs",
                    "group": parent.get("name") or cat.get("name") or "Uncategorized",
                    "category_id": t.get("category_id", ""), "amount": D(t.get("amount")),
                    "class_id": t.get("class_id", ""), "contact_id": t.get("contact_id", ""),
                    "payee": t.get("payee", ""), "paid_by": t.get("paid_by", ""),
                    "charged_to": t.get("charged_to", "")})
    for ln in computed_lines:
        out.append({"date": ln.get("date", ""), "month": (ln.get("date") or "")[:7],
                    "section": ln["section"], "other": False,
                    "cogs": ln.get("computed") == "cogs", "group": ln["group"],
                    "name": ln.get("name", ""),
                    "category_id": ln.get("category_id", ""), "amount": D(ln["amount"]),
                    "class_id": "", "contact_id": "", "payee": "(computed)", "paid_by": "",
                    "charged_to": "", "computed": ln.get("computed", "")})
    return out


def pivot(rows, row_key, col_key) -> dict:
    """{row: {col: Decimal}} plus '__total__' column and row."""
    grid: dict = {}
    for r in rows:
        rk, ck = row_key(r), col_key(r)
        g = grid.setdefault(rk, {})
        g[ck] = g.get(ck, ZERO) + r["amount"]
        g["__total__"] = g.get("__total__", ZERO) + r["amount"]
        tot = grid.setdefault("__total__", {})
        tot[ck] = tot.get(ck, ZERO) + r["amount"]
        tot["__total__"] = tot.get("__total__", ZERO) + r["amount"]
    return {rk: {ck: cents(v) for ck, v in cols.items()} for rk, cols in grid.items()}


def months_between(start: str, end: str) -> list[str]:
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= end[:7]:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


# ── period columns (v1.18) ───────────────────────────────────────────────────

WEEK_START = 0              # weeks run Monday (date.weekday() 0) to Sunday
TOTAL = "__total__"         # the Total column's key (also pivot()'s)
MANY_COLUMNS = 60           # more columns than this: the Reports page asks first
_PERIODS = (("day", "Day"), ("week", "Week"), ("month", "Month"),
            ("quarter", "Quarter"), ("year", "Year"), ("total", "Total"))
_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def period_choices() -> tuple:
    """((key, label), …) for the "Columns:" dropdown, in its order."""
    return _PERIODS


class Bucket(NamedTuple):
    key: str        # first day of the whole period (TOTAL for the Total column)
    label: str      # column heading
    start: str      # first day inside the report range
    end: str        # last day inside the report range
    partial: bool   # the range cuts this period short


def period_start(d: date, period: str) -> date:
    if period == "day":
        return d
    if period == "week":
        return d - timedelta(days=(d.weekday() - WEEK_START) % 7)
    if period == "month":
        return d.replace(day=1)
    if period == "quarter":
        return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    if period == "year":
        return date(d.year, 1, 1)
    raise ValueError(f"unknown period {period!r}")


def _next_start(s: date, period: str) -> date:
    if period == "day":
        return s + timedelta(days=1)
    if period == "week":
        return s + timedelta(days=7)
    months = {"month": 1, "quarter": 3, "year": 12}[period]
    m = s.month - 1 + months
    return date(s.year + m // 12, m % 12 + 1, 1)


def period_key(iso: str, period: str) -> str:
    """The column a date falls in: its period's first day (ISO), TOTAL for
    the Total-only layout, "" for a missing / bad date."""
    if period == "total":
        return TOTAL
    try:
        return period_start(date.fromisoformat((iso or "")[:10]), period).isoformat()
    except ValueError:
        return ""


def _md(d: date) -> str:
    return f"{_MON[d.month - 1]} {d.day}"


def _span(a: date, b: date) -> str:
    if a == b:
        return _md(a)
    if (a.year, a.month) == (b.year, b.month):
        return f"{_md(a)}–{b.day}"
    return f"{_md(a)}–{_md(b)}"


def _label(cur: date, a: date, b: date, period: str, partial: bool, multi_year: bool) -> str:
    """Column heading: "Nov 16", "Wk of Nov 10", "Nov 26", "Q4 26", "2026". A
    period the range cuts short shows its real dates instead ("Nov 12–15",
    "Nov 1–16 26", "Nov 1–Dec 31 25", "Jul 1–Dec 31 2025"). Days and weeks add
    the year when the range spans more than one."""
    yy = f"{b.year % 100:02d}"
    year = f", {(b if partial else a).year}" if multi_year else ""
    if period == "day":
        return _md(a) + year
    if period == "week":
        return (_span(a, b) if partial else f"Wk of {_md(a)}") + year
    if partial:
        return f"{_span(a, b)} {b.year if period == 'year' else yy}"
    if period == "month":
        return f"{_MON[cur.month - 1]} {yy}"
    if period == "quarter":
        return f"Q{(cur.month - 1) // 3 + 1} {yy}"
    return str(cur.year)


def period_buckets(start: str, end: str, period: str) -> list:
    """[Bucket, …] covering [start, end] (ISO, inclusive) for a Columns choice:
    day, week (Monday–Sunday, WEEK_START), month, quarter, year, or total (one
    Total bucket). The first and last periods may be partial."""
    if period == "total":
        return [Bucket(TOTAL, "Total", start, end, False)]
    s, e = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    out = []
    multi_year = s.year != e.year
    cur = period_start(s, period)
    while cur <= e:
        nxt = _next_start(cur, period)
        last = nxt - timedelta(days=1)
        a, b = max(cur, s), min(last, e)
        partial = (a, b) != (cur, last)
        out.append(Bucket(cur.isoformat(), _label(cur, a, b, period, partial, multi_year),
                          a.isoformat(), b.isoformat(), partial))
        cur = nxt
    return out


def computed_segments(start: str, end: str, period: str) -> list:
    """[(seg_start, seg_end), …] to compute non-cash lines for so each lands in
    the column it belongs to. Depreciation is worked out by whole month, so
    Day and Week columns use calendar months (each month's amount goes in the
    column holding the month's last day in the range); longer periods use
    their own columns."""
    if period in ("day", "week"):
        return [(b.start, b.end) for b in period_buckets(start, end, "month")]
    return [(b.start, b.end) for b in period_buckets(start, end, period)]


# ── P&L structure with sub-accounts (v1.18) ──────────────────────────────────

SECTION_TITLES = {"Income": "Income", "Cost of Goods Sold": "Cost of Goods Sold",
                  "Expense": "Expenses", "Other Income": "Other Income",
                  "Other Expense": "Other Expenses"}
# Display sign per section: money out is shown as a positive amount spent.
SECTION_SIGN = {"Income": 1, "Cost of Goods Sold": -1, "Expense": -1,
                "Other Income": 1, "Other Expense": -1}
OTHER_GROUP = "Other Income / Other Expense"


def account_path(row, cats) -> tuple:
    """Names from the top-level account down to the row's own account (any
    depth). Computed lines without a category use (group, name); a row with
    no account is ("Uncategorized",)."""
    cid = row.get("category_id") or ""
    if cid in cats:
        names, seen = [], set()
        while cid and cid in cats and cid not in seen:
            seen.add(cid)
            names.append(cats[cid].get("name") or "")
            cid = cats[cid].get("parent_id") or ""
        return tuple(reversed(names))
    if row.get("computed"):
        return (row.get("group") or "Other",) + ((row["name"],) if row.get("name") else ())
    return (row.get("group") or "Uncategorized",)


def _add(d: dict, k, v):
    d[k] = d.get(k, ZERO) + v


def _add_col(d: dict, col, v):
    """Add v to its column and to the Total (once when the column is Total)."""
    if col != TOTAL:
        _add(d, col, v)
    _add(d, TOTAL, v)


def _tree_sections(rows, cats, col_of, section_of) -> dict:
    roots: dict = {}
    for r in rows:
        node = roots.setdefault(section_of(r), {"children": {}, "own": {}})
        for name in account_path(r, cats):
            node = node["children"].setdefault(name, {"label": name, "children": {},
                                                      "own": {}})
        _add_col(node["own"], col_of(r), r["amount"])
    return roots


def _finish(node, sign: int, path: tuple = ()) -> dict:
    path = path + ((node["label"],) if node.get("label") else ())
    kids = [_finish(c, sign, path) for c in sorted(node["children"].values(),
                                                   key=lambda c: c["label"].lower())]
    total = dict(node["own"])
    for k in kids:
        for col, v in k["raw_total"].items():
            _add(total, col, v)
    return {"label": node.get("label", ""), "children": kids, "path": path,
            "raw_total": total,
            "own": {k: cents(v * sign) for k, v in node["own"].items()},
            "total": {k: cents(v * sign) for k, v in total.items()},
            "has_own": any(v != 0 for v in node["own"].values())}


def _sum(rows, col_of, keep) -> dict:
    out: dict = {}
    for r in rows:
        if keep(r):
            _add_col(out, col_of(r), r["amount"])
    return {k: cents(v) for k, v in out.items()}


def pnl_report(rows, cats, columns, col_of, memo_rows=(), signed: bool = False) -> dict:
    """
    The P&L as blocks, top to bottom:
      section Income · [section Cost of Goods Sold · summary Gross Profit] ·
      section Expenses · [summary Net Operating Income · group Other Income /
      Other Expense (sections)] · summary Net Income · memo sections.
    A section holds nodes {label, children, own, total, has_own} for every
    account level (parents total their subs and their own rows). Amounts are
    {column key: Decimal} for `columns` (period keys) plus TOTAL; col_of(row)
    gives a row's column. Expenses are shown positive unless signed=True.
    memo_rows carry "memo" (a section label) and sit below Net Income.
    """
    cols = [c for c in columns if c != TOTAL] + [TOTAL]
    roots = _tree_sections(rows, cats, col_of, pnl_section_label)
    has = {s for s in roots}

    def section(label):
        sign = 1 if signed else SECTION_SIGN[label]
        node = _finish(roots.get(label, {"children": {}, "own": {}}), sign)
        return {"type": "section", "label": SECTION_TITLES[label], "key": label,
                "sign": sign, "nodes": node["children"], "total": node["total"]}

    def summary(label, keep):
        return {"type": "summary", "label": label, "amounts": _sum(rows, col_of, keep)}

    blocks = [section("Income")]
    if COGS_LABEL in has:
        blocks.append(section(COGS_LABEL))
        blocks.append(summary("Gross Profit", lambda r: not r.get("other") and (
            r.get("cogs") or r["section"] == "income")))
    blocks.append(section("Expense"))
    other = [s for s in ("Other Income", "Other Expense") if s in has]
    if other:
        blocks.append(summary("Net Operating Income", lambda r: not r.get("other")))
        blocks.append({"type": "group", "label": OTHER_GROUP,
                       "sections": [section(s) for s in other]})
    blocks.append(summary("Net Income", lambda r: True))
    if memo_rows:
        mroots = _tree_sections(memo_rows, cats, col_of, lambda r: r["memo"])
        for label in sorted(mroots, key=lambda m: MEMO_ORDER.index(m)
                            if m in MEMO_ORDER else 99):
            sign = 1 if signed else -1
            node = _finish(mroots[label], sign)
            blocks.append({"type": "section", "label": label, "key": label, "memo": True,
                           "sign": sign, "nodes": node["children"], "total": node["total"]})
    return {"columns": cols, "blocks": blocks}


MEMO_DEBT = "Debt Repayment"
MEMO_CAPITAL = "Asset & Inventory Purchases (on Balance Sheet)"
MEMO_ORDER = (MEMO_DEBT, MEMO_CAPITAL)


def memo_rows(txns, cats) -> list:
    """Rows for the memo sections under Net Income: loan principal (Debt
    Repayment) and fixed-asset / inventory purchases, which are balance-sheet
    items, not P&L."""
    out = []
    for t in txns:
        if str(t.get("is_transfer", "0")) == "1":
            continue
        cat = cats.get(t.get("category_id", "") or "") or {}
        ctype = cat.get("type", "")
        label = MEMO_DEBT if ctype == "debt_repayment" else (
            MEMO_CAPITAL if ctype in ("asset", "inventory") else "")
        if label:
            out.append({"date": t.get("date", ""), "memo": label, "amount": D(t.get("amount")),
                        "category_id": t.get("category_id", ""), "section": "expense",
                        "class_id": t.get("class_id", "")})
    return out


def _node_lines(node, depth: int, section: str) -> list:
    path = node.get("path", (node["label"],))
    if not node["children"]:
        return [{"label": node["label"], "depth": depth, "kind": "leaf",
                 "amounts": node["total"], "section": section, "path": path}]
    out = [{"label": node["label"], "depth": depth, "kind": "parent", "amounts": None,
            "collapsed": node["total"], "section": section, "path": path}]
    for ch in node["children"]:
        out += _node_lines(ch, depth + 1, section)
    if node["has_own"]:
        out.append({"label": f"{node['label']} (other)", "depth": depth + 1, "kind": "other",
                    "amounts": node["own"], "section": section, "path": path})
    out.append({"label": f"Total {node['label']}", "depth": depth, "kind": "total",
                "amounts": node["total"], "section": section, "path": path})
    return out


def _section_lines(sec, depth: int) -> list:
    out = [{"label": sec["label"], "depth": depth, "kind": "section", "amounts": None,
            "collapsed": sec["total"], "section": sec["key"]}]
    for n in sec["nodes"]:
        for ln in _node_lines(n, depth + 1, sec["key"]):
            ln["level"] = ln["depth"] - depth - 1      # 0 = a top-level account
            out.append(ln)
    out.append({"label": f"Total {sec['label']}", "depth": depth, "kind": "section_total",
                "amounts": sec["total"], "section": sec["key"]})
    return out


SUMMARY_KEEP = {
    "Gross Profit": lambda r: not r.get("other") and (r.get("cogs") or r["section"] == "income"),
    "Net Operating Income": lambda r: not r.get("other"),
    "Net Income": lambda r: True,
}


def row_section(r) -> str:
    """The P&L section key a report row sits in (a memo row's memo label)."""
    return r.get("memo") or pnl_section_label(r)


def drill_rows(rows, cats, line: dict) -> list:
    """The report rows behind one report line (R1 drill-down). line is a
    pnl_lines() row, or {"kind", "section", "path", "label"}: a section or
    its total → every row in the section; an account (parent / its Total /
    leaf) → its rows and its sub-accounts'; "(other)" → the account's own
    rows; a summary (Gross Profit, Net Income…) → the rows it adds up. Memo
    rows ("memo" set) only belong to their memo section."""
    kind = line.get("kind", "")
    sec = line.get("section", "")
    path = tuple(line.get("path") or ())
    if kind == "summary":
        keep = SUMMARY_KEEP.get(line.get("label", ""), lambda r: True)
        return [r for r in rows if not r.get("memo") and keep(r)]
    out = []
    for r in rows:
        if row_section(r) != sec:
            continue
        if kind in ("section", "section_total"):
            out.append(r)
            continue
        p = account_path(r, cats)
        if kind == "other":
            if p == path:
                out.append(r)
        elif p[:len(path)] == path:
            out.append(r)
    return out


def pnl_lines(report) -> list:
    """pnl_report() flattened into rows for grids and CSV:
    {label, depth, kind, amounts (None for a heading), section}. kind is
    section / section_total / group / summary / parent / leaf / other / total."""
    out = []
    for b in report["blocks"]:
        if b["type"] == "section":
            out += _section_lines(b, 0)
        elif b["type"] == "summary":
            out.append({"label": b["label"], "depth": 0, "kind": "summary",
                        "amounts": b["amounts"], "section": ""})
        else:
            out.append({"label": b["label"], "depth": 0, "kind": "group", "amounts": None,
                        "section": ""})
            for s in b["sections"]:
                out += _section_lines(s, 1)
    return out


def tax_line_for(cat_id, cats) -> tuple[str, object]:
    """(tax_line, deductible fraction) — a sub-category inherits its parent's line."""
    cat, parent = _cat_path(cat_id, cats)
    line = cat.get("tax_line") or parent.get("tax_line") or ""
    pct = cat.get("deduct_pct")
    if pct in (None, "") and parent:
        pct = parent.get("deduct_pct")
    pct = D(pct if pct not in (None, "") else 100) / 100
    return line, pct


def schedule_c(rows, cats) -> dict:
    """
    {"lines": {tax_line: {"book": Decimal, "deductible": Decimal}}, "gross_receipts",
     "returns", "cogs", "other_income", "gross_income", "expenses", "net_profit",
     "unmapped": Decimal}. Expenses positive; income positive.
    """
    lines: dict = {}
    unmapped = ZERO
    for r in rows:
        if r.get("computed") == "cogs":
            line, pct = "4", D(1)
        elif r.get("cogs"):
            line, pct = tax_line_for(r["category_id"], cats)
            line = line or "4"          # a Cost of Goods Sold account is line 4
        elif r.get("computed") == "depreciation":
            line, pct = "13", D(1)
        elif r.get("computed") == "gain":
            line, pct = "", D(1)       # Form 4797 — outside Schedule C
        else:
            line, pct = tax_line_for(r["category_id"], cats)
        if not line:
            if r.get("computed") != "gain":
                unmapped += r["amount"]
            continue
        amt = r["amount"] if r["section"] == "income" else -r["amount"]
        e = lines.setdefault(line, {"book": ZERO, "deductible": ZERO})
        e["book"] += amt
        e["deductible"] += amt * pct
    for e in lines.values():
        e["book"], e["deductible"] = cents(e["book"]), cents(e["deductible"])

    def get(k):
        return lines.get(k, {}).get("deductible", ZERO)

    gross_receipts = get("1")
    returns = -get("2") if get("2") < 0 else get("2")
    gross_profit = gross_receipts - abs(get("2")) - get("4")
    gross_income = gross_profit + get("6")
    expense_keys = [k for k in lines if k not in ("1", "2", "4", "6")]
    expenses = sum((get(k) for k in expense_keys), ZERO)
    return {"lines": lines, "gross_receipts": gross_receipts, "returns": abs(returns),
            "cogs": get("4"), "other_income": get("6"), "gross_income": cents(gross_income),
            "expenses": cents(expenses), "net_profit": cents(gross_income - expenses),
            "unmapped": cents(unmapped)}


def cash_flow(accounts, txns, cats, start: str, end: str) -> dict:
    """
    Direct-method cash flow for "cash" accounts (bank, cash, petty cash —
    not cards, loans or owner equity accounts). Every cash movement is
    classified by its category, or for transfers by the other account:
      operating  income/expenses, inventory, credit-card payments, sales/payroll tax
      investing  fixed-asset purchases and sales
      financing  owner contributions/draws, Due to Owner, loans, debt repayment
    Transfers between two cash accounts are internal and ignored.
    """
    acc = {a["id"]: a for a in accounts}

    def is_cash(a):
        return a and account_class(a) == "asset" and (a.get("type") or "") in CASH_TYPES + ("",)

    cash_ids = {a["id"] for a in accounts if is_cash(a)}
    by_pair: dict = {}
    for t in txns:
        if t.get("transfer_pair_id"):
            by_pair.setdefault(t["transfer_pair_id"], []).append(t)
    begin = sum((D(acc[i].get("opening_balance")) for i in cash_ids), ZERO)
    sections = {"operating": {}, "investing": {}, "financing": {}}
    for t in txns:
        if t.get("account_id") not in cash_ids:
            continue
        d = t.get("date") or ""
        amt = D(t.get("amount"))
        if d < start:
            begin += amt
            continue
        if d > end:
            continue
        if str(t.get("is_transfer", "0")) == "1":
            others = [o for o in by_pair.get(t.get("transfer_pair_id"), [])
                      if o.get("id") != t.get("id")]
            other = acc.get(others[0]["account_id"]) if others else None
            if other and other["id"] in cash_ids:
                continue
            role = (other or {}).get("role", "")
            cls = account_class(other) if other else ""
            if role in ("owner_capital", "owner_draws", "owner_paid", "due_to_owner"):
                sec, label = "financing", {"owner_draws": "Owner draws"}.get(role, "Owner contributions & loans")
            elif role in ("sales_tax_payable", "payroll_liabilities"):
                sec, label = "operating", "Taxes remitted / withheld"
            elif (other or {}).get("type") in ("credit card", "credit"):
                sec, label = "operating", "Credit card payments"
            elif (other or {}).get("type") == "fixed assets":
                sec, label = "investing", "Fixed assets bought / sold"
            elif cls == "liability":
                sec, label = "financing", "Loans"
            else:
                sec, label = "financing", "Other transfers"
        else:
            cat, parent = _cat_path(t.get("category_id"), cats)
            ctype = cat.get("type", "") if cat else ""
            if ctype == "asset":
                sec, label = "investing", "Fixed assets bought / sold"
            elif ctype == "debt_repayment":
                sec, label = "financing", "Debt repayment"
            elif ctype == "inventory":
                sec, label = "operating", "Inventory purchases"
            elif ctype == "cogs":
                sec, label = "operating", "Cash paid for cost of goods sold"
            elif pnl_bucket(t, cats) == "income":
                sec, label = "operating", "Cash received from customers"
            else:
                sec, label = "operating", "Cash paid for expenses"
        sections[sec][label] = sections[sec].get(label, ZERO) + amt
    order = {label: i for i, label in enumerate(CASH_FLOW_LINE_ORDER)}
    sections = {k: dict(sorted(v.items(), key=lambda kv: order.get(kv[0], len(order))))
                for k, v in sections.items()}
    totals = {k: cents(sum(v.values(), ZERO)) for k, v in sections.items()}
    net = cents(sum(totals.values(), ZERO))
    return {"begin": cents(begin), "sections": {k: {l: cents(a) for l, a in v.items()}
                                                for k, v in sections.items()},
            "totals": totals, "net_change": net, "end": cents(begin + net)}


# Cash-flow lines in statement order (same order as the P&L: money in, cost of
# goods, expenses); anything else keeps its place after these.
CASH_FLOW_LINE_ORDER = (
    "Cash received from customers", "Cash paid for cost of goods sold",
    "Inventory purchases", "Cash paid for expenses", "Credit card payments",
    "Taxes remitted / withheld", "Fixed assets bought / sold", "Owner contributions & loans",
    "Owner draws", "Loans", "Debt repayment", "Other transfers")


def pnl_section_label(row) -> str:
    """Report heading for a pnl_rows() row."""
    if row.get("other"):
        return "Other Income" if row["section"] == "income" else "Other Expense"
    if row.get("cogs"):
        return COGS_LABEL
    return "Income" if row["section"] == "income" else "Expense"


COGS_LABEL = "Cost of Goods Sold"
# Order of P&L sections on screen and in exports: Income, Cost of Goods Sold
# (→ Gross Profit), Expense (→ Net Operating Income), Other Income / Expense.
PNL_SECTION_ORDER = ("Income", COGS_LABEL, "Expense", "Other Income", "Other Expense")


def budget_vs_actual(rows, budgets: dict, cats, start: str, end: str) -> list[dict]:
    """
    budgets: {category_id: monthly amount (positive)} for top-level accounts.
    Each top-level account's row compares its budget with its actual (its
    sub-accounts roll up); below it, indented (depth 1, 2, …), each sub-account
    with activity and "<Parent> (other)" for a parent's own transactions —
    these carry no budget (None). Ordered like the P&L: income, cost of goods
    sold, expense, other income, other expense, then by name.
    """
    n_months = len(months_between(start, end))
    own: dict = {}
    for r in rows:
        cid = r["category_id"] if r["category_id"] in cats else ""
        own[cid] = own.get(cid, ZERO) + (r["amount"] if r["section"] == "income"
                                         else -r["amount"])
    kids: dict = {}
    for c in cats.values():
        pid = c.get("parent_id") or ""
        if pid and pid in cats and pid != c["id"]:
            kids.setdefault(pid, []).append(c["id"])

    def root_of(cid):
        seen = set()
        while (cats.get(cid, {}).get("parent_id") or "") in cats and cid not in seen:
            seen.add(cid)
            cid = cats[cid]["parent_id"]
        return cid

    def total(cid, seen=()):
        if cid in seen:
            return ZERO
        return own.get(cid, ZERO) + sum((total(k, seen + (cid,)) for k in kids.get(cid, ())),
                                        ZERO)

    def sub_rows(cid, depth, income, seen=()):
        out = []
        active = [k for k in sorted(kids.get(cid, ()), key=lambda k: cats[k]["name"].lower())
                  if k not in seen and total(k, seen + (cid,)) != 0]
        for k in active:
            out.append({"category_id": k, "name": cats[k].get("name", ""), "depth": depth,
                        "income": income, "budget": None,
                        "actual": cents(total(k, seen + (cid,))), "variance": None,
                        "pct": None})
            out += sub_rows(k, depth + 1, income, seen + (cid,))
        if active and own.get(cid, ZERO) != 0:
            out.append({"category_id": "", "name": f"{cats[cid].get('name', '')} (other)",
                        "depth": depth, "income": income, "budget": None,
                        "actual": cents(own[cid]), "variance": None, "pct": None})
        return out

    roots = {root_of(c) for c in budgets if c in cats} | \
        {root_of(c) for c, v in own.items() if c and v != 0}
    rank = {"income": 0, "cogs": 1, "expense": 2, "other_income": 3, "other_expense": 4}
    out = []
    for cid in sorted(roots, key=lambda c: (rank.get(cats[c].get("type"), 2),
                                            cats[c].get("name", "").lower())):
        cat = cats[cid]
        b = cents(D(budgets.get(cid)) * n_months)
        a = cents(total(cid))
        income = is_income_type(cat.get("type"))
        variance = cents(a - b) if income else cents(b - a)   # + = good
        out.append({"category_id": cid, "name": cat.get("name", "Uncategorized"), "depth": 0,
                    "income": income, "budget": b, "actual": a, "variance": variance,
                    "pct": (a / b * 100) if b else None})
        out += sub_rows(cid, 1, income)
    return out
