"""
The unified "Accounts" list (QuickBooks-style Chart of Accounts) — Qt-free.

FinanceBook keeps two tables: `accounts` (balance-sheet accounts: bank, cards,
loans, equity…) and `categories` (income / expense and the other P&L or
capitalising types). The UI shows them as one list grouped by type; this module
decides the groups, their order, which table each "New Account" type saves to,
and what the unified "Account" picker on a transaction offers.

Picker values: a category is its id; a balance-sheet account is ACCOUNT_PREFIX
+ its id (choosing one makes the transaction a transfer).
"""
from typing import NamedTuple

ACCOUNT_PREFIX = "acct:"


class Group(NamedTuple):
    key: str
    label: str
    kind: str               # "account" (accounts table) or "category"
    types: tuple            # stored type values that belong to this group
    new_type: str           # type saved when "New Account" picks this group


GROUPS = (
    Group("bank", "Bank", "account", ("checking", "savings", "cash"), "checking"),
    Group("credit_card", "Credit Card", "account", ("credit card", "credit"), "credit card"),
    Group("gift_card", "Gift Card", "account", ("gift card",), "gift card"),
    Group("investment", "Investment", "account", ("investment",), "investment"),
    Group("fixed_assets", "Fixed Assets", "account", ("fixed assets",), "fixed assets"),
    Group("other_assets", "Other Assets", "account", ("other assets", "other"), "other assets"),
    Group("liability", "Loan / Liability", "account", ("loan", "liability"), "loan"),
    Group("equity", "Equity", "account", ("equity",), "equity"),
    Group("income", "Income", "category", ("income",), "income"),
    Group("other_income", "Other Income", "category", ("other_income",), "other_income"),
    Group("expense", "Expense", "category", ("expense",), "expense"),
    Group("other_expense", "Other Expense", "category", ("other_expense",), "other_expense"),
    Group("debt_repayment", "Debt Repayment", "category", ("debt_repayment",), "debt_repayment"),
    Group("asset", "Asset Purchase", "category", ("asset",), "asset"),
    Group("inventory", "Inventory Purchase", "category", ("inventory",), "inventory"),
)
GROUPS_BY_KEY = {g.key: g for g in GROUPS}
_ACCOUNT_TYPE_GROUP = {t: g for g in GROUPS if g.kind == "account" for t in g.types}
_CATEGORY_TYPE_GROUP = {t: g for g in GROUPS if g.kind == "category" for t in g.types}

# "New Account" type choices: (label, group key). Bank offers its three types.
NEW_ACCOUNT_CHOICES = (
    ("Bank — Checking", "bank:checking"),
    ("Bank — Savings", "bank:savings"),
    ("Bank — Cash", "bank:cash"),
    ("Credit Card", "credit_card"),
    ("Gift Card", "gift_card"),
    ("Investment", "investment"),
    ("Fixed Assets", "fixed_assets"),
    ("Other Assets", "other_assets"),
    ("Loan", "liability:loan"),
    ("Other Liability", "liability:liability"),
    ("Equity", "equity"),
    ("Income", "income"),
    ("Other Income", "other_income"),
    ("Expense", "expense"),
    ("Other Expense", "other_expense"),
    ("Debt Repayment", "debt_repayment"),
    ("Asset Purchase (Balance Sheet)", "asset"),
    ("Inventory Purchase (Balance Sheet)", "inventory"),
)


def resolve_choice(choice: str) -> tuple:
    """('account'|'category', stored type) for a NEW_ACCOUNT_CHOICES value."""
    key, _, sub = choice.partition(":")
    g = GROUPS_BY_KEY[key]
    return g.kind, (sub or g.new_type)


def group_for_account(acct: dict) -> Group:
    """Group of a balance-sheet account. Unknown free-text types fall under
    Other Assets (the ledger treats them as assets too)."""
    return _ACCOUNT_TYPE_GROUP.get((acct.get("type") or "").lower(),
                                   GROUPS_BY_KEY["other_assets"])


def group_for_category(cat: dict) -> Group:
    return _CATEGORY_TYPE_GROUP.get(cat.get("type") or "expense", GROUPS_BY_KEY["expense"])


def chart_rows(accounts, categories) -> list:
    """
    Ordered rows for the list: [(group, [entry, …]), …] with empty groups left
    out. An entry is {"kind": "account"|"category", "data": dict, "depth": 0|1}.
    Accounts are sorted by name; categories keep parent → indented children
    (a child whose parent is missing is shown at the top level).
    """
    by_group = {g.key: [] for g in GROUPS}
    for a in sorted(accounts, key=lambda a: (a.get("name") or "").lower()):
        by_group[group_for_account(a).key].append(
            {"kind": "account", "data": a, "depth": 0})
    ids = {c["id"] for c in categories}
    roots = [c for c in categories if not c.get("parent_id") or c["parent_id"] not in ids]
    for root in sorted(roots, key=lambda c: (c.get("name") or "").lower()):
        g = by_group[group_for_category(root).key]
        g.append({"kind": "category", "data": root, "depth": 0})
        for sub in sorted((c for c in categories if c.get("parent_id") == root["id"]),
                          key=lambda c: (c.get("name") or "").lower()):
            g.append({"kind": "category", "data": sub, "depth": 1})
    return [(g, by_group[g.key]) for g in GROUPS if by_group[g.key]]


def picker_entries(categories, accounts, exclude_account_id: str = "") -> list:
    """
    [(group label, [(text, value), …]), …] for the unified Account picker:
    categories (roots as "Name", subs as "Parent → Child") and balance-sheet
    accounts (as "Name · Group"), excluding the transaction's own account.
    """
    out = []
    names = {c["id"]: c.get("name", "") for c in categories}
    for g, entries in chart_rows([a for a in accounts if a.get("id") != exclude_account_id],
                                 categories):
        opts = []
        for e in entries:
            d = e["data"]
            if e["kind"] == "account":
                opts.append((f"{d.get('name', '')} · {g.label}", ACCOUNT_PREFIX + d["id"]))
            elif e["depth"]:
                opts.append((f"{names.get(d.get('parent_id'), '')} → {d.get('name', '')}", d["id"]))
            else:
                opts.append((d.get("name", ""), d["id"]))
        out.append((g.label, opts))
    # Balance-sheet groups first, matching the list, is fine — but a transaction
    # is most often categorized, so put P&L groups first in the picker.
    cat_first = [x for x in out if GROUPS_BY_LABEL[x[0]].kind == "category"]
    return cat_first + [x for x in out if GROUPS_BY_LABEL[x[0]].kind == "account"]


GROUPS_BY_LABEL = {g.label: g for g in GROUPS}


def split_value(value: str) -> tuple:
    """('account', id) | ('category', id) | ('', '') for a picker value."""
    value = value or ""
    if not value:
        return "", ""
    if value.startswith(ACCOUNT_PREFIX):
        return "account", value[len(ACCOUNT_PREFIX):]
    return "category", value


def account_value(account_id: str) -> str:
    return ACCOUNT_PREFIX + account_id if account_id else ""
