"""
Pure bookkeeping math shared by reports, the dashboard and the business pages.
No DB/UI imports — callers pass in plain dicts as returned by sheets.client.

FinanceBook is single-entry: every transaction lives in one account and carries
a category. The Balance Sheet is derived from account balances, and the Equity
section is decomposed so that Assets − Liabilities = Equity always holds.

Sign conventions (same as the rest of the app):
  * asset accounts: positive balance = money you have
  * liability accounts: negative balance = money owed
  * equity accounts (business books): negative balance = owner's equity credit,
    e.g. the "Capital Contributions" account goes to −500 when the owner pays a
    $500 company expense out of pocket as an investment. "Owner's Draws" goes
    positive as money is taken out.
"""
from datetime import date
from decimal import Decimal

from utils.money import D, cents, ZERO

LIABILITY_TYPES = ("credit", "credit card", "loan", "liability")
EQUITY_TYPES = ("equity",)

# Category types that never appear on the P&L. "debt_repayment" moves principal,
# "asset" capitalises a fixed-asset purchase (expensed later as depreciation),
# "inventory" buys stock (expensed later as Cost of Goods Sold).
NON_PNL_CATEGORY_TYPES = ("debt_repayment", "asset", "inventory")

# Built-in account roles for business books.
ROLE_OWNER_CAPITAL = "owner_capital"
ROLE_OWNER_DRAWS = "owner_draws"
ROLE_DUE_TO_OWNER = "due_to_owner"
ROLE_PETTY_CASH = "petty_cash"
ROLE_OWNER_PAID = "owner_paid"          # extra owner funding source (card/cash)
ROLE_REIMB_PAYABLE = "reimb_payable"     # owed to employees who paid

OWNER_FUNDING_ROLES = (ROLE_OWNER_CAPITAL, ROLE_DUE_TO_OWNER, ROLE_OWNER_PAID)


def account_class(acct: dict) -> str:
    """'asset', 'liability' or 'equity' for an account dict."""
    t = (acct.get("type") or "").lower()
    if t in EQUITY_TYPES:
        return "equity"
    if t in LIABILITY_TYPES:
        return "liability"
    return "asset"


def pnl_bucket(txn: dict, cats: dict) -> str:
    """
    Where a non-transfer transaction lands: 'income', 'expense' or 'excluded'.
    Mirrors the P&L report: uncategorized money-in counts as income,
    uncategorized money-out as expense.
    """
    cat = cats.get(txn.get("category_id", "") or "", {}) or {}
    ctype = cat.get("type", "expense") if cat else ""
    if ctype in NON_PNL_CATEGORY_TYPES:
        return "excluded"
    if ctype == "income":
        return "income"
    if not cat and D(txn.get("amount")) > 0:
        return "income"
    return "expense"


def _is_transfer(t: dict) -> bool:
    return str(t.get("is_transfer", "0")) == "1"


def net_income(txns, cats: dict, start: str = "", end: str = "",
               noncash: Decimal = ZERO) -> Decimal:
    """Cash-basis net income for [start, end] (ISO dates, inclusive, '' = open)."""
    total = ZERO
    for t in txns:
        if _is_transfer(t):
            continue
        d = t.get("date", "") or ""
        if start and d < start:
            continue
        if end and d > end:
            continue
        if pnl_bucket(t, cats) == "excluded":
            continue
        total += D(t.get("amount"))
    return cents(total + D(noncash))


def account_balances(accounts, txns, as_of: str = "") -> dict:
    """{account_id: Decimal balance} including the opening balance."""
    bal = {a["id"]: D(a.get("opening_balance")) for a in accounts}
    for t in txns:
        aid = t.get("account_id", "")
        if aid not in bal:
            continue
        if as_of and (t.get("date", "") or "") > as_of:
            continue
        bal[aid] += D(t.get("amount"))
    return {k: cents(v) for k, v in bal.items()}


