"""
parsers/iif.py — QuickBooks IIF (Intuit Interchange Format) parser.

IIF is a tab-separated text format exported from QuickBooks Desktop.
Only BANK and CCARD account types become FinanceBook accounts.
INC and EXP types become FinanceBook categories.
Everything else (EQUITY, FIXASSET, LTLIAB, OCLIAB, etc.) is skipped —
those are balance-sheet items that don't map to transactions in this app.

How to export from QuickBooks Desktop:
    File → Utilities → Export → Lists to IIF Files → Chart of Accounts
"""

import hashlib
from datetime import datetime

# QB types → FinanceBook account types
# These all represent places that actually hold or owe money.
_QB_ACCT_TYPE = {
    "BANK":     "checking",     # bank accounts
    "CCARD":    "credit card",  # credit cards
    "AR":       "other",        # accounts receivable
    "AP":       "other",        # accounts payable
    "OCASSET":  "other",        # other current assets (cash, prepaid, deposits)
    "FIXASSET": "investment",   # fixed assets (property, equipment, investments)
    "OCLIAB":   "loan",         # other current liabilities (short-term loans)
    "LTLIAB":   "loan",         # long-term liabilities (mortgages, notes payable)
}

# QB types that become FinanceBook income/expense categories
_QB_INCOME_TYPES  = {"INC"}
_QB_EXPENSE_TYPES = {"EXP", "EXEXP"}

# EQUITY and NONPOSTING are skipped — owner's equity / retained earnings
# don't map cleanly to accounts or categories in a personal finance context.


def parse_iif(file_bytes: bytes) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """
    Parse a QuickBooks IIF export file.

    Returns:
        accounts     — BANK/CCARD dicts ready for db.save_account()
        categories   — INC/EXP dicts with 'type', 'name', 'parent_name'
                       (short display name) and 'qb_full_name' (full QB path).
                       Parent categories always appear before their children.
        transactions — transaction dicts; each has '_account_name' string —
                       caller resolves to account_id after creating accounts.
        warnings     — non-fatal issues
    """
    try:
        text = file_bytes.decode("windows-1252")
    except Exception:
        text = file_bytes.decode("utf-8", errors="replace")

    accounts: dict[str, dict]   = {}   # qb_full_name → account dict
    categories: dict[str, dict] = {}   # qb_full_name → category dict
    transactions: list[dict]    = []
    warnings: list[str]         = []

    accnt_headers: list[str]  = []
    trns_headers:  list[str]  = []
    current_trns:  dict | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue

        parts       = line.split("\t")
        record_type = parts[0].strip().upper()
        values      = parts[1:]

        # ── Section header rows ────────────────────────────────────────────
        if record_type == "!ACCNT":
            accnt_headers = [p.strip().upper() for p in values]
            continue
        if record_type == "!TRNS":
            trns_headers = [p.strip().upper() for p in values]
            continue
        if record_type in ("!SPL", "!ENDTRNS"):
            continue
        if record_type.startswith("!"):
            continue

        # ── Account / category records ─────────────────────────────────────
        if record_type == "ACCNT" and accnt_headers:
            row      = dict(zip(accnt_headers, [_clean(v) for v in values]))
            fullname = row.get("NAME", "").strip()
            if not fullname:
                continue
            qb_type  = row.get("ACCNTTYPE", "").strip().upper()
            ob_raw   = row.get("OBAMOUNT", "0").replace(",", "").strip() or "0"
            try:
                ob = float(ob_raw)
            except ValueError:
                ob = 0.0

            if qb_type in _QB_ACCT_TYPE:
                if fullname not in accounts:
                    accounts[fullname] = {
                        "name":            fullname,
                        "type":            _QB_ACCT_TYPE[qb_type],
                        "institution":     "",
                        "opening_balance": str(ob),
                        "currency":        "USD",
                    }

            elif qb_type in _QB_INCOME_TYPES | _QB_EXPENSE_TYPES:
                if fullname not in categories:
                    cat_type = "income" if qb_type in _QB_INCOME_TYPES else "expense"
                    # QB uses "Parent:Child" naming — split on first colon
                    if ":" in fullname:
                        parent_full, short_name = fullname.split(":", 1)
                        parent_full = parent_full.strip()
                        short_name  = short_name.strip()
                    else:
                        parent_full = ""
                        short_name  = fullname
                    categories[fullname] = {
                        "name":         short_name,
                        "type":         cat_type,
                        "parent_name":  parent_full,   # full QB name of parent
                        "qb_full_name": fullname,
                    }
            # All other QB types (EQUITY, FIXASSET, LTLIAB, etc.) are skipped.
            continue

        # ── Transaction main-line ──────────────────────────────────────────
        if record_type == "TRNS" and trns_headers:
            if current_trns:
                transactions.append(current_trns)

            row          = dict(zip(trns_headers, [_clean(v) for v in values]))
            date_str     = _parse_qb_date(row.get("DATE", ""))
            account_name = row.get("ACCNT", "").strip()
            payee        = row.get("NAME",  "").strip()
            memo         = row.get("MEMO",  "").strip()
            amount_raw   = row.get("AMOUNT", "0").replace(",", "").strip()
            try:
                amount = float(amount_raw)
            except ValueError:
                amount = 0.0
                warnings.append(f"Could not parse amount '{amount_raw}' — set to 0")

            current_trns = {
                "_account_name": account_name,
                "date":          date_str,
                "payee":         payee,
                "memo":          memo,
                "amount":        str(amount),
                "category_id":   "",
                "class_id":      "",
                "is_transfer":   "0",
                "reconciled":    "0",
                "notes":         "",
                "import_hash":   _make_hash(date_str, account_name, amount_raw, payee),
            }
            continue

        if record_type == "ENDTRNS":
            if current_trns:
                transactions.append(current_trns)
                current_trns = None
            continue

    if current_trns:
        transactions.append(current_trns)

    # ── Order categories: parents before children ──────────────────────────
    ordered_cats = _order_categories(list(categories.values()))

    return list(accounts.values()), ordered_cats, transactions, warnings


def _order_categories(cats: list[dict]) -> list[dict]:
    """Return categories with parents guaranteed to come before their children."""
    parents  = [c for c in cats if not c["parent_name"]]
    children = [c for c in cats if c["parent_name"]]
    return parents + children


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """Strip surrounding double-quotes QB adds to values containing commas."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def _parse_qb_date(s: str) -> str:
    """Convert QuickBooks date strings to YYYY-MM-DD."""
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _make_hash(date: str, account: str, amount: str, payee: str) -> str:
    key = f"{date}|{account}|{amount}|{payee}"
    return hashlib.md5(key.encode()).hexdigest()[:16]
