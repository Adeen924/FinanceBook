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


def _name_key(d: dict):
    return (d.get("name") or "").lower()


def _tree(items, nests) -> list:
    """[(item, depth, displayed parent id, root item), …] in display order:
    each root sorted by name, followed by its descendants (any depth). An item
    nests under its parent_id only if that parent is in `items` and
    nests(parent, child) agrees; anything else — a missing parent, or a cycle
    nothing reaches from a root — is shown at the top level. Every item
    appears exactly once."""
    by_id = {i["id"]: i for i in items}
    children: dict = {}
    roots = []
    for it in items:
        pid = it.get("parent_id") or ""
        if pid and pid != it["id"] and pid in by_id and nests(by_id[pid], it):
            children.setdefault(pid, []).append(it)
        else:
            roots.append(it)
    out, seen = [], set()

    def walk(node, depth, parent_id, root):
        if node["id"] in seen:
            return
        seen.add(node["id"])
        out.append((node, depth, parent_id, root))
        for ch in sorted(children.get(node["id"], []), key=_name_key):
            walk(ch, depth + 1, node["id"], root)

    for r in sorted(roots, key=_name_key):
        walk(r, 0, "", r)
    for it in sorted(items, key=_name_key):          # cycles: not reachable from a root
        walk(it, 0, "", it)
    return out


def chart_rows(accounts, categories) -> list:
    """
    Ordered rows for the list: [(group, [entry, …]), …] with empty groups left
    out. An entry is {"kind": "account"|"category", "data": dict, "depth": int,
    "parent": displayed parent id or ""}. Each top-level row is followed by its
    indented sub-accounts. Sub-accounts are listed in their top-level account's
    group (a balance-sheet sub-account only nests under a parent in its own
    group). Nothing is ever left out: a row whose parent is missing, inactive
    or part of a cycle is shown at the top level.
    """
    by_group = {g.key: [] for g in GROUPS}
    same_group = lambda p, c: group_for_account(p).key == group_for_account(c).key
    for a, depth, pid, _root in _tree(accounts, same_group):
        by_group[group_for_account(a).key].append(
            {"kind": "account", "data": a, "depth": depth, "parent": pid})
    for c, depth, pid, root in _tree(categories, lambda p, ch: True):
        by_group[group_for_category(root).key].append(
            {"kind": "category", "data": c, "depth": depth, "parent": pid})
    return [(g, by_group[g.key]) for g in GROUPS if by_group[g.key]]


def picker_entries(categories, accounts, exclude_account_id: str = "") -> list:
    """
    [(group label, [(text, value), …]), …] for the unified Account picker:
    categories (roots as "Name", subs as "Parent → Child") and balance-sheet
    accounts (as "Name · Group"), excluding the transaction's own account.
    """
    out = []
    for g, entries in chart_rows([a for a in accounts if a.get("id") != exclude_account_id],
                                 categories):
        opts = []
        path: list = []                 # names of the displayed ancestors
        for e in entries:
            d = e["data"]
            del path[e["depth"]:]
            text = " → ".join(path + [d.get("name", "")])
            path.append(d.get("name", ""))
            if e["kind"] == "account":
                opts.append((f"{text} · {g.label}", ACCOUNT_PREFIX + d["id"]))
            else:
                opts.append((text, d["id"]))
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