def balance_sheet(accounts, txns, cats: dict, as_of: str, fy_start: str = "",
                  extra_assets=(), extra_liabilities=(),
                  noncash_pnl_before_fy: Decimal = ZERO,
                  noncash_pnl_in_fy: Decimal = ZERO, extra_equity=()) -> dict:
    """
    Build a balancing Balance Sheet as of `as_of`.

    accounts            every account (active or not — an inactive account with
                        a balance still belongs on the balance sheet)
    txns                every transaction up to at least as_of
    cats                {category_id: category dict}
    fy_start            first day of the current fiscal year (default Jan 1 of
                        as_of's year); income before it is Retained Earnings
    extra_assets        [(label, Decimal)] computed assets that are not accounts
                        (fixed assets at net book value, inventory, …)
    extra_liabilities   [(label, Decimal positive = owed)] computed liabilities
    noncash_pnl_*       non-cash P&L amounts (e.g. −depreciation) that pair with
                        the extra assets so the sheet still balances
    extra_equity        [(label, Decimal)] equity that came with a computed asset
                        but no transaction (e.g. inventory you already had)

    Returns a dict with assets / liabilities / equity line lists (label, amount,
    meta) and totals. Liabilities and equity are presented as positive amounts
    owed / owned, the way an accountant reads a balance sheet.

    Equity lines:
      Opening balance equity  = Σ opening balances of every account
      Owner contributions     = −(balances of capital-type equity accounts,
                                  excluding their opening balance)
      Owner draws             = −(balance of draw accounts, excl. opening)
      Retained earnings       = net income before fy_start
      Net income (this year)  = net income fy_start..as_of
      Other adjustments       = whatever is left, which is exactly: money spent
                                in non-P&L categories that isn't offset by a
                                computed asset (e.g. loan principal paid without
                                a matching liability entry) plus one-sided
                                transfers. Shown so the sheet always balances
                                and the user can see why.
    """
    if not fy_start:
        fy_start = f"{as_of[:4]}-01-01"
    txns = [t for t in txns if (t.get("date", "") or "") <= as_of]
    bal = account_balances(accounts, txns)

    assets, liabilities, equity = [], [], []
    opening_total = ZERO
    contributions = ZERO
    draws = ZERO
    other_equity = []
    for a in accounts:
        aid = a["id"]
        b = bal.get(aid, ZERO)
        opening = D(a.get("opening_balance"))
        opening_total += opening
        cls = account_class(a)
        inactive = str(a.get("active", "1")) == "0"
        if inactive and b == 0:
            continue
        label = a.get("name", "") + (" (inactive)" if inactive else "")
        if cls == "asset":
            assets.append((label, b, {"account_id": aid}))
        elif cls == "liability":
            liabilities.append((label, -b, {"account_id": aid}))
        else:
            moved = b - opening          # activity since tracking began
            role = a.get("role", "") or ""
            if role == ROLE_OWNER_DRAWS:
                draws += moved
            elif role in (ROLE_OWNER_CAPITAL, ROLE_OWNER_PAID) or not role:
                contributions += -moved
            else:
                other_equity.append((label, -moved, {"account_id": aid}))

    for label, amt in extra_assets:
        assets.append((label, cents(amt), {"computed": True}))
    for label, amt in extra_liabilities:
        liabilities.append((label, cents(amt), {"computed": True}))

    total_assets = cents(sum((x[1] for x in assets), ZERO))
    total_liab = cents(sum((x[1] for x in liabilities), ZERO))
    total_equity = cents(total_assets - total_liab)

    day_before = _day_before(fy_start)
    retained = net_income(txns, cats, end=day_before, noncash=noncash_pnl_before_fy)
    current = net_income(txns, cats, start=fy_start, end=as_of, noncash=noncash_pnl_in_fy)

    equity.append(("Opening balance equity", cents(opening_total), {}))
    equity.append(("Owner contributions", cents(contributions), {}))
    equity.append(("Owner draws", cents(-draws), {}))
    equity.extend(other_equity)
    for label, amt in extra_equity:
        equity.append((label, cents(amt), {"computed": True}))
    equity.append(("Retained earnings (prior years)", retained, {}))
    equity.append(("Net income (this year)", current, {}))
    explained = cents(sum((x[1] for x in equity), ZERO))
    other = cents(total_equity - explained)
    if other != 0:
        equity.append(("Other adjustments", other, {"plug": True}))

    return {
        "as_of": as_of,
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_assets": total_assets,
        "total_liabilities": total_liab,
        "total_equity": total_equity,
        "other_adjustments": other,
        "balanced": cents(total_assets - total_liab - total_equity) == 0,
    }


