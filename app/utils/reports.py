"""
Report math shared by the Reports page and the year-end export. Pure.

  pnl_rows()        normalised P&L rows (transactions + computed lines)
  pivot()           sum P&L rows into a {row: {column: amount}} grid
  schedule_c()      group by Schedule C tax line, applying deductible %
  cash_flow()       direct-method cash flow statement for cash accounts
  budget_vs_actual()
"""
from datetime import date

from utils.money import D, cents, ZERO
from utils.ledger import pnl_bucket, pnl_section, account_class, is_income_type

CASH_TYPES = ("checking", "savings", "cash", "gift card", "investment", "other")


def _cat_path(cat_id, cats):
    cat = cats.get(cat_id or "", {}) or {}
    parent = cats.get(cat.get("parent_id", ""), {}) or {}
    return cat, parent


def pnl_rows(txns, cats, computed_lines=()) -> list[dict]:
    """
    [{date, month, section 'income'|'expense', other, group, category_id, amount,
      class_id, contact_id, payee, paid_by, charged_to}] — transfers and non-P&L
    categories dropped. other=True for "Other Income / Other Expense" categories
    (section still says which side, so totals and net income are unchanged). computed_lines (depreciation, COGS…) are added on their period's last date.
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
                    "group": parent.get("name") or cat.get("name") or "Uncategorized",
                    "category_id": t.get("category_id", ""), "amount": D(t.get("amount")),
                    "class_id": t.get("class_id", ""), "contact_id": t.get("contact_id", ""),
                    "payee": t.get("payee", ""), "paid_by": t.get("paid_by", ""),
                    "charged_to": t.get("charged_to", "")})
    for ln in computed_lines:
        out.append({"date": ln.get("date", ""), "month": (ln.get("date") or "")[:7],
                    "section": ln["section"], "other": False, "group": ln["group"],
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
            elif pnl_bucket(t, cats) == "income":
                sec, label = "operating", "Cash received from customers"
            else:
                sec, label = "operating", "Cash paid for expenses"
        sections[sec][label] = sections[sec].get(label, ZERO) + amt
    totals = {k: cents(sum(v.values(), ZERO)) for k, v in sections.items()}
    net = cents(sum(totals.values(), ZERO))
    return {"begin": cents(begin), "sections": {k: {l: cents(a) for l, a in v.items()}
                                                for k, v in sections.items()},
            "totals": totals, "net_change": net, "end": cents(begin + net)}


def pnl_section_label(row) -> str:
    """Report heading for a pnl_rows() row."""
    if row.get("other"):
        return "Other Income" if row["section"] == "income" else "Other Expense"
    return "Income" if row["section"] == "income" else "Expense"


# Order of P&L sections on screen and in exports.
PNL_SECTION_ORDER = ("Income", "Expense", "Other Income", "Other Expense")


def budget_vs_actual(rows, budgets: dict, cats, start: str, end: str) -> list[dict]:
    """
    budgets: {category_id: monthly amount (positive)}. Actuals are summed per
    top-level category (sub-categories roll up). Returns rows sorted by name.
    """
    n_months = len(months_between(start, end))
    actual: dict = {}
    for r in rows:
        cat, parent = _cat_path(r["category_id"], cats)
        key = parent.get("id") or cat.get("id") or ""
        actual[key] = actual.get(key, ZERO) + (r["amount"] if r["section"] == "income" else -r["amount"])
    out = []
    for cid in set(budgets) | {k for k in actual if k}:
        cat = cats.get(cid, {})
        if cat.get("parent_id"):
            continue
        b = cents(D(budgets.get(cid)) * n_months)
        a = cents(actual.get(cid, ZERO))
        income = is_income_type(cat.get("type"))
        variance = cents(a - b) if income else cents(b - a)   # + = good
        out.append({"category_id": cid, "name": cat.get("name", "Uncategorized"),
                    "income": income, "budget": b, "actual": a, "variance": variance,
                    "pct": (a / b * 100) if b else None})
    out.sort(key=lambda x: (not x["income"], x["name"].lower()))
    return out