def unexplained_breakdown(txns, cats: dict, as_of: str) -> dict:
    """
    The two things that make up "Other adjustments" when no computed assets are
    involved: non-P&L category spending (debt principal etc.) and transfers
    whose other half is missing. Used by tests and the BS tooltip.
    """
    non_pnl = ZERO
    transfers = ZERO
    for t in txns:
        if (t.get("date", "") or "") > as_of:
            continue
        if _is_transfer(t):
            transfers += D(t.get("amount"))
        elif pnl_bucket(t, cats) == "excluded":
            non_pnl += D(t.get("amount"))
    return {"non_pnl": cents(non_pnl), "unmatched_transfers": cents(transfers)}


def _day_before(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return ""
    return date.fromordinal(d.toordinal() - 1).isoformat()


# ── Owner investment ─────────────────────────────────────────────────────────

def owner_investment(accounts, txns, start: str = "", end: str = "",
                     owner_id: str = "") -> dict:
    """
    Summarise the owner's money in the business for [start, end].

      contributed      capital put in (owner-paid expenses booked as capital,
                       plus cash deposited as capital)
      lent             company expenses the owner paid that the company owes
                       back (Due to Owner increases)
      reimbursed       money the company paid back to the owner
      drawn            money the owner took out (draws)
      owed_now         Due to Owner balance at `end` (not range-limited)
      net_investment   contributed − drawn + owed_now (lifetime, at `end`)

    `items` lists every owner-funded transaction in the range for drill-down.
    """
    by_id = {a["id"]: a for a in accounts}
    roles = {a["id"]: (a.get("role") or "") for a in accounts}

    def in_range(t):
        d = t.get("date", "") or ""
        return (not start or d >= start) and (not end or d <= end)

    contributed = lent = reimbursed = drawn = ZERO
    life_contrib = life_drawn = owed_now = ZERO
    items = []
    for a in accounts:
        if roles[a["id"]] == ROLE_DUE_TO_OWNER or (
                roles[a["id"]] == ROLE_OWNER_PAID and account_class(a) == "liability"):
            owed_now += -D(a.get("opening_balance"))
    for t in txns:
        aid = t.get("account_id", "")
        role = roles.get(aid, "")
        acct = by_id.get(aid, {})
        amt = D(t.get("amount"))
        d = t.get("date", "") or ""
        if end and d > end:
            continue
        cls = account_class(acct) if acct else ""
        kind = ""
        if role == ROLE_OWNER_DRAWS:
            life_drawn += amt
            if in_range(t):
                drawn += amt
                kind = "Owner draw"
        elif role == ROLE_DUE_TO_OWNER or (role == ROLE_OWNER_PAID and cls == "liability"):
            owed_now += -amt
            if in_range(t):
                if amt < 0:
                    lent += -amt
                    kind = "Company owes owner"
                else:
                    reimbursed += amt
                    kind = "Reimbursed to owner"
        elif role in (ROLE_OWNER_CAPITAL, ROLE_OWNER_PAID):
            life_contrib += -amt
            if in_range(t):
                contributed += -amt
                kind = "Capital contribution"
        elif owner_id and t.get("paid_by") == owner_id and in_range(t):
            kind = "Paid by owner"
        if kind:
            items.append(dict(t, owner_kind=kind))
    items.sort(key=lambda t: (t.get("date", ""), t.get("created_at", "")))
    return {
        "contributed": cents(contributed),
        "lent": cents(lent),
        "reimbursed": cents(reimbursed),
        "drawn": cents(drawn),
        "owed_now": cents(owed_now),
        "net_investment": cents(life_contrib - life_drawn + owed_now),
        "items": items,
    }
